"""
Consilium Agent - Session Manager

Handles workspace management, history persistence, and session storage.

Copyright (c) 2025 Artel Team
Licensed under Artel Team Non-Commercial License
"""

import json
import logging
import hashlib
import fcntl
import os
from pathlib import Path
from contextlib import contextmanager
from datetime import datetime
from typing import Iterable

from .constants import HISTORY_TAIL_LINES

class SessionManager:
    """Manages session persistence for workspaces and agents"""

    def __init__(self, workspace_path: Path):
        self.workspace_path = workspace_path.absolute()
        self.workspace_hash = self._compute_workspace_hash()
        self.config_dir = Path.home() / ".consilium"
        self.session_dir = self.config_dir / "workspaces" / self.workspace_hash
        self.agents_dir = self.session_dir / "agents"
        self.history_file = self.session_dir / "history.jsonl"
        self.session_file = self.session_dir / "session.json"
        self.logger = logging.getLogger('SessionManager')

        self.logger.trace("SessionManager:init workspace=%s", self.workspace_path)
        self._ensure_directories()

    def _compute_workspace_hash(self) -> str:
        """Compute hash of workspace path"""
        path_str = str(self.workspace_path)
        return hashlib.sha256(path_str.encode()).hexdigest()[:16]

    def _ensure_directories(self):
        """Create necessary directories"""
        self.session_dir.mkdir(parents=True, exist_ok=True)
        self.agents_dir.mkdir(exist_ok=True)
        self.logger.debug(f"Session directory: {self.session_dir}")

    @contextmanager
    def _locked_file(self, path: Path, mode: str, **kwargs):
        """Open file with an exclusive lock."""
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, mode, **kwargs) as handle:
            try:
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
                yield handle
            finally:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)

    def load_session_metadata(self) -> dict:
        """Load session metadata or create new"""
        if self.session_file.exists():
            try:
                self.logger.trace("SessionManager:loading session metadata")
                with self._locked_file(self.session_file, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                    self.logger.info(f"Loaded session metadata: {data.get('message_count', 0)} messages")
                    return data
            except Exception:
                self.logger.exception("Failed to load session metadata")

        # Create new session metadata
        data = {
            'workspace_path': str(self.workspace_path),
            'created_at': datetime.now().isoformat(),
            'last_accessed': datetime.now().isoformat(),
            'message_count': 0
        }
        self.save_session_metadata(data)
        self.logger.debug("SessionManager:new session metadata created")
        return data

    def save_session_metadata(self, data: dict):
        """Save session metadata"""
        data['last_accessed'] = datetime.now().isoformat()
        try:
            with self._locked_file(self.session_file, 'w', encoding='utf-8') as f:
                json.dump(data, f, indent=2)
            self.logger.trace("SessionManager:session metadata saved")
        except Exception:
            self.logger.exception("Failed to save session metadata")

    def load_agent_session(self, agent_id: str, aliases: Iterable[str] | None = None) -> dict:
        """Load agent session data or create new.

        Args:
            agent_id: Stable identifier of the agent (used for storage).
            aliases: Optional legacy names to search for existing session files.
        """

        def _candidate_path(name: str) -> Path:
            return self.agents_dir / f"{str(name).lower()}.json"

        target_file = _candidate_path(agent_id)
        search_order: list[str] = [agent_id]
        if aliases:
            for alias in aliases:
                alias_str = str(alias)
                if alias_str not in search_order:
                    search_order.append(alias_str)

        agent_file = target_file
        source_alias = agent_id

        for candidate_alias in search_order:
            candidate_file = _candidate_path(candidate_alias)
            if candidate_file.exists():
                agent_file = candidate_file
                source_alias = candidate_alias
                break

        if agent_file.exists():
            try:
                self.logger.trace("SessionManager:loading agent session %s", source_alias)
                with self._locked_file(agent_file, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                self.logger.info(f"Loaded {source_alias} session: {data.get('session_id', 'N/A')}")
                data.pop('system_prompt_override', None)
                data.pop('nickname', None)
                data.pop('command_path', None)

                if agent_file != target_file and not target_file.exists():
                    try:
                        agent_file.rename(target_file)
                        self.logger.debug(
                            "SessionManager:migrated agent session file %s -> %s",
                            agent_file.name,
                            target_file.name,
                        )
                    except OSError as rename_error:
                        self.logger.debug(
                            "SessionManager:failed to migrate agent session file %s -> %s (%s)",
                            agent_file.name,
                            target_file.name,
                            rename_error,
                        )
                return data
            except Exception:
                self.logger.exception(f"Failed to load {source_alias} session")

        data = {
            'session_id': None,
            'created_at': datetime.now().isoformat(),
            'last_message_at': None,
            'message_count': 0,
        }
        self.logger.debug(f"Created new session data for {agent_id}")
        self.logger.trace("SessionManager:new agent session defaults %s", agent_id)
        return data

    def save_agent_session(
        self,
        agent_id: str,
        session_id: str,
        message_count: int,
    ):
        """Save agent session data"""
        agent_file = self.agents_dir / f"{agent_id.lower()}.json"

        created_at = datetime.now().isoformat()
        if agent_file.exists():
            try:
                with self._locked_file(agent_file, 'r', encoding='utf-8') as existing_file:
                    existing_data = json.load(existing_file)
                created_at = existing_data.get('created_at', created_at)
            except Exception as load_error:
                self.logger.warning(
                    f"Failed to read existing session metadata for {agent_id}: {load_error}",
                    exc_info=True,
                )

        data = {
            'session_id': session_id,
            'created_at': created_at,
            'last_message_at': datetime.now().isoformat(),
            'message_count': message_count,
        }

        try:
            with self._locked_file(agent_file, 'w', encoding='utf-8') as f:
                json.dump(data, f, indent=2, ensure_ascii=False)
            self.logger.debug(f"Saved {agent_id} session: {session_id}")
        except Exception:
            self.logger.exception(f"Failed to save {agent_id} session")

    def save_agent_nickname(self, agent_id: str, nickname: str | None):
        """Deprecated: nicknames stored via agent registry overrides."""
        self.logger.debug("SessionManager:save_agent_nickname ignored for %s", agent_id)

    def save_agent_command_path(self, agent_id: str, command_path: str | None):
        """Deprecated: command paths stored via agent registry overrides."""
        self.logger.debug("SessionManager:save_agent_command_path ignored for %s", agent_id)

    def load_history(self) -> list:
        """Load chat history from JSONL file (always tail, limited to HISTORY_TAIL_LINES)"""
        if not self.history_file.exists():
            self.logger.trace("SessionManager:history file missing, starting empty")
            return []

        try:
            # Always load tail (last N messages) to match ChatLog max_lines limit
            raw_lines = self._read_history_tail(HISTORY_TAIL_LINES)

            history = []
            for raw in raw_lines:
                try:
                    history.append(json.loads(raw))
                except json.JSONDecodeError:
                    self.logger.exception("Corrupted history line skipped")

            self.logger.info(f"Loaded {len(history)} messages from history")
            return history
        except Exception:
            self.logger.exception("Failed to load history")
            return []

    def _read_history_tail(self, limit: int) -> list[str]:
        if limit <= 0:
            return []

        self.logger.trace("SessionManager:_read_history_tail limit=%d", limit)
        lines: list[str] = []
        buffer = bytearray()

        with self._locked_file(self.history_file, 'rb') as fb:
            fb.seek(0, os.SEEK_END)
            position = fb.tell()

            while position > 0 and len(lines) < limit:
                read_size = min(8192, position)
                position -= read_size
                fb.seek(position)
                chunk = fb.read(read_size)
                fb.seek(position)

                buffer[:0] = chunk

                while True:
                    newline_index = buffer.rfind(b"\n")
                    if newline_index == -1:
                        break
                    line = buffer[newline_index + 1:]
                    buffer = buffer[:newline_index]
                    if line.strip():
                        lines.append(line.decode('utf-8', errors='ignore'))
                        if len(lines) >= limit:
                            break

            if buffer and len(lines) < limit and buffer.strip():
                lines.append(buffer.decode('utf-8', errors='ignore'))

        lines.reverse()
        self.logger.trace("SessionManager:_read_history_tail collected=%d", len(lines))
        return lines

    def append_to_history(
        self,
        role: str,
        content: str,
        agent: str = None,
        display_name: str = None,
        msg_id: int | str | None = None,
        reply_to: int | str | None = None,
    ):
        """Append message to history (JSONL format)"""
        message = {
            'timestamp': datetime.now().isoformat(),
            'role': role,
            'content': content
        }

        if agent:
            message['agent'] = agent
        if display_name:
            message['display_name'] = display_name
        if msg_id is not None:
            message['msg_id'] = msg_id
        if reply_to is not None:
            message['reply_to'] = reply_to

        try:
            with self._locked_file(self.history_file, 'a', encoding='utf-8') as f:
                f.write(json.dumps(message, ensure_ascii=False) + '\n')
        except Exception:
            self.logger.exception("Failed to append to history")

    def get_last_message_id(self, tail_limit: int = 256) -> int:
        """Return the highest message id present in history.jsonl."""
        if not self.history_file.exists():
            return 0

        def _extract(lines: list[str]) -> int:
            for raw in reversed(lines):
                try:
                    payload = json.loads(raw)
                except json.JSONDecodeError:
                    continue
                msg_id = payload.get('msg_id')
                if isinstance(msg_id, int):
                    return msg_id
                if isinstance(msg_id, str):
                    stripped = msg_id.strip()
                    if stripped.isdigit():
                        return int(stripped)
            return 0

        try:
            lines = self._read_history_tail(tail_limit)
            last = _extract(lines)
            if last:
                return last
            if tail_limit < HISTORY_TAIL_LINES:
                lines = self._read_history_tail(HISTORY_TAIL_LINES)
                last = _extract(lines)
                if last:
                    return last
            return 0
        except Exception:
            self.logger.exception("Failed to determine last message id")
            return 0


# ============================================================================
# PROMPT EDITOR MODAL
# ============================================================================



__all__ = ['SessionManager']
