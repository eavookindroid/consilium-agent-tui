"""
Consilium Agent - Message Courier

Handles message routing, delivery, @mentions prioritization,
and @@secret message filtering.

Copyright (c) 2025 Artel Team
Licensed under Artel Team Non-Commercial License
"""

import json
import re
import logging
from dataclasses import dataclass
from collections import deque
from typing import Any, Callable, Awaitable
from datetime import datetime

from .constants import (
    STATUS_PROCESSING,
    STATUS_ERROR_PROCESSING,
    STATUS_STAYED_SILENT,
    STATUS_EMPTY_RESPONSE,
    CHAT_HEADER_TEMPLATE
)

@dataclass(slots=True)
class CourierMessage:
    author: str
    text: str
    is_error: bool = False
    is_init: bool = False
    metadata: dict[str, Any] | None = None


@dataclass(slots=True)
class JournalEntry:
    id: int
    author: str
    text: str
    timestamp: datetime
    metadata: dict[str, Any] | None = None


@dataclass(slots=True)
class CourierHooks:
    add_status: Callable[[str], None]
    publish_entry: Callable[[JournalEntry, bool], None]
    is_shutting_down: Callable[[], bool]
    is_interrupt_requested: Callable[[], bool]
    wait_for_step_permission: Callable[[str], Awaitable[bool]]
    is_step_mode_enabled: Callable[[], bool]
    get_display_name: Callable[[str], str]
    is_silent_response_text: Callable[[str], bool]
    run_backend: Callable[[str, str, bool, bool], Awaitable[Any]]


class ConsiliumCourier:
    """Event courier that delivers journal entries to participants."""

    def __init__(
        self,
        agents: dict[str, dict[str, Any]],
        hooks: CourierHooks,
        logger: logging.Logger,
        *,
        last_message_id: int = 0,
    ) -> None:
        self._agents = agents
        self._hooks = hooks
        self._logger = logger
        self._journal: list[JournalEntry] = []
        self._next_journal_id: int = max(0, last_message_id) + 1
        self._last_seen: dict[str, int] = {}
        self._pending_agents: deque[str] = deque()
        self._pending_membership: set[str] = set()
        self._drain_in_progress = False
        if last_message_id:
            self._logger.debug("Courier initialized with last_message_id=%d", last_message_id)

    # ------------------------------------------------------------------ #
    # Public API
    # ------------------------------------------------------------------ #

    def enqueue_message(self, message: CourierMessage) -> JournalEntry:
        """Register a new message in the journal and schedule delivery."""
        entry = self._append_journal_entry(
            author=message.author,
            text=message.text,
            metadata={
                **(message.metadata or {}),
                'is_init': message.is_init,
                'is_error': message.is_error,
            },
        )

        if not message.is_init:
            self._hooks.publish_entry(entry, message.is_error)

        # Check if this is a secret reply from agent (before scheduling)
        is_secret = (entry.metadata or {}).get('status') == 'secret'
        if is_secret and message.author != 'User':
            # Secret reply from agent - deliver directly to User, bypass pending
            self._last_seen['User'] = entry.id
            self._logger.debug("Secret reply from %s delivered directly to User", message.author)
            self._debug_state(f"enqueue author={message.author} id={entry.id} (secret reply)")
            return entry

        # Extract mentions and private_to from metadata (only set for User messages)
        mentions = (message.metadata or {}).get('mentions', []) if message.author == 'User' else []
        private_to = (message.metadata or {}).get('private_to') if message.author == 'User' else None

        self._schedule_pending_for(
            message.author,
            include_user=not message.is_init,
            mentions=mentions,
            private_to=private_to
        )
        self._debug_state(f"enqueue author={message.author} id={entry.id}")
        return entry

    async def drain(self) -> None:
        """Process pending participants until the queue is empty or interrupted."""
        if self._drain_in_progress:
            return
        self._drain_in_progress = True
        self._debug_state("drain start")
        try:
            while True:
                if self._hooks.is_shutting_down() or self._hooks.is_interrupt_requested():
                    self._logger.debug("Courier drain aborted due to shutdown/interrupt")
                    break

                participant = self._pop_pending_agent()
                if participant is None:
                    break

                if participant == 'User':
                    self._logger.debug("Courier deliver -> User (UI refresh)")
                    self._deliver_to_user()
                    self._debug_state("post user delivery")
                    continue

                agent_config = self._agents.get(participant)
                if not agent_config or not agent_config.get('enabled', True):
                    self._purge_participant(participant)
                    continue

                context = self._build_context_for(participant)
                if not context:
                    self._logger.debug("Courier skip %s: no unseen messages", participant)
                    continue

                init_flag = any(entry.metadata.get('is_init') for entry in context)
                proceed = await self._handle_step_mode(participant, init_flag)
                if not proceed:
                    self._logger.debug("Step-mode denied delivery to %s", participant)
                    continue

                self._logger.debug(
                    "Courier deliver -> %s context_ids=%s init=%s",
                    participant,
                    [entry.id for entry in context],
                    init_flag,
                )
                await self._dispatch_agent(participant, agent_config, context, init_flag)
                self._debug_state(f"post delivery {participant}")
        finally:
            self._drain_in_progress = False
            self._debug_state("drain end")

    # ------------------------------------------------------------------ #
    # Journal helpers
    # ------------------------------------------------------------------ #

    def _append_journal_entry(
        self,
        *,
        author: str,
        text: str,
        metadata: dict[str, Any] | None,
    ) -> JournalEntry:
        entry = JournalEntry(
            id=self._next_journal_id,
            author=author,
            text=text,
            timestamp=datetime.now(),
            metadata=metadata or {},
        )
        entry.metadata.setdefault('msg_id', entry.id)
        self._journal.append(entry)
        self._next_journal_id += 1

        self._last_seen[author] = entry.id
        self._ensure_participant_registered(author)
        return entry

    def _ensure_participant_registered(self, participant: str) -> None:
        if participant not in self._last_seen:
            self._last_seen[participant] = 0

    def _iter_participants(self) -> list[str]:
        participants = ['User']
        participants.extend(
            name
            for name, config in self._agents.items()
            if config.get('enabled', True)
        )
        for participant in participants:
            self._ensure_participant_registered(participant)
        return participants

    def _schedule_pending_for(
        self,
        author: str,
        include_user: bool,
        mentions: list[str] = None,
        private_to: str | None = None
    ) -> None:
        # Private delivery: ONLY to specified agent
        if private_to:
            self._enqueue_pending_agent(private_to)
            if include_user:
                self._enqueue_pending_agent('User')
            self._prioritize_agent(private_to)
            self._logger.debug("Private delivery to: %s", private_to)
            self._debug_state(f"schedule_after author={author} (private)")
            return  # Early exit!

        # Regular delivery: all participants
        for participant in self._iter_participants():
            if participant == author:
                continue
            if not include_user and participant == 'User':
                continue
            self._enqueue_pending_agent(participant)

        # Prioritize mentioned agents (only from User messages)
        if mentions:
            for mentioned_agent in reversed(mentions):  # reversed to preserve mention order
                self._prioritize_agent(mentioned_agent)
            self._logger.debug("Prioritized mentions: %s", mentions)

        self._debug_state(f"schedule_after author={author}")

    def _build_context_for(self, participant: str) -> list[JournalEntry]:
        last_seen_id = self._last_seen.get(participant, 0)
        context = []
        last_seen_candidate = last_seen_id

        for entry in self._journal:
            if entry.id <= last_seen_id:
                continue

            # Track last ID even if we skip the message
            last_seen_candidate = entry.id

            # Skip own messages
            if entry.author == participant:
                continue

            # Skip secret messages not intended for this participant
            # Exception: User sees ALL messages (both own secrets and secret replies)
            if entry.metadata and entry.metadata.get('status') == 'secret':
                if participant != 'User':  # User sees everything!
                    private_to = entry.metadata.get('private_to')
                    if private_to and private_to != participant:
                        self._logger.trace("Skipping secret message %d (not for %s)", entry.id, participant)
                        continue

            context.append(entry)

        # Update last_seen even if context is empty (due to filtering)
        if last_seen_candidate > last_seen_id:
            self._last_seen[participant] = last_seen_candidate
            self._logger.trace("Updated %s last_seen=%d", participant, last_seen_candidate)

        # TODO(FR-001): enforce context window limits (count/size) when courier looping is extended.
        return context

    # ------------------------------------------------------------------ #
    # Pending queue management
    # ------------------------------------------------------------------ #

    def _enqueue_pending_agent(self, participant: str) -> None:
        if participant in self._pending_membership:
            return
        self._pending_agents.append(participant)
        self._pending_membership.add(participant)

    def _prioritize_agent(self, participant: str) -> None:
        """Move participant to the front of pending queue."""
        if participant not in self._pending_membership:
            return  # Not in queue

        # Remove from current position
        try:
            self._pending_agents.remove(participant)
        except ValueError:
            return

        # Insert at the front
        self._pending_agents.appendleft(participant)
        self._logger.debug("Moved %s to front of queue", participant)

    def _pop_pending_agent(self) -> str | None:
        if not self._pending_agents:
            return None
        participant = self._pending_agents.popleft()
        self._pending_membership.discard(participant)
        return participant

    def _purge_participant(self, participant: str) -> None:
        self._pending_membership.discard(participant)
        try:
            while True:
                self._pending_agents.remove(participant)
        except ValueError:
            pass
        self._debug_state(f"purge {participant}")

    def mark_participant_disabled(self, participant: str) -> None:
        """Public API: remove participant from routing state."""
        if participant == 'User':
            return
        self._purge_participant(participant)

    def mark_participant_enabled(self, participant: str) -> None:
        """Public API: ensure participant state is registered after enabling."""
        if participant == 'User':
            return
        self._ensure_participant_registered(participant)
        self._purge_participant(participant)
        latest_id = self._journal[-1].id if self._journal else 0
        if self._last_seen.get(participant, 0) < latest_id:
            self._enqueue_pending_agent(participant)
        self._debug_state(f"enabled {participant}")

    def _deliver_to_user(self) -> None:
        if not self._journal:
            return
        latest_id = self._journal[-1].id
        self._last_seen['User'] = latest_id
        self._logger.debug("Courier synced User last_seen=%d", latest_id)

    # ------------------------------------------------------------------ #
    # Dispatch helpers
    # ------------------------------------------------------------------ #

    async def _handle_step_mode(self, agent_name: str, init_flag: bool) -> bool:
        if not self._hooks.is_step_mode_enabled() or init_flag:
            return True
        self._logger.debug("Step mode: waiting before dispatching to %s", agent_name)
        proceed = await self._hooks.wait_for_step_permission(agent_name)
        if not proceed:
            self._logger.debug("Step mode wait aborted for %s", agent_name)
        return proceed

    async def _dispatch_agent(
        self,
        agent_name: str,
        agent_config: dict[str, Any],
        context: list[JournalEntry],
        init_flag: bool,
    ) -> None:
        display_name = self._hooks.get_display_name(agent_name)
        formatted = self._format_context(context)

        self._hooks.add_status(STATUS_PROCESSING.format(display_name=display_name))
        self._logger.trace("%s thinking with context:\n%s", agent_name, formatted)
        try:
            response = await self._hooks.run_backend(
                agent_name,
                formatted,
                init_flag,
                True,
            )
        except Exception as handler_error:  # pragma: no cover - defensive
            self._logger.error(
                "[%s] Message processing error: %s", agent_name, handler_error,
                exc_info=True,
            )
            self._hooks.add_status(STATUS_ERROR_PROCESSING.format(display_name=display_name, handler_error=handler_error))
            return

        if not agent_config.get('enabled', True):
            self._logger.info("%s disabled during processing, discarding response", agent_name)
            return

        response_message = self._make_agent_courier_message(agent_name, response)
        if response_message is None:
            self._hooks.add_status(STATUS_STAYED_SILENT.format(display_name=display_name))
            return

        if response_message.text:
            self._logger.debug("Queued response from %s: %s", agent_name, response_message.text[:60])
            self.enqueue_message(response_message)
            self._debug_state(f"response queued from {agent_name}")
        else:
            self._hooks.add_status(STATUS_EMPTY_RESPONSE.format(display_name=display_name))

    def _format_context(self, context: list[JournalEntry]) -> str:
        lines: list[str] = []
        for entry in context:
            lines.extend(self._format_entry(entry))
            lines.append("")
        return "\n".join(lines).rstrip()

    def _make_agent_courier_message(self, agent_name: str, response: Any) -> CourierMessage | None:
        if response is None:
            return None

        if isinstance(response, dict):
            response_text = str(response.get('text', '')).strip()
            response_error = bool(response.get('error'))
        else:
            response_text = str(response).strip()
            response_error = False

        header_data, body_text = self._extract_response_header(response_text)
        response_metadata: dict[str, Any] = {}

        if header_data:
            reply_to = self._coerce_replyto(header_data.get('replyto'))
            if reply_to is not None:
                response_metadata['replyto'] = reply_to

                # Check if replying to a secret message
                if self._is_secret_message(reply_to):
                    response_metadata['status'] = 'secret'
                    response_metadata['private_to'] = 'User'
                    self._logger.debug("%s replied to secret message %s", agent_name, reply_to)

            targets = self._coerce_targets(header_data.get('to'))
            if targets:
                response_metadata['targets'] = targets

            response_metadata['header'] = header_data

        body_text = body_text.strip()

        if body_text and self._hooks.is_silent_response_text(body_text):
            return None

        if not body_text:
            return None

        return CourierMessage(
            author=agent_name,
            text=body_text,
            is_error=response_error,
            is_init=False,
            metadata=response_metadata or None,
        )

    def _extract_response_header(self, text: str) -> tuple[dict[str, Any] | None, str]:
        """Split agent response into JSON header and body."""
        if not text:
            return None, ""

        length = len(text)
        idx = 0

        while idx < length and text[idx].isspace():
            idx += 1

        prefix = text[:idx]
        fence_close_idx: int | None = None

        if text.startswith("```", idx):
            fence_end = text.find("\n", idx)
            if fence_end == -1:
                return None, text
            fence_tag = text[idx + 3:fence_end].strip().lower()
            if fence_tag in ("", "json", "application/json", "json5"):
                idx = fence_end + 1
                fence_close_idx = text.find("```", idx)
                if fence_close_idx == -1:
                    return None, text
            else:
                return None, text

        while idx < length and text[idx].isspace():
            idx += 1

        if idx >= length or text[idx] != '{':
            return None, text

        closing_index_relative = self._find_matching_brace(text[idx:])
        if closing_index_relative is None:
            self._logger.debug("Failed to locate JSON header terminator in response")
            return None, text

        closing_index = idx + closing_index_relative
        raw_header = text[idx:closing_index + 1]

        body_start = closing_index + 1
        if fence_close_idx is not None and body_start <= fence_close_idx:
            body_start = fence_close_idx + 3

        body = text[body_start:]
        if body:
            body = body.lstrip("\r\n")

        header = self._parse_json_header(raw_header)
        if header is None:
            self._logger.debug("Invalid JSON header in agent response: %s", raw_header)
            return None, text

        return header, prefix + body

    @staticmethod
    def _find_matching_brace(chunk: str) -> int | None:
        depth = 0
        in_string = False
        escape = False

        for index, char in enumerate(chunk):
            if in_string:
                if escape:
                    escape = False
                elif char == '\\':
                    escape = True
                elif char == '"':
                    in_string = False
                continue

            if char == '"':
                in_string = True
                continue

            if char == '{':
                depth += 1
            elif char == '}':
                depth -= 1
                if depth == 0:
                    return index
            elif char in ('[', ']'):
                # Include brackets in balance but do not search separately
                pass

        return None

    def _parse_json_header(self, raw: str) -> dict[str, Any] | None:
        cleaned = self._strip_trailing_commas(raw)
        try:
            data = json.loads(cleaned)
        except json.JSONDecodeError:
            return None
        if not isinstance(data, dict):
            return None
        return data

    @staticmethod
    def _strip_trailing_commas(payload: str) -> str:
        result: list[str] = []
        in_string = False
        escape = False

        for char in payload:
            result.append(char)

            if in_string:
                if escape:
                    escape = False
                elif char == '\\':
                    escape = True
                elif char == '"':
                    in_string = False
                continue

            if char == '"':
                in_string = True
                continue

            if char in ('}', ']'):
                idx = len(result) - 2
                while idx >= 0 and result[idx].isspace():
                    idx -= 1
                if idx >= 0 and result[idx] == ',':
                    del result[idx]

        return ''.join(result)

    @staticmethod
    def _coerce_replyto(value: Any) -> int | str | None:
        if value is None:
            return None
        if isinstance(value, int):
            return value
        if isinstance(value, str):
            trimmed = value.strip()
            if not trimmed:
                return None
            if trimmed.isdigit():
                try:
                    return int(trimmed)
                except ValueError:
                    return trimmed
            return trimmed
        return None

    def _coerce_targets(self, value: Any) -> list[str]:
        if value is None:
            return []
        if isinstance(value, str):
            return [value.strip()] if value.strip() else []
        if isinstance(value, (list, tuple)):
            targets: list[str] = []
            for item in value:
                if isinstance(item, str):
                    cleaned = item.strip()
                    if cleaned:
                        targets.append(cleaned)
            return targets
        return []

    def _is_secret_message(self, msg_id: int) -> bool:
        """
        Check if message with given ID is marked as secret.

        Args:
            msg_id: Journal entry ID

        Returns:
            True if message has metadata={'status': 'secret'}

        Implementation:
            O(n) linear search through journal
            (acceptable for typical journal size <1000 messages)
        """
        for entry in self._journal:
            if entry.id == msg_id:
                return entry.metadata.get('status') == 'secret' if entry.metadata else False
        return False

    # ------------------------------------------------------------------ #
    # Formatting helpers
    # ------------------------------------------------------------------ #

    def _format_entry(self, entry: JournalEntry) -> list[str]:
        author_display = self._display_name(entry.author)
        targets = self._resolve_targets(entry)
        to_line = ", ".join(targets) if targets else "all"
        header = CHAT_HEADER_TEMPLATE.format(
            id=entry.id,
            msd_id=entry.id,
            author=author_display,
            targets=to_line,
        ).rstrip()
        header_lines = header.splitlines()
        lower_header = [line.strip().lower() for line in header_lines]
        if not any(line.startswith("from:") for line in lower_header):
            header_lines.append(f"from: {author_display}")
        if not any(line.startswith("to:") for line in lower_header):
            header_lines.append(f"to: {to_line}")
        lines = header_lines + ["", entry.text]
        return lines

    def _display_name(self, participant: str) -> str:
        return self._hooks.get_display_name(participant)

    def _resolve_targets(self, entry: JournalEntry) -> list[str]:
        metadata_targets = entry.metadata.get('targets') if entry.metadata else None
        if metadata_targets:
            if isinstance(metadata_targets, str):
                return [metadata_targets]
            if isinstance(metadata_targets, (list, tuple)):
                collected = [str(target) for target in metadata_targets if str(target).strip()]
                if collected:
                    return collected

        alias_map = self._build_alias_map()
        mentions: set[str] = set()
        for match in re.finditer(r'@([^\s@]+)', entry.text):
            token = match.group(1)
            normalized = self._normalize_alias(token)
            if not normalized:
                continue
            participant = alias_map.get(normalized)
            if participant == "__all__":
                return []
            if participant:
                mentions.add(participant)

        if not mentions:
            return []

        ordered_participants = self._iter_participants()
        return [
            self._display_name(participant)
            for participant in ordered_participants
            if participant in mentions
        ]

    def _build_alias_map(self) -> dict[str, str]:
        alias_map: dict[str, str] = {
            "all": "__all__",
            "everyone": "__all__",
        }

        for participant in self._iter_participants():
            display = self._display_name(participant)
            aliases = {
                participant,
                display,
                display.replace(" ", ""),
            }
            for alias in aliases:
                normalized = self._normalize_alias(alias)
                if normalized:
                    alias_map[normalized] = participant
        return alias_map

    @staticmethod
    def _normalize_alias(value: str) -> str:
        return re.sub(r'[^0-9a-z]+', '', value.casefold())

    def _debug_state(self, note: str) -> None:
        if not self._logger.isEnabledFor(logging.DEBUG):
            return
        pending_snapshot = list(self._pending_agents)
        last_seen_snapshot = dict(sorted(self._last_seen.items()))
        journal_tail = [entry.id for entry in self._journal[-5:]]
        self._logger.debug(
            "Courier state [%s]: pending=%s membership=%s last_seen=%s journal_tail=%s next_id=%d",
            note,
            pending_snapshot,
            sorted(self._pending_membership),
            last_seen_snapshot,
            journal_tail,
            self._next_journal_id,
        )


__all__ = ['CourierMessage', 'JournalEntry', 'CourierHooks', 'ConsiliumCourier']
