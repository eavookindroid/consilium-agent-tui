"""Backend implementation for Gemini agents."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from .base import AgentBackend
from ..constants import (
    IDENTITY_TEMPLATE,
    REPLY_CHAT_PROTOCOL_PROMPT,
    SYSTEM_PROMPT_PERIOD,
)

if TYPE_CHECKING:
    from ..app import ConsiliumAgentTUI


class GeminiBackend(AgentBackend):
    class_id = "gemini"
    display_name = "Gemini CLI"
    description = "CLI integration for Gemini agents."

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
            logger.trace(f"[{agent_name}] Skipping system prompts (counter={counter}/{SYSTEM_PROMPT_PERIOD})")

        if not skip_log:
            logger.trace("=" * 50)
            logger.trace(f"[{agent_name}] MESSAGE FORWARDING PROMPT:")
            logger.trace(f"Message: {message}")
            logger.trace("=" * 50)

        command = app.get_agent_path(agent_name) or (app.agents.get(agent_name, {}).get('executable') or "gemini")
        if not command:
            logger.error(f"[{agent_name}] CLI command path is not configured")
            app.add_error(f"{agent_name}: CLI command path is not configured", agent=agent_name)
            return None

        session_id = app.agents[agent_name]['session_id']
        resume_id = str(session_id).strip() if session_id else None
        cmd = [
            command,
            '-p',
            prompt,
            '--output-format=stream-json',
        ]
        if resume_id:
            cmd.extend(['--resume', resume_id])
        cmd.extend(['--verbose', '--dangerously-skip-permissions'])

        def parse_event(event: dict, current_text: str):
            event_type = event.get('type')

            if event_type == 'system' and 'session_id' in event:
                session = event.get('session_id')
                if session:
                    app.agents[agent_name]['session_id'] = session
                    logger.debug(f"[{agent_name}] Session ID: {session}")

            elif event_type == 'message' and event.get('role') == 'assistant':
                if not event.get('delta'):
                    content = event.get('content', '')
                    if content:
                        logger.trace(f"[{agent_name}] Assistant message: {content}")
                        return content

            elif event_type == 'tool_use':
                tool_name = event.get('tool_name', 'unknown')
                app.add_tool_call(agent_name, tool_name, '')

            elif event_type == 'result':
                logger.trace(f"[{agent_name}] FINAL RESULT: {current_text}")
                return current_text, 'stop'

            elif event_type == 'error':
                error_text = event.get('error') or event.get('message') or ''
                if error_text:
                    logger.error(f"[{agent_name}] ERROR: {error_text}")
                    return {'error': error_text, 'action': 'stop'}

            return None

        response, _actions, errors = await app._call_agent_cli(agent_name, cmd, parse_event)

        if errors:
            combined_error = "\n".join(dict.fromkeys(str(e) for e in errors if e))
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
