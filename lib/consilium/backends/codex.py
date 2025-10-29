"""Backend wrapper for Codex agents."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

from .base import AgentBackend
from ..constants import (
    IDENTITY_TEMPLATE,
    REPLY_CHAT_PROTOCOL_PROMPT,
    SYSTEM_PROMPT_PERIOD,
    STATUS_CODEX_COMPACT_FAILED,
    STATUS_CODEX_COMPACT_SUCCESS,
    STATUS_CONTEXT_OVERFLOW,
    STATUS_SESSION_RESET,
)

if TYPE_CHECKING:
    from ..app import ConsiliumAgentTUI


class CodexBackend(AgentBackend):
    class_id = "codex"
    display_name = "OpenAI Codex CLI"
    description = "Wrapper around existing Codex CLI integration."

    async def run(
        self,
        app: "ConsiliumAgentTUI",
        agent_name: str,
        message: str,
        *,
        is_init: bool = False,
        skip_log: bool = False,
    ) -> Any:
        logger = app.logger

        attempt = 0
        while True:
            if app._shutting_down:
                logger.debug(f"[{agent_name}] Shutdown in progress; skipping request")
                return None

            # Periodic system prompt inclusion with cyclic counter
            agent_entry = app.agents.get(agent_name, {})
            counter = agent_entry.get('prompt_counter', 0)

            # Get period from app settings (overrides constant)
            period = getattr(app, 'system_prompt_period', SYSTEM_PROMPT_PERIOD)

            # Determine if we should include system prompts this time
            include_system_prompts = False

            if is_init:
                # Init message always includes prompts, doesn't affect counter
                include_system_prompts = True
            elif period == 0:
                # "Once" mode - only init message has prompts
                include_system_prompts = False
            elif period == 1:
                # "Always" mode - every message has prompts
                include_system_prompts = True
            else:
                # Increment counter first (for non-init messages)
                counter += 1

                # Periodic mode - send prompts every Nth message
                if counter >= period:
                    include_system_prompts = True
                    counter = 0  # Reset after sending
                else:
                    include_system_prompts = False

                agent_entry['prompt_counter'] = counter

            # Build prompt based on period setting
            if include_system_prompts:
                identity_line = IDENTITY_TEMPLATE.format(display_name=app.get_agent_display_name(agent_name))
                participants_line = app._get_active_participants()
                system_prompt = app._get_agent_system_prompt(agent_name, is_init=is_init)
                prompt = (
                    f"{identity_line}\n"
                    f"{REPLY_CHAT_PROTOCOL_PROMPT}\n"
                    f"{participants_line}\n{system_prompt}\n\n{message}"
                )
                logger.trace(f"[{agent_name}] Including system prompts (counter reset, period={period})")
            else:
                prompt = message
                logger.trace(f"[{agent_name}] Skipping system prompts (counter={counter}/{period})")

            if not skip_log:
                logger.trace("=" * 50)
                logger.trace(f"[{agent_name}] MESSAGE FORWARDING PROMPT:")
                logger.trace(f"Message: {message}")
                logger.trace("=" * 50)

            command = app.get_agent_path(agent_name) or (app.agents.get(agent_name, {}).get('executable') or "codex")
            if not command:
                logger.error(f"[{agent_name}] CLI command path is not configured")
                app.add_error(f"{agent_name}: CLI command path is not configured", agent=agent_name)
                return None

            session_id = app.agents[agent_name]['session_id']
            if session_id:
                cmd = [
                    command, 'exec', '--json',
                    '--dangerously-bypass-approvals-and-sandbox',
                    '--skip-git-repo-check', 'resume', session_id, prompt
                ]
            else:
                cmd = [
                    command, 'exec', '--json',
                    '--dangerously-bypass-approvals-and-sandbox',
                    '--skip-git-repo-check', prompt
                ]

            context_overflow = False
            overflow_notified = False

            def parse_event(event: dict, current_text: str):
                nonlocal context_overflow, overflow_notified

                event_type = event.get('type', 'unknown')
                logger.trace(f"[{agent_name}] EVENT type={event_type}: {json.dumps(event, ensure_ascii=False)}")

                if event_type == 'thread.started':
                    thread_id = event.get('thread_id')
                    if thread_id:
                        app.agents[agent_name]['session_id'] = thread_id
                        logger.debug(f"[{agent_name}] Thread ID: {thread_id}")

                elif event_type in {'error', 'turn.failed'}:
                    error_msg = event.get('message', '') or event.get('error', {}).get('message', '')

                    if 'context window' in (error_msg or '').lower():
                        context_overflow = True
                        if not overflow_notified:
                            overflow_notified = True
                            logger.warning(f"[{agent_name}] Context overflow detected")
                            display_name = app.get_agent_display_name(agent_name)
                            app.add_status(STATUS_CONTEXT_OVERFLOW.format(display_name=display_name))
                        return {'action': ['compact', 'stop']}

                    fatal_error = error_msg or f'Unknown {agent_name} error'
                    logger.error(f"[{agent_name}] Error: {fatal_error}")
                    app.agents[agent_name]['session_id'] = None
                    display_name = app.get_agent_display_name(agent_name)
                    app.add_status(STATUS_SESSION_RESET.format(display_name=display_name))
                    return {'action': 'stop', 'error': fatal_error}

                elif event_type == 'item.completed':
                    item = event.get('item', {})
                    item_type = item.get('type', '')

                    if item_type == 'command_execution':
                        executed_command = item.get('command', '')
                        status = item.get('status', '')
                        if status == 'completed':
                            app.add_tool_call(agent_name, 'bash', executed_command[:50])

                    elif item_type in {'agent_message', 'message'}:
                        result_text = item.get('text', '')
                        logger.trace(f"[{agent_name}] FINAL MESSAGE ({item_type}): {result_text}")
                        return {'text': result_text, 'action': 'stop'}

                elif event_type == 'message':
                    result_text = event.get('text', '')
                    if result_text:
                        logger.trace(f"[{agent_name}] FINAL MESSAGE (message): {result_text}")
                        return {'text': result_text, 'action': 'stop'}

                return None

            response, actions, errors = await app._call_agent_cli(agent_name, cmd, parse_event)
            logger.trace(f"[{agent_name}] RAW RESPONSE: final_text={repr(response)}, actions={actions}, errors={errors}")

            def _needs_compact(text: str | None) -> bool:
                if not text:
                    return False
                lowered = str(text).lower()
                keywords = (
                    "token limit",
                    "context limit",
                    "context window",
                    "maximum context length",
                    "context length exceeded",
                    "limit exceeded",
                    "too many tokens",
                    "prompt too long",
                    "message is too long",
                )
                return any(phrase in lowered for phrase in keywords)

            limit_hint = any(_needs_compact(err) for err in errors)
            if isinstance(response, str) and _needs_compact(response):
                limit_hint = True

            combined_error: str | None = None
            if errors:
                combined_error = "\n".join(dict.fromkeys(str(e) for e in errors if e)) or None

            if context_overflow or limit_hint:
                if attempt >= 1:
                    logger.warning(f"[{agent_name}] Compact retry limit reached")
                    return None
                display_name = app.get_agent_display_name(agent_name)
                app.add_status(STATUS_CONTEXT_OVERFLOW.format(display_name=display_name))
                if await self._compact(app, agent_name):
                    attempt += 1
                    continue
                return None

            if combined_error:
                return {'text': combined_error, 'error': True}

            if app._is_silent_response_text(response):
                logger.debug(f"[{agent_name}] Stayed silent")
                return None

            agent_entry = app.agents.get(agent_name)
            if agent_entry and agent_entry.get('session_id'):
                agent_entry['message_count'] += 1
                agent_id = agent_entry.get('agent_id') or agent_name
                app.session_manager.save_agent_session(
                    agent_id,
                    agent_entry['session_id'],
                    agent_entry['message_count'],
                )

            return {'text': response}
