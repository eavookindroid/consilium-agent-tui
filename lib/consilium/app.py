"""
Consilium Agent - Main Application

TUI application for multi-agent chat with message routing.

Copyright (c) 2025 Artel Team
Licensed under Artel Team Non-Commercial License
"""

import json
import os
import asyncio
import inspect
import logging
import re
import shutil
import traceback
from typing import Any
from pathlib import Path
from functools import partial

from textual.app import App, ComposeResult
from textual import events
from textual.widgets import Footer
from textual.widget import Widget
from textual.binding import Binding
from textual.css.query import NoMatches
from rich.text import Text
from rich.style import Style
from rich.console import Group
from rich.markdown import Markdown

# Consilium imports
from .constants import *
from .agents import AgentOverrides, AgentProfile
from .utils import (
    INIT_PROMPT,
    SYSTEM_PROMPT,
    load_prompts_from_config,
    log_file_path,
)
from .courier import ConsiliumCourier, CourierMessage, CourierHooks, JournalEntry
from .session import SessionManager
from .widgets import (
    ChatComposer, ChatLog, ConsiliumCommandProvider,
    ConsiliumFooter, AnimatedStatusBar
)
from .modals import (
    PromptEditorScreen, PromptSelectionScreen,
    RoleSelectionScreen,
    AgentPromptEditorScreen, MembersSelectionScreen,
    SystemSettingsScreen
)
from .roles import RoleManager, Role
from .backends import AgentBackendRegistry
from .registry import AgentRegistry, AgentRegistryEvent


class ParticipantsHeader(Widget):
    """Custom header widget displaying participants with colours."""

    DEFAULT_CSS = """
    ParticipantsHeader {
        dock: top;
        height: 1;
        background: $panel;
        color: $text;
        padding: 0 1;
        content-align: center middle;
        text-style: bold;
        overflow: hidden;
    }
    """

    def __init__(self, *, name: str | None = None, id: str | None = None, classes: str | None = None) -> None:
        super().__init__(name=name, id=id, classes=classes)
        self._text: Text = Text("", no_wrap=True, overflow="ellipsis")

    def update_text(self, text: Text) -> None:
        self._text = text
        self.refresh()

    def render(self) -> Text:
        return self._text

    def on_click(self, event: events.Click) -> None:
        if event.style is None:
            return
        agent_name = event.style.meta.get("agent") if event.style.meta else None
        if not agent_name or agent_name == "User":
            return

        app = getattr(self, "app", None)
        if app is None:
            return

        current_enabled = app.is_agent_enabled(agent_name)
        app.set_agent_enabled(agent_name, not current_enabled)
        event.stop()


class ConsiliumAgentTUI(App):
    """Consilium Agent TUI with tool calls and logging"""

    CSS = """
    RichLog {
        background: $surface;
        color: $text;
        height: 1fr;
        width: 100%;
        overflow-x: hidden;
        text-wrap: wrap;
    }

    #chat-log {
        border: none;
        padding: 1;
        scrollbar-size-horizontal: 0;
        width: 100%;
    }

    #status-bar {
        height: 1;
        background: #000000;
        color: #888888;
        padding: 0 1;
        content-align: left middle;
    }

    """

    COMMANDS = App.COMMANDS | {ConsiliumCommandProvider}

    BINDINGS = [
        Binding("escape", "interrupt_conversation", "Interrupt"),
        Binding("ctrl+g", "kickoff_chat", "Start", show=True, key_display="Ctrl+G", priority=True),
        Binding("ctrl+q", "quit", "Quit", show=True, key_display="Ctrl+Q"),
        Binding("ctrl+s", "command_palette", "Settings", show=True, key_display="Ctrl+S"),
        Binding("ctrl+r", "edit_roles", "Roles", show=True, key_display="Ctrl+R"),
        Binding("ctrl+o", "toggle_step_mode", "STEP OFF", show=True, key_display="Ctrl+O", priority=True),
        Binding("ctrl+n", "next_step", "Next Step", show=True, key_display="Ctrl+N", priority=True),
        # Chat history navigation
        Binding("ctrl+home", "scroll_home", "Chat: Top", show=False),
        Binding("ctrl+end", "scroll_end", "Chat: Bottom", show=False),
        Binding("ctrl+pageup", "page_up", "Chat: Page Up", show=False),
        Binding("ctrl+pagedown", "page_down", "Chat: Page Down", show=False),
    ]

    def __init__(self):
        self.logger = logging.getLogger('ConsiliumAgent')
        self.settings_path = Path.home() / ".consilium" / "settings.json"
        self._user_settings: dict[str, Any] = {}
        self._settings_loaded = False
        self._suspend_theme_watch = False
        self.user_nickname: str | None = None  # User's display name (stored globally)
        self.user_avatar: str | None = None  # User's avatar emoji (stored globally)
        self.user_color: str | None = None  # User's preferred chat colour
        self._participants_header: ParticipantsHeader | None = None
        self._pending_header_text: Text | None = None
        self._participant_order_ids: list[str] = []
        self.enable_prompt_editor_command: bool = False
        self.logger.trace("ConsiliumAgentTUI:__init__ start cwd=%s", Path.cwd())

        # Shutdown management
        self._shutting_down = False
        self._shutdown_attempts = 0
        self._interrupt_requested = False
        self._background_tasks: list[asyncio.Task] = []
        self._running_subprocesses: list[asyncio.subprocess.Process] = []
        self._shutdown_trigger: str | None = None

        # Load prompts into memory (already loaded in utils module)
        self.init_prompt = INIT_PROMPT
        self.system_prompt = SYSTEM_PROMPT
        # System prompt refresh period (will be loaded from settings)
        self.system_prompt_period = SYSTEM_PROMPT_PERIOD
        # Per-agent system prompts (workspace-specific overrides); populated later
        self.agent_prompts: dict[str, str] = {}
        self._agents_without_prompt: set[str] = set()
        self.logger.trace("ConsiliumAgentTUI:prompts loaded")

        super().__init__()

        self.role_manager = RoleManager()

        # Initialize session manager and workspace paths
        self.session_manager = SessionManager(Path.cwd())
        self.workspace_root = self.session_manager.workspace_path
        self._prompts_root: Path = self.session_manager.session_dir / "prompts"
        self.logger.trace(
            "ConsiliumAgentTUI:workspace paths set root=%s prompts_root=%s",
            self.workspace_root,
            self._prompts_root,
        )
        self._asyncio_handler_installed = False

        self.history = []  # For UI display only (not for prompts!)
        self.history_limit = 1000

        # Agent registry and backend registry
        self.backend_registry = AgentBackendRegistry()
        self.agent_registry = AgentRegistry(self.settings_path)
        self.agent_profiles: dict[str, AgentProfile] = {}
        self.agents: dict[str, dict[str, Any]] = {}
        self._registry_listener_task: asyncio.Task | None = None
        self._load_agents_from_registry()
        self.logger.trace("ConsiliumAgentTUI:agent registry initialized %s", list(self.agents.keys()))

        hooks = CourierHooks(
            add_status=self.add_status,
            publish_entry=self._publish_entry_from_courier,
            is_shutting_down=lambda: self._shutting_down,
            is_interrupt_requested=lambda: self._interrupt_requested,
            wait_for_step_permission=self._wait_for_step_permission,
            is_step_mode_enabled=lambda: self.step_by_step_mode,
            get_display_name=self.get_agent_display_name,
            is_silent_response_text=self._is_silent_response_text,
            run_backend=self.run_agent_backend,
        )
        last_msg_id = self.session_manager.get_last_message_id()
        if last_msg_id:
            self.logger.info("Resuming message numbering from %d", last_msg_id + 1)
        self.courier = ConsiliumCourier(
            self.agents,
            hooks,
            self.logger.getChild("Courier"),
            last_message_id=last_msg_id,
        )

        self.agent_locks: dict[str, asyncio.Lock | None] = {}
        self.logger.trace("ConsiliumAgentTUI:agent locks initialized")

        # Input history for Ctrl+Up/Down navigation (stored in workspace session dir)
        self.input_history_path = self.session_manager.session_dir / "input_history.json"
        self.input_history: list[str] = []
        self.input_history_index: int = -1
        self.input_history_limit: int = 300
        self.logger.trace("ConsiliumAgentTUI:input history initialized")

        # Step-by-step mode
        self.step_by_step_mode: bool = False
        self.waiting_for_step: bool = False
        self.step_event: asyncio.Event = asyncio.Event()
        self.step_event.set()  # Initially not waiting

        # Status bar auto-clear timer
        self._status_clear_timer = None
        self._status_bar_widget: AnimatedStatusBar | None = None
        self._pending_status_text: str | None = None

        # Chat entries storage for dynamic resize
        self._chat_entries: list[tuple[Any, dict[str, Any]]] = []
        self._rebuilding_chat = False

        # Startup gate (Ctrl+G) for first-time workspaces
        self._start_gate_active: bool = False
        self._start_gate_event: asyncio.Event = asyncio.Event()

        # Load persisted application-level settings (theme, etc.)
        self._load_user_settings()
        self.logger.trace("ConsiliumAgentTUI:user settings loaded")

        self.setup_workspace()
        self._load_workspace_prompts()
        self.load_persisted_sessions()
        self._load_input_history()
        self.logger.trace("ConsiliumAgentTUI:initialization steps complete")
        self.logger.debug(f"ConsiliumAgentTUI initialized with agents: {list(self.agents.keys())}")

    # ------------------------------------------------------------------
    # Agent registry helpers
    # ------------------------------------------------------------------

    def _load_agents_from_registry(self) -> None:
        """Synchronously load agent profiles and build runtime entries."""
        self.agent_registry.load_sync()

        profiles = list(self.agent_registry.list_profiles())
        self.agent_profiles = {profile.agent_id: profile for profile in profiles}

        runtime_state = self._capture_agent_runtime()
        self._agent_name_map = {}
        self._agent_id_to_name = {}
        self.agents.clear()
        self.agent_locks = {}
        self._participant_order_ids = []

        for profile in profiles:
            self._register_profile(profile, runtime_state.get(profile.agent_id))

        self._refresh_participants_ui()

    def _capture_agent_runtime(self) -> dict[str, dict[str, Any]]:
        """Capture runtime-only state for existing agents."""
        runtime: dict[str, dict[str, Any]] = {}
        for name, config in self.agents.items():
            agent_id = config.get('agent_id') or name
            runtime[agent_id] = {
                'session_id': config.get('session_id'),
                'message_count': config.get('message_count', 0),
                'process': config.get('process'),
                'enabled': config.get('enabled', True),
            }
        return runtime

    def _build_agent_entry(self, profile: AgentProfile, runtime: dict[str, Any] | None = None) -> dict[str, Any]:
        """Create runtime configuration entry for courier and UI."""
        command_default = profile.descriptor.default_executable
        command_override = profile.overrides.command_path
        command_override_raw = command_override.strip() if command_override and command_override.strip() else ""
        command_override_expanded = self._expand_command_path(command_override_raw) if command_override_raw else ""

        backend_id = profile.get_backend_id()
        if not backend_id:
            default_backend = self.backend_registry.get_default_backend()
            backend_id = default_backend.class_id if default_backend else None

        descriptor_color = self._normalize_color_value(profile.descriptor.color) if getattr(profile.descriptor, "color", None) else None
        if descriptor_color is None:
            descriptor_color = self.get_default_agent_color()

        override_color = self._normalize_color_value(profile.overrides.color) if profile.overrides.color else None
        color_effective = override_color or descriptor_color

        metadata_avatar = ""
        if isinstance(profile.descriptor.metadata, dict):
            meta_value = profile.descriptor.metadata.get("avatar")
            if isinstance(meta_value, str) and meta_value.strip():
                metadata_avatar = meta_value.strip()

        override_avatar = profile.overrides.avatar
        stored_avatar = override_avatar.strip() if override_avatar and override_avatar.strip() else ""
        effective_avatar = stored_avatar or metadata_avatar or self._color_to_emoji(color_effective)

        entry = {
            'profile': profile,
            'agent_id': profile.agent_id,
            'display_name': profile.get_display_name(),
            'session_id': None,
            'color': color_effective,
            'color_override': override_color or "",
            'color_default': descriptor_color,
            'avatar': stored_avatar,
            'avatar_default': effective_avatar,
            'backend_id': backend_id,
            'backend': None,
            'message_count': 0,
            'prompt_counter': 0,
            'enabled': profile.is_enabled(),
            'process': None,
            'nickname': profile.overrides.nickname,
            'class_name': profile.descriptor.class_name,
            'executable': command_default,
            'command_path': command_override_expanded,
            'role_id': profile.overrides.role_id or profile.descriptor.default_role,
        }
        self.logger.trace(
            "Members profile resolved: %s overrides=%s",
            profile.agent_id,
            profile.overrides.as_dict(),
        )

        if runtime:
            if runtime.get('session_id') is not None:
                entry['session_id'] = runtime.get('session_id')
            entry['message_count'] = runtime.get('message_count', entry['message_count'])
            entry['process'] = runtime.get('process')
            if runtime.get('enabled') is not None:
                entry['enabled'] = bool(runtime.get('enabled'))

        backend_obj = self.backend_registry.get_backend(entry['backend_id'])
        if backend_obj is not None:
            entry['backend'] = backend_obj
            entry['backend_id'] = backend_obj.class_id
            entry['class_name'] = backend_obj.display_name or backend_obj.class_id
        else:
            entry['backend'] = None
            if not entry.get('class_name'):
                entry['class_name'] = (entry['backend_id'] or '').title() or "—"

        return entry

    def _register_profile(self, profile: AgentProfile, runtime: dict[str, Any] | None = None) -> str:
        entry = self._build_agent_entry(profile, runtime)
        agent_id = profile.agent_id
        desired_key = entry['display_name']
        existing_key = self._agent_id_to_name.get(agent_id)

        if existing_key and existing_key in self.agents:
            current_entry = self.agents.pop(existing_key)
            lock = self.agent_locks.pop(existing_key, None)
            self._agent_name_map.pop(existing_key, None)
            self._agent_id_to_name.pop(agent_id, None)
            entry['session_id'] = current_entry.get('session_id') or entry['session_id']
            entry['message_count'] = current_entry.get('message_count', entry['message_count'])
            entry['process'] = current_entry.get('process') or entry['process']
        else:
            lock = None

        key = desired_key
        if key in self.agents and self.agents[key].get('agent_id') != agent_id:
            key = f"{desired_key}#{agent_id}"
            self.logger.warning(
                "Duplicate agent display name '%s', using key '%s'",
                desired_key,
                key,
            )

        if lock is None:
            self.agent_locks[key] = None
        else:
            self.agent_locks[key] = lock

        self.agents[key] = entry
        self._agent_name_map[key] = agent_id
        self._agent_id_to_name[agent_id] = key
        if agent_id not in self._participant_order_ids:
            self._participant_order_ids.append(agent_id)

        courier = getattr(self, "courier", None)
        if existing_key and existing_key != key and courier:
            courier.mark_participant_disabled(existing_key)

        if courier:
            if entry.get('enabled'):
                courier.mark_participant_enabled(key)
            else:
                courier.mark_participant_disabled(key)

        self._apply_role_prompt(key, entry.get('role_id'), persist=False, force=False)
        return key

    def _get_agent_lock(self, agent_name: str) -> asyncio.Lock:
        lock = self.agent_locks.get(agent_name)
        if lock is None:
            lock = asyncio.Lock()
            self.agent_locks[agent_name] = lock
        return lock

    async def run_agent_backend(self, agent_name: str, message: str, is_init: bool, skip_log: bool) -> Any:
        entry = self.agents.get(agent_name)
        if not entry:
            self.logger.error("Attempted to run backend for unknown agent '%s'", agent_name)
            return None

        backend = entry.get('backend')
        if backend is None:
            backend = self.backend_registry.get_backend(entry.get('backend_id'))
            if backend is None:
                self.logger.error("No backend configured for agent '%s'", agent_name)
                self.add_error(f"{agent_name}: backend class is not configured", agent=agent_name)
                return None
            entry['backend'] = backend
            entry['backend_id'] = backend.class_id

        return await backend.run(
            self,
            agent_name,
            message,
            is_init=is_init,
            skip_log=skip_log,
        )

    def _remove_agent_by_id(self, agent_id: str) -> None:
        key = self._agent_id_to_name.pop(agent_id, None)
        if not key:
            return

        entry = self.agents.pop(key, None)
        self._agent_name_map.pop(key, None)
        lock = self.agent_locks.pop(key, None)
        process = entry.get('process') if entry else None
        if agent_id in self._participant_order_ids:
            self._participant_order_ids.remove(agent_id)
        if process is not None and getattr(process, 'returncode', None) is None:
            try:
                process.kill()
                self.logger.debug("Process.kill() called for removed agent %s", key)
            except Exception as exc:  # pragma: no cover - safety
                self.logger.warning("Failed to kill process for removed agent %s: %s", key, exc)
            self._running_subprocesses = [p for p in self._running_subprocesses if p is not process]

        courier = getattr(self, "courier", None)
        if courier:
            courier.mark_participant_disabled(key)

    async def _subscribe_registry_events(self) -> None:
        await self.agent_registry.subscribe(self._handle_registry_event)

    async def _handle_registry_event(self, event: AgentRegistryEvent) -> None:
        self._apply_registry_event(event)

    def _apply_registry_event(self, event: AgentRegistryEvent) -> None:
        if self._shutting_down:
            return

        event_type = event.event_type
        if event_type == "registry-loaded":
            self._load_agents_from_registry()
        elif event_type in {"profile-created", "profile-updated"} and event.profile:
            self.agent_profiles[event.profile.agent_id] = event.profile
            runtime = self._capture_agent_runtime().get(event.profile.agent_id)
            self._register_profile(event.profile, runtime)
        elif event_type == "profile-removed":
            self.agent_profiles.pop(event.agent_id, None)
            self._remove_agent_by_id(event.agent_id)

        self._refresh_participants_ui()

    def _resolve_agent_id(self, agent_name: str) -> str | None:
        """Return agent_id for given display name key."""
        entry = self.agents.get(agent_name)
        if entry:
            return entry.get('agent_id')
        return self._agent_name_map.get(agent_name)

    def _close_messages_no_wait(self) -> None:
        """Trace Textual request to stop the message pump instantly."""
        stack_summary = traceback.extract_stack(limit=50)
        formatted_stack = "".join(traceback.format_list(stack_summary[:-1]))

        if self._shutdown_trigger is None:
            self._shutdown_trigger = "message pump close"

        self.logger.error(
            "Textual MessagePump requested immediate shutdown (trigger=%s)",
            self._shutdown_trigger,
        )
        self.logger.error("MessagePump close stack:\n%s", formatted_stack)
        self.logger.debug("MessagePump close stack raw=%r", formatted_stack)

        super()._close_messages_no_wait()

    # -------------------------------------------------------------------------
    # Shutdown helpers
    # -------------------------------------------------------------------------

    def _create_task(self, coro):
        """Create and track a background task for proper shutdown cleanup."""
        task = asyncio.create_task(coro)
        self._background_tasks.append(task)
        self.logger.trace(
            "ConsiliumAgentTUI:create_task total=%d coro=%s",
            len(self._background_tasks),
            getattr(coro, "__name__", repr(coro)),
        )

        def _on_done(done: asyncio.Task) -> None:
            if done in self._background_tasks:
                self._background_tasks.remove(done)
            try:
                exc = done.exception()
            except asyncio.CancelledError:
                self.logger.debug("Background task %s cancelled", done.get_name())
                return
            except Exception as inner_exc:  # pragma: no cover - defensive
                self.logger.error("Failed to retrieve task exception: %s", inner_exc, exc_info=True)
                return

            if exc is not None:
                self.logger.exception("Background task crashed: %s", exc)

        task.add_done_callback(_on_done)
        return task

    async def _drain_background_tasks(self, timeout: float = 1.5) -> None:
        """Allow pending background tasks to finish before cancellation."""
        pending = [task for task in self._background_tasks if not task.done()]
        if not pending:
            return

        self.logger.debug("Awaiting %d background tasks before shutdown", len(pending))
        try:
            await asyncio.wait_for(asyncio.gather(*pending), timeout=timeout)
        except asyncio.TimeoutError:
            self.logger.warning("Timeout waiting for background tasks, cancelling remaining")
            for task in pending:
                if not task.done():
                    task.cancel()
            await asyncio.gather(*pending, return_exceptions=True)
        finally:
            self._background_tasks = [task for task in self._background_tasks if not task.done()]

    async def _create_subprocess_exec(self, *args, **kwargs):
        """Create subprocess and track it for shutdown cleanup."""
        process = await asyncio.create_subprocess_exec(*args, **kwargs)
        self._running_subprocesses.append(process)
        self.logger.trace("ConsiliumAgentTUI:create_subprocess pid=%s total=%d", getattr(process, 'pid', None), len(self._running_subprocesses))
        return process

    def _get_active_participants(self) -> str:
        """
        Generate dynamic participant list based on currently enabled agents.
        Returns a string like: "You are in a group chat with User, Claude, Codex."
        """
        # Get enabled agent names (use display names/nicknames)
        enabled_agents = [
            self.get_agent_display_name(name)
            for name, config in self.agents.items()
            if config.get('enabled', True)
        ]

        # Always include User as first participant (use nickname if set)
        user_display_name = self.get_agent_display_name("User")
        participants = [user_display_name] + enabled_agents

        # Simple format: just list all participants
        return PARTICIPANTS_TEMPLATE.format(participants=', '.join(participants))

    def _color_to_emoji(self, color: str) -> str:
        """Map agent color to emoji circle."""
        color_map = {
            '#eacf5b': '🟡',  # yellow for Claude
            '#55daeb': '🔵',  # cyan for Codex
            '#c0c0c0': '⚪',  # silver for Gemini
            '#f3acf8': '🟣',  # purple/pink for Glm
        }
        key = color.lower() if isinstance(color, str) else color
        return color_map.get(key, '⚪')  # white circle as fallback

    def _resolve_agent_avatar(self, agent_name: str) -> str:
        agent = self.agents.get(agent_name, {})
        avatar = agent.get('avatar')
        if isinstance(avatar, str) and avatar.strip():
            return avatar.strip()
        default_avatar = agent.get('avatar_default')
        if isinstance(default_avatar, str) and default_avatar.strip():
            return default_avatar.strip()
        color = agent.get('color') or agent.get('color_default') or self.get_default_agent_color()
        if isinstance(color, str) and color:
            return self._color_to_emoji(color)
        return '⚪'

    def _generate_dynamic_title(self) -> str:
        """
        Generate dynamic title based on enabled agents.
        Returns: "Consilium Agent : 🟢 [User] + 🟡 [Claude] + 🔵 [Codex]"
        """
        # Always include User first (use nickname if set)
        user_display_name = self.get_agent_display_name("User")
        user_avatar = self.get_user_avatar() or "🟢"
        parts = [f'{user_avatar} [{user_display_name}]']

        # Add enabled agents with their emoji (use display names/nicknames)
        for name, config in self.agents.items():
            if config.get('enabled', True):
                emoji = self._resolve_agent_avatar(name)
                display_name = self.get_agent_display_name(name)
                parts.append(f"{emoji} [{display_name}]")

        return f"Consilium Agent : {' + '.join(parts)}"

    def _render_participants_header_text(self) -> Text:
        text = Text(no_wrap=True, overflow="ellipsis")
        text.append("Members: ", style="bold")

        user_display_name = self.get_agent_display_name("User")
        user_color = self.get_user_color()
        user_avatar = self.get_user_avatar() or "🟢"
        user_style = Style(color=user_color) if user_color else None
        text.append(f"{user_avatar} {user_display_name}", style=user_style)
        text.append("    ")

        for agent_id in self._participant_order_ids:
            key = self._agent_id_to_name.get(agent_id)
            if not key:
                continue
            config = self.agents.get(key)
            if not config:
                continue
            color = config.get('color') or config.get('color_default') or self.get_default_agent_color()
            avatar = self._resolve_agent_avatar(key)
            display_name = self.get_agent_display_name(key)
            enabled = bool(config.get('enabled', True))
            base_color = color if enabled else "#515151"
            style = Style(color=base_color, meta={"agent": key})
            text.append(f"{avatar} {display_name}", style=style)
            text.append("    ")

        return text

    def _refresh_participants_ui(self) -> None:
        text = self._render_participants_header_text()
        if self._participants_header is not None:
            try:
                self._participants_header.update_text(text)
            except Exception:
                self.logger.exception("Failed to update participants header")
        else:
            self._pending_header_text = text

    async def _shutdown(self):
        """Gracefully shutdown all background tasks and subprocesses."""
        # Prevent concurrent shutdowns
        if self._shutting_down and self._shutdown_attempts > 0:
            self.logger.debug("Shutdown already in progress, skipping duplicate call")
            return

        self._shutting_down = True
        self._shutdown_attempts += 1
        hard = (self._shutdown_attempts > 1)

        # Capture caller information for diagnostics
        import traceback

        stack_summary = traceback.extract_stack(limit=50)
        origin_frame = stack_summary[-2] if len(stack_summary) >= 2 else stack_summary[-1]
        origin_desc = f"{Path(origin_frame.filename).name}:{origin_frame.lineno}#{origin_frame.name}"
        reason = self._shutdown_trigger or "unknown"

        self.logger.info(
            "Shutting down... (attempt %s, hard=%s, reason=%s, origin=%s)",
            self._shutdown_attempts,
            hard,
            reason,
            origin_desc,
        )

        # Detailed call stack for deep debugging (TRACE level only)
        formatted_stack = "".join(traceback.format_list(stack_summary[:-1]))
        self.logger.debug("Shutdown call stack trimmed:\n%s", formatted_stack)
        self.logger.trace("Shutdown stack raw=%r", formatted_stack)

        # Allow background tasks to complete gracefully
        await self._drain_background_tasks()

        # Cancel any lingering background tasks
        if self._background_tasks:
            self.logger.debug(f"Cancelling {len(self._background_tasks)} remaining background tasks")
            for task in list(self._background_tasks):
                if not task.done():
                    task.cancel()

            await asyncio.gather(*self._background_tasks, return_exceptions=True)
            self._background_tasks.clear()

        # Terminate all running subprocesses
        if self._running_subprocesses:
            self.logger.debug(f"Terminating {len(self._running_subprocesses)} subprocesses")
            for proc in list(self._running_subprocesses):
                if proc.returncode is None:
                    try:
                        proc.terminate()
                    except ProcessLookupError:
                        pass  # Process already terminated

            # Grace period for soft shutdown (skip in hard mode)
            if not hard:
                await asyncio.sleep(SHUTDOWN_GRACE_PERIOD)

            # Kill any remaining processes
            for proc in list(self._running_subprocesses):
                if proc.returncode is None:
                    try:
                        proc.kill()
                        # Wait with timeout to prevent hanging on unresponsive processes
                        try:
                            await asyncio.wait_for(proc.wait(), timeout=2.0)
                            self.logger.debug(f"Killed subprocess PID {proc.pid}")
                        except asyncio.TimeoutError:
                            self.logger.warning(f"Process PID {proc.pid} did not terminate in time")
                    except ProcessLookupError:
                        pass

            self._running_subprocesses.clear()

        self.logger.info("Shutdown complete")

    async def action_interrupt_conversation(self):
        """Stop all agents without shutting down application (ESC key)"""
        if self._interrupt_requested:
            return

        self._interrupt_requested = True
        self.logger.info("Interrupting conversation...")

        # Kill all subprocesses
        for proc in list(self._running_subprocesses):
            if proc.returncode is None:
                try:
                    proc.terminate()
                except ProcessLookupError:
                    pass

        # Grace period for soft termination
        await asyncio.sleep(SHUTDOWN_GRACE_PERIOD)

        # Kill any remaining processes
        for proc in list(self._running_subprocesses):
            if proc.returncode is None:
                try:
                    proc.kill()
                    await proc.wait()
                    self.logger.debug(f"Killed subprocess PID {proc.pid}")
                except ProcessLookupError:
                    pass

        # Cancel all background tasks
        await self._drain_background_tasks()

        pending = [task for task in self._background_tasks if not task.done()]
        for task in pending:
            task.cancel()

        if self._background_tasks:
            await asyncio.gather(*self._background_tasks, return_exceptions=True)
            self._background_tasks.clear()

        self._write_chat(Text(TEXT_INTERRUPTED, style="italic #666666"))
        self.logger.info("Conversation interrupted")
        self._interrupt_requested = False


    def _write_chat(self, content, *, remember: bool = True):
        """Write to chat log with smart auto-scroll.

        Only scrolls to bottom if user is already at bottom, preserving
        manual scroll position when user is reading history.

        Args:
            content: Content to write to chat
            remember: If True, store entry for dynamic resize (default: True)
        """
        # Store entry for resize if requested
        if remember and not self._rebuilding_chat:
            self._chat_entries.append((content, {}))

        chat_log = self.query_one("#chat-log", ChatLog)
        # Auto-detect: only scroll if user is already at bottom
        should_scroll = chat_log.is_vertical_scroll_end
        chat_log.write(content, scroll_end=should_scroll)

    def _rebuild_chat_log(self) -> None:
        """Re-render chat entries to accommodate layout changes (e.g., terminal resize)."""
        if self._rebuilding_chat:
            return  # Prevent recursive rebuilds

        try:
            chat_log = self.query_one("#chat-log", ChatLog)
        except NoMatches:
            self.logger.debug("Chat log not available for rebuild")
            return

        self._rebuilding_chat = True
        try:
            # Save current entries
            entries = list(self._chat_entries)

            # Clear and rebuild
            chat_log.clear()
            for content, _kwargs in entries:
                # Write without remembering (avoid duplicates)
                self._write_chat(content, remember=False)
        finally:
            self._rebuilding_chat = False

    def on_resize(self, event: events.Resize) -> None:
        """Re-wrap chat log content when terminal size changes."""
        # Call parent handler if exists
        parent_on_resize = getattr(super(), "on_resize", None)
        if callable(parent_on_resize):
            parent_on_resize(event)

        # Schedule chat rebuild for text re-wrapping
        self.call_later(self._rebuild_chat_log)

    def setup_workspace(self):
        """Create shared workspace"""
        self.logger.info(f"Workspace ready: {self.workspace_root.absolute()}")
        self.logger.trace(
            "ConsiliumAgentTUI:prompt storage ready at %s",
            self._prompts_root,
        )

    def load_persisted_sessions(self):
        """Load persisted session IDs for all agents"""
        self.logger.trace("ConsiliumAgentTUI:load_persisted_sessions start")
        self.session_metadata = self.session_manager.load_session_metadata()

        for agent_display_name, agent_config in self.agents.items():
            agent_id = agent_config.get('agent_id') or agent_display_name
            aliases = [agent_display_name]
            agent_data = self.session_manager.load_agent_session(agent_id, aliases=aliases)
            if agent_data.get('session_id'):
                agent_config['session_id'] = agent_data['session_id']
                agent_config['message_count'] = agent_data.get('message_count', 0)
                self.logger.info(f"{agent_display_name} session restored: {agent_data['session_id']}")

        self.logger.trace("ConsiliumAgentTUI:load_persisted_sessions complete")

    # ---------------------------------------------------------------------
    # Input history helpers
    # ---------------------------------------------------------------------

    def _load_input_history(self):
        """Load input history from workspace/input_history.json"""
        history_file = self.input_history_path

        if not history_file.exists():
            self.logger.debug("No input history file found, starting with empty history")
            self.logger.trace("ConsiliumAgentTUI:input history file missing")
            return

        try:
            with history_file.open('r', encoding='utf-8') as f:
                data = json.load(f)
                if isinstance(data, list):
                    # Filter out commands when loading
                    filtered = [
                        entry for entry in data
                        if isinstance(entry, str) and entry.strip() and not entry.strip().startswith('/')
                    ]
                    self.input_history = filtered[-self.input_history_limit:]
                    self.logger.debug(f"Loaded {len(self.input_history)} input history entries")
                    self.logger.trace("ConsiliumAgentTUI:input history entries loaded count=%d", len(self.input_history))
                else:
                    self.logger.warning("Invalid input history format, starting fresh")
        except Exception:
            self.logger.exception("Failed to load input history")

    def _save_input_history(self):
        """Save input history to workspace/input_history.json"""
        history_file = self.input_history_path

        try:
            history_file.parent.mkdir(parents=True, exist_ok=True)
            # Keep only last N entries to prevent file growth
            history_to_save = self.input_history[-self.input_history_limit:]
            with history_file.open('w', encoding='utf-8') as f:
                json.dump(history_to_save, f, ensure_ascii=False, indent=2)
            self.logger.debug(f"Saved {len(history_to_save)} input history entries")
            self.logger.trace("ConsiliumAgentTUI:input history saved count=%d", len(history_to_save))
        except Exception:
            self.logger.exception("Failed to save input history")

    def get_history_prev(self) -> str | None:
        """Get previous message from input history"""
        if not self.input_history:
            return None

        # If at end (-1), start from last item
        if self.input_history_index == -1:
            self.input_history_index = len(self.input_history) - 1
        # Otherwise move backwards
        elif self.input_history_index > 0:
            self.input_history_index -= 1

        return self.input_history[self.input_history_index]

    def get_history_next(self) -> str | None:
        """Get next message from input history"""
        if not self.input_history or self.input_history_index == -1:
            return None

        # Move forward
        if self.input_history_index < len(self.input_history) - 1:
            self.input_history_index += 1
            return self.input_history[self.input_history_index]
        else:
            # Reached end, reset index
            self.input_history_index = -1
            return ""  # Empty string to clear composer

    def _remember_input(self, text: str) -> None:
        """Persist user input for history navigation."""
        normalized = text.strip()
        if not normalized or normalized.startswith('/'):
            return

        self.input_history.append(normalized)

        # Keep only the most recent entries within the limit
        if len(self.input_history) > self.input_history_limit:
            self.input_history = self.input_history[-self.input_history_limit:]

        # Reset navigation pointer so Ctrl+Up starts from the newest entry
        self.input_history_index = -1

        self._save_input_history()
        self.logger.trace(f"Input remembered (total={len(self.input_history)})")

    def get_previous_input(self) -> str | None:
        """Expose previous input for the composer (Ctrl+Up)."""
        return self.get_history_prev()

    def get_next_input(self) -> str | None:
        """Expose next input for the composer (Ctrl+Down)."""
        return self.get_history_next()

    # ---------------------------------------------------------------------
    # User settings (theme, etc.)
    # ---------------------------------------------------------------------

    def _load_user_settings(self):
        """Load settings like theme from ~/.consilium/settings.json."""
        settings: dict[str, Any] = {}

        if self.settings_path.exists():
            try:
                with self.settings_path.open('r', encoding='utf-8') as handle:
                    settings = json.load(handle)
                self.logger.debug(f"Loaded settings from {self.settings_path}")
                self.logger.trace("ConsiliumAgentTUI:user settings file read")
                if 'members' in settings:
                    # Keep members management isolated in AgentRegistry; avoid stale snapshots
                    settings = dict(settings)
                    settings.pop('members', None)
            except Exception:
                self.logger.exception("Failed to load settings")
                settings = {}

        theme = settings.get('theme')
        if isinstance(theme, str):
            self.logger.debug(f"Applying saved theme: {theme}")
            self._suspend_theme_watch = True
            try:
                self.theme = theme
            except Exception:
                self.logger.warning(
                    f"Failed to apply saved theme '{theme}'",
                    exc_info=True,
                )
            finally:
                self._suspend_theme_watch = False

        # Persist current enabled state snapshot for compatibility
        synced_enabled: dict[str, bool] = {}

        for agent_name, agent_cfg in self.agents.items():
            agent_id = agent_cfg.get('agent_id') or agent_name
            enabled = bool(agent_cfg.get('enabled', True))
            synced_enabled[agent_id] = enabled

            if hasattr(self, "courier") and self.courier:
                if enabled:
                    self.courier.mark_participant_enabled(agent_name)
                else:
                    self.courier.mark_participant_disabled(agent_name)

        settings['agents_enabled'] = synced_enabled

        # Load user nickname (global setting)
        user_nickname = settings.get('user_nickname')
        if isinstance(user_nickname, str) and user_nickname.strip():
            self.user_nickname = user_nickname.strip()
            self.logger.debug(f"Loaded user nickname: {self.user_nickname}")
        else:
            self.user_nickname = None

        user_avatar = settings.get('user_avatar')
        if isinstance(user_avatar, str) and user_avatar.strip():
            self.user_avatar = self._normalize_avatar_value(user_avatar)
            if self.user_avatar:
                self.logger.debug("Loaded user avatar: %s", self.user_avatar)
            else:
                self.user_avatar = None
        else:
            self.user_avatar = None

        user_color = settings.get('user_color')
        if isinstance(user_color, str) and user_color.strip():
            normalized_user_color = self._normalize_color_value(user_color)
            if normalized_user_color:
                self.user_color = normalized_user_color
                self.logger.debug("Loaded user color: %s", self.user_color)
            else:
                self.user_color = None
        else:
            self.user_color = None

        # Load system prompt period
        system_period = settings.get('system_prompt_period')
        if isinstance(system_period, int) and system_period >= 0:
            self.system_prompt_period = system_period
            self.logger.debug("Loaded system prompt period: %s", system_period)

        self._user_settings = settings
        self._settings_loaded = True

    def _update_agent_settings_cache(self) -> None:
        """Ensure agent enabled states are stored in settings cache."""
        if not hasattr(self, 'agents'):
            return
        agent_states = {
            (config.get('agent_id') or name): bool(config.get('enabled', True))
            for name, config in self.agents.items()
            if config.get('agent_id')
        }
        self._user_settings['agents_enabled'] = agent_states

    def _save_user_settings(self):
        """Persist current settings to disk."""
        if not self._settings_loaded:
            return

        self._update_agent_settings_cache()

        # Update user nickname in settings cache
        if self.user_nickname:
            self._user_settings['user_nickname'] = self.user_nickname
        elif 'user_nickname' in self._user_settings:
            del self._user_settings['user_nickname']

        if self.user_avatar:
            self._user_settings['user_avatar'] = self.user_avatar
        elif 'user_avatar' in self._user_settings:
            del self._user_settings['user_avatar']

        if self.user_color:
            self._user_settings['user_color'] = self.user_color
        elif 'user_color' in self._user_settings:
            del self._user_settings['user_color']

        # Save system prompt period
        if hasattr(self, 'system_prompt_period'):
            self._user_settings['system_prompt_period'] = self.system_prompt_period

        try:
            current_settings: dict[str, Any] = {}
            if self.settings_path.exists():
                try:
                    with self.settings_path.open('r', encoding='utf-8') as handle:
                        current_settings = json.load(handle)
                except Exception:
                    self.logger.warning("Failed to read existing settings before save", exc_info=True)

            merged = dict(current_settings)
            merged.update(self._user_settings)
            if self.logger:
                members_snapshot = merged.get('members') if isinstance(merged.get('members'), list) else None
                self.logger.trace(
                    "User settings save merge: members=%s agents_enabled=%s",
                    members_snapshot,
                    merged.get('agents_enabled'),
                )

            self.settings_path.parent.mkdir(parents=True, exist_ok=True)
            with self.settings_path.open('w', encoding='utf-8') as handle:
                json.dump(merged, handle, indent=2, ensure_ascii=False)
            self.logger.debug(f"Settings saved to {self.settings_path}")
            self.logger.trace("ConsiliumAgentTUI:user settings saved")
        except Exception as exc:
            self.logger.exception("Failed to save settings")

    def watch_theme(self, theme: str) -> None:
        """Persist theme changes triggered via the settings panel."""
        if self._suspend_theme_watch or not self._settings_loaded:
            return

        if self._user_settings.get('theme') == theme:
            return

        self._user_settings['theme'] = theme
        self.logger.info(f"Theme changed to '{theme}'")
        self._save_user_settings()

    def is_agent_enabled(self, agent_name: str) -> bool:
        """Return True if agent is currently enabled."""
        agent = self.agents.get(agent_name)
        if not agent:
            return False
        return bool(agent.get('enabled', True))

    def set_agent_enabled(self, agent_name: str, enabled: bool) -> None:
        """Enable or disable agent participation."""
        agent = self.agents.get(agent_name)
        if not agent:
            self.logger.warning(f"Attempted to toggle unknown agent '{agent_name}'")
            return

        current = bool(agent.get('enabled', True))
        if current == enabled:
            self.logger.debug(f"{agent_name} already {('enabled' if enabled else 'disabled')}, no change needed")
            return

        agent_id = agent.get('agent_id')
        profile = self.agent_profiles.get(agent_id) if agent_id else None
        agent['enabled'] = enabled
        if profile:
            profile.overrides.enabled = enabled

        state = "enabled" if enabled else "disabled"
        self.logger.info(f"{agent_name} has been {state} (old={current}, new={enabled})")
        self.logger.debug(f"Current agent states: {[(name, cfg.get('enabled')) for name, cfg in self.agents.items()]}")

        if self.courier:
            if enabled:
                self.courier.mark_participant_enabled(agent_name)
            else:
                self.courier.mark_participant_disabled(agent_name)

        if not enabled:
            self._terminate_agent_process(agent_name, agent)

        if agent_id:
            self._create_task(
                self.agent_registry.patch_overrides(
                    agent_id,
                    {"enabled": enabled},
                )
            )

        self._refresh_participants_ui()
        self._save_user_settings()

    def _terminate_agent_process(self, agent_name: str, agent: dict[str, Any]) -> None:
        """Stop running agent process when disabling."""
        process = agent.get('process')
        if not process:
            return
        try:
            if process.returncode is None:
                try:
                    pid = process.pid
                    self.logger.info(f"Killing running process for {agent_name} (PID: {pid})")
                except (AttributeError, ProcessLookupError):
                    self.logger.debug(f"Process for {agent_name} already terminated (no PID)")
                    pid = None

                try:
                    process.kill()
                    self.logger.debug(f"Process.kill() called for {agent_name}")
                except ProcessLookupError:
                    self.logger.debug(f"Process for {agent_name} already terminated during kill")
                except OSError as e:
                    self.logger.warning(f"OSError killing process for {agent_name}: {e}")

                if process in self._running_subprocesses:
                    try:
                        self._running_subprocesses.remove(process)
                        self.logger.debug(f"Removed {agent_name} process from running subprocesses list")
                    except ValueError:
                        pass
            agent['process'] = None
        except Exception as e:
            self.logger.error(f"Unexpected error killing process for {agent_name}: {e}", exc_info=True)
            agent['process'] = None

    def get_agent_nickname(self, agent_name: str) -> str | None:
        """Get agent nickname (or None if not set)."""
        agent = self.agents.get(agent_name)
        if not agent:
            return None
        nickname = agent.get('nickname')
        return nickname

    def set_agent_nickname(self, agent_name: str, nickname: str | None) -> None:
        """Set agent nickname and persist via registry."""
        agent = self.agents.get(agent_name)
        if not agent:
            self.logger.warning(f"Attempted to set nickname for unknown agent '{agent_name}'")
            return

        if nickname is not None:
            nickname = nickname.strip() or None

        agent_id = agent.get('agent_id')
        profile = self.agent_profiles.get(agent_id) if agent_id else None
        agent['nickname'] = nickname
        if profile:
            profile.overrides.nickname = nickname
            self.logger.trace(
                "Members override persisted: %s.nickname=%r",
                agent_id or agent_name,
                nickname,
            )

        if agent_id:
            self._create_task(
                self.agent_registry.patch_overrides(
                    agent_id,
                    {"nickname": nickname},
                )
            )

        self._refresh_participants_ui()
        self.logger.info(f"{agent_name} nickname set to: {nickname}")

    def get_agent_avatar(self, agent_name: str) -> str | None:
        """Return current avatar for agent (or None if unknown)."""
        agent = self.agents.get(agent_name)
        if not agent:
            return None
        avatar = agent.get('avatar')
        return avatar

    def set_agent_avatar(self, agent_name: str, avatar: str | None) -> None:
        """Set agent avatar emoji and persist via registry."""
        agent = self.agents.get(agent_name)
        if not agent:
            self.logger.warning("Attempted to set avatar for unknown agent '%s'", agent_name)
            return

        agent_id = agent.get('agent_id')
        profile = self.agent_profiles.get(agent_id) if agent_id else None

        normalized = self._normalize_avatar_value(avatar)
        stored_avatar = normalized or ""

        descriptor_default = ""
        if profile and isinstance(profile.descriptor.metadata, dict):
            meta_value = profile.descriptor.metadata.get("avatar")
            if isinstance(meta_value, str) and meta_value.strip():
                descriptor_default = meta_value.strip()

        color = agent.get('color') or agent.get('color_default') or self.get_default_agent_color()
        display_avatar = normalized or descriptor_default or self._color_to_emoji(color)

        current_override = profile.overrides.avatar if profile else None

        # Skip persistence if nothing changed
        if (
            normalized == current_override
            and agent.get('avatar') == stored_avatar
            and agent.get('avatar_default') == display_avatar
        ):
            self.logger.debug("Members skip avatar (unchanged): %s -> %s", agent_name, normalized)
            return

        agent['avatar'] = stored_avatar
        agent['avatar_default'] = display_avatar
        if profile:
            profile.overrides.avatar = normalized
            self.logger.trace(
                "Members override persisted: %s.avatar=%r",
                agent_id or agent_name,
                normalized,
            )

        if agent_id:
            self._create_task(
                self.agent_registry.patch_overrides(
                    agent_id,
                    {"avatar": normalized},
                )
            )

        self._refresh_participants_ui()
        self.logger.info("%s avatar set to: %s", agent_name, display_avatar)

    def get_user_nickname(self) -> str | None:
        """Get user's nickname (or None if not set)."""
        return self.user_nickname

    def set_user_nickname(self, nickname: str | None) -> None:
        """Set user's nickname and persist to global settings."""
        # Normalize empty string to None
        if nickname is not None and not nickname.strip():
            nickname = None

        # Update in memory
        self.user_nickname = nickname

        # Persist to settings
        self._save_user_settings()

        # Update title to reflect new nickname
        self._refresh_participants_ui()

        self.logger.info(f"User nickname set to: {nickname}")

    def get_user_avatar(self) -> str:
        """Return avatar emoji for the local user."""
        avatar = self.user_avatar
        if avatar and avatar.strip():
            return avatar.strip()
        return "👤"

    def set_user_avatar(self, avatar: str | None) -> None:
        """Set user's avatar emoji and persist to global settings."""
        normalized = self._normalize_avatar_value(avatar)
        if normalized == self.user_avatar:
            self.logger.debug("User avatar unchanged: %s", normalized)
            return

        self.user_avatar = normalized
        self._save_user_settings()
        self._refresh_participants_ui()

        display_avatar = normalized or "🟢"
        self.logger.info("User avatar set to: %s", display_avatar)

    def get_user_color(self, *, override_only: bool = False) -> str | None:
        if override_only:
            return self.user_color
        return self.user_color or self.get_default_user_color()

    def set_user_color(self, color_value: str | None) -> None:
        normalized = self._normalize_color_value(color_value) if color_value else None
        if color_value and normalized is None:
            self.logger.warning("Invalid user color specified: %s", color_value)
            return

        if normalized == self.user_color or (normalized is None and self.user_color is None):
            self.logger.debug("User color unchanged: %s", normalized)
            return

        self.user_color = normalized
        self._save_user_settings()
        self._refresh_participants_ui()

        display_color = self.get_user_color()
        self.logger.info("User color set to: %s", display_color)

    def get_agent_display_name(self, agent_name: str) -> str:
        """Get display name (nickname if set, otherwise canonical name). Works for both agents and User."""
        # Special case: User nickname
        if agent_name == "User":
            user_nick = self.get_user_nickname()
            return user_nick if user_nick else "User"

        # Agent nickname
        agent = self.agents.get(agent_name)
        if not agent:
            return agent_name
        nickname = agent.get('nickname')
        if nickname:
            return nickname
        display = agent.get('display_name')
        if display:
            return display
        return agent_name

    def get_agent_class(self, agent_name: str) -> str:
        agent = self.agents.get(agent_name, {})
        return agent.get('class_name') or "—"

    def get_agent_backend_id(self, agent_name: str) -> str | None:
        agent = self.agents.get(agent_name)
        if not agent:
            return None
        backend_id = agent.get('backend_id')
        if backend_id:
            return backend_id
        backend = agent.get('backend')
        if backend is not None:
            return backend.class_id
        return None

    def get_agent_color(self, agent_name: str, *, override_only: bool = False) -> str | None:
        agent = self.agents.get(agent_name)
        if not agent:
            return None
        if override_only:
            color_override = agent.get('color_override')
            return color_override or None
        color_effective = agent.get('color')
        if color_effective:
            return color_effective
        return agent.get('color_default') or self.get_default_agent_color()

    def list_backend_options(self) -> list[tuple[str, str]]:
        options: list[tuple[str, str]] = []
        for backend in self.backend_registry.list_backends():
            label = backend.display_name or backend.class_id.capitalize()
            options.append((label, backend.class_id))
        return options

    def get_default_backend_id(self) -> str | None:
        default_backend = self.backend_registry.get_default_backend()
        return default_backend.class_id if default_backend else None

    def set_agent_backend(self, agent_name: str, backend_id: str) -> None:
        agent = self.agents.get(agent_name)
        if not agent:
            self.logger.warning("Attempted to set backend for unknown agent '%s'", agent_name)
            return

        backend = self.backend_registry.get_backend(backend_id)
        if backend is None:
            self.logger.warning("Attempted to set unknown backend '%s' for %s", backend_id, agent_name)
            return

        normalized_backend_id = backend.class_id
        agent['backend'] = backend
        agent['backend_id'] = normalized_backend_id
        agent['class_name'] = backend.display_name or normalized_backend_id

        agent_id = agent.get('agent_id')
        profile = self.agent_profiles.get(agent_id) if agent_id else None
        if profile:
            profile.overrides.backend_id = normalized_backend_id
            self.logger.trace(
                "Members override persisted: %s.backend_id=%s",
                agent_id or agent_name,
                normalized_backend_id,
            )

        if agent_id:
            self._create_task(
                self.agent_registry.patch_overrides(
                    agent_id,
                    {"backend_id": normalized_backend_id},
                )
            )

        backend_label = backend.display_name or backend.class_id
        self.logger.info("%s backend set to: %s", agent_name, backend_label)

    def set_agent_color(self, agent_name: str, color_value: str | None) -> None:
        agent = self.agents.get(agent_name)
        if not agent:
            self.logger.warning("Attempted to set color for unknown agent '%s'", agent_name)
            return

        normalized = self._normalize_color_value(color_value) if color_value else None
        if color_value and normalized is None:
            self.logger.warning("Invalid color value for %s: %s", agent_name, color_value)
            return

        agent_id = agent.get('agent_id')
        profile = self.agent_profiles.get(agent_id) if agent_id else None

        color_default = agent.get('color_default') or (profile.descriptor.color if profile and profile.descriptor.color else None)
        color_default_normalized = self._normalize_color_value(color_default) if color_default else None
        if color_default_normalized is None:
            color_default_normalized = self.get_default_agent_color()

        color_effective = normalized or color_default_normalized

        if (
            agent.get('color') == color_effective
            and (agent.get('color_override') or "") == (normalized or "")
            and agent.get('color_default') == color_default_normalized
        ):
            self.logger.debug("Members skip color (unchanged): %s -> %s", agent_name, color_effective)
            return

        agent['color'] = color_effective
        agent['color_override'] = normalized or ""
        agent['color_default'] = color_default_normalized

        descriptor_avatar = ""
        if profile and isinstance(profile.descriptor.metadata, dict):
            meta_value = profile.descriptor.metadata.get("avatar")
            if isinstance(meta_value, str) and meta_value.strip():
                descriptor_avatar = meta_value.strip()

        stored_avatar = agent.get('avatar') or ""
        agent['avatar_default'] = stored_avatar or descriptor_avatar or self._color_to_emoji(color_effective)

        if profile:
            profile.overrides.color = normalized
            self.logger.trace(
                "Members override persisted: %s.color=%r",
                agent_id or agent_name,
                normalized,
            )

        if agent_id:
            self._create_task(
                self.agent_registry.patch_overrides(
                    agent_id,
                    {"color": normalized},
                )
            )

        if agent_name in self.agents:
            # Update any Rich components relying on agent colours
            self._refresh_participants_ui()

        self.logger.info("%s color set to: %s", agent_name, color_effective)

    @staticmethod
    def get_default_agent_color() -> str:
        return "#D0D0D0"

    @staticmethod
    def get_default_user_color() -> str:
        return "#D0D0D0"

    @staticmethod
    def _normalize_color_value(value: str | None) -> str | None:
        if value is None:
            return None
        trimmed = value.strip()
        if not trimmed:
            return None
        if trimmed.startswith("#"):
            hex_part = trimmed[1:]
        else:
            hex_part = trimmed
        if len(hex_part) != 6:
            return None
        try:
            int(hex_part, 16)
        except ValueError:
            return None
        return f"#{hex_part.upper()}"

    @staticmethod
    def _normalize_avatar_value(value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip()
        if not normalized:
            return None
        normalized = normalized.replace("\n", "").replace("\r", "")
        if len(normalized) > 4:
            normalized = normalized[:4]
        return normalized

    @staticmethod
    def _expand_command_path(path: str) -> str:
        if not path:
            return ""
        expanded = os.path.expandvars(path)
        expanded = os.path.expanduser(expanded)
        return expanded or path

    def get_agent_path(self, agent_name: str) -> str:
        agent = self.agents.get(agent_name, {})
        path = (agent.get('command_path') or "").strip()
        if path:
            return self._expand_command_path(path)
        executable = agent.get('executable') or ""
        if executable:
            return shutil.which(executable) or executable
        return ""

    def set_agent_path(self, agent_name: str, command_path: str | None) -> str:
        """Update CLI command path for an agent; empty value falls back to default executable."""
        agent = self.agents.get(agent_name)
        if not agent:
            self.logger.warning("Attempted to set command path for unknown agent '%s'", agent_name)
            return ""

        executable = agent.get('executable') or ""
        normalized = (command_path or "").strip()
        if normalized:
            expanded = self._expand_command_path(normalized)
            effective = expanded or normalized
            agent['command_path'] = effective
            persisted = normalized
            if expanded != normalized:
                self.logger.debug(
                    "Resolved command path for %s: %s → %s",
                    agent_name,
                    normalized,
                    effective,
                )
        else:
            auto_detected = shutil.which(executable) if executable else None
            effective = auto_detected or executable
            agent['command_path'] = effective
            persisted = None

        agent_id = agent.get('agent_id')
        profile = self.agent_profiles.get(agent_id) if agent_id else None
        if profile:
            profile.overrides.command_path = persisted
            self.logger.trace(
                "Members override persisted: %s.command_path=%r",
                agent_id or agent_name,
                persisted,
            )

        if agent_id:
            self._create_task(
                self.agent_registry.patch_overrides(
                    agent_id,
                    {"command_path": persisted},
                )
            )

        display_path = effective or "unset"
        self.logger.info("%s command path set to: %s", agent_name, display_path)
        return effective

    def get_agent_role(self, agent_name: str) -> str | None:
        return self.agents.get(agent_name, {}).get('role_id')

    def set_agent_role(self, agent_name: str, role_id: str | None) -> None:
        agent = self.agents.get(agent_name)
        if not agent:
            self.logger.warning(f"Attempted to assign role to unknown agent '{agent_name}'")
            return

        new_role_id = role_id or None
        role_name = None
        if new_role_id:
            role = self.role_manager.get_role(new_role_id)
            if role is None:
                self.logger.warning(f"Attempted to assign unknown role '{new_role_id}' to {agent_name}")
                new_role_id = None
            else:
                role_name = role.name

        current_role = agent.get('role_id')
        if current_role == new_role_id:
            self.logger.debug(f"{agent_name} role unchanged ({new_role_id})")
            return

        agent['role_id'] = new_role_id
        agent_id = agent.get('agent_id')
        profile = self.agent_profiles.get(agent_id) if agent_id else None
        if profile:
            profile.overrides.role_id = new_role_id
            self.logger.trace(
                "Members override persisted: %s.role_id=%r",
                agent_id or agent_name,
                new_role_id,
            )

        if agent_id:
            self._create_task(
                self.agent_registry.patch_overrides(
                    agent_id,
                    {"role_id": new_role_id},
                )
            )

        if new_role_id is None:
            self.logger.info(f"{agent_name} role cleared")
        else:
            self.logger.info(f"{agent_name} role set to {role_name} ({new_role_id})")

        self._apply_role_prompt(agent_name, new_role_id, persist=False, force=True)
        self._save_user_settings()

    def _apply_role_prompt(
        self,
        agent_name: str,
        role_id: str | None,
        *,
        persist: bool = False,
        force: bool = False,
    ) -> None:
        """
        Ensure agent prompt is synced with its role when no workspace override exists.

        Args:
            agent_name: Display name key for the agent.
            role_id: Role identifier or None.
            persist: If True, persist prompt changes to workspace files.
            force: Apply prompt regardless of existing overrides.
        """
        if not force and agent_name in self.agent_prompts and agent_name not in self._agents_without_prompt:
            return

        prompt_text: str | None = None
        if role_id:
            try:
                role = self.role_manager.get_role(role_id)
                if role is None:
                    self.logger.warning(
                        "Role %s not found while applying prompt to %s; using default prompt",
                        role_id,
                        agent_name,
                    )
                else:
                    prompt_text = role.prompt or ""
            except Exception as exc:
                self.logger.error(
                    "Failed to load prompt for role %s while applying to %s: %s",
                    role_id,
                    agent_name,
                    exc,
                    exc_info=True,
                )
                prompt_text = None

        if prompt_text is not None and not prompt_text.strip():
            prompt_text = None

        self.set_agent_system_prompt(agent_name, prompt_text, persist=persist)

    def _get_chat_log(self) -> ChatLog | None:
        """Safely return chat log widget if present."""
        try:
            return self.query_one(ChatLog)
        except NoMatches:
            self.logger.debug("Chat log not available for UI update")
            return None

    def _get_agent_prompt_file(self, agent_name: str) -> Path:
        """Return path to workspace-specific prompt file for the given agent."""
        agent_dir = self._prompts_root / agent_name.lower()
        return agent_dir / "prompt.txt"

    def agent_prompt_exists(self, agent_name: str) -> bool:
        """Return True if workspace-specific prompt file exists for agent."""
        return self._get_agent_prompt_file(agent_name).exists()

    def _ensure_agent_prompt_dir(self, agent_name: str) -> Path:
        """Ensure directory for agent prompt exists and return it."""
        agent_dir = self._prompts_root / agent_name.lower()
        agent_dir.mkdir(parents=True, exist_ok=True)
        return agent_dir

    def _load_workspace_prompts(self) -> None:
        """Load existing workspace-specific prompts from disk into cache."""
        self.logger.trace("ConsiliumAgentTUI:loading workspace prompts from %s", self._prompts_root)
        loaded = 0
        self._agents_without_prompt.clear()
        for agent_name in self.agents.keys():
            text = self._load_agent_prompt_from_disk(agent_name)
            if text is None:
                self._agents_without_prompt.add(agent_name)
                continue
            self.set_agent_system_prompt(agent_name, text, persist=False)
            loaded += 1
            prompt_path = self._get_agent_prompt_file(agent_name)
            self.logger.info(f"Loaded workspace prompt for {agent_name} from {prompt_path}")
        if loaded:
            self.logger.debug(f"Workspace prompts loaded: {loaded}")
        self.logger.trace("ConsiliumAgentTUI:workspace prompts load finished count=%d", loaded)

    def _load_agent_prompt_from_disk(self, agent_name: str) -> str | None:
        """Return prompt text from disk if available."""
        prompt_path = self._get_agent_prompt_file(agent_name)
        if not prompt_path.exists():
            return None
        try:
            text = prompt_path.read_text(encoding='utf-8')
        except Exception:
            self.logger.exception(f"Failed to read prompt for {agent_name} ({prompt_path})")
            return None
        if not text.strip():
            self.logger.info(f"Workspace prompt for {agent_name} is empty, removing file")
            try:
                prompt_path.unlink()
            except Exception:
                self.logger.exception(f"Failed to remove empty prompt for {agent_name}")
            return None
        return text

    def _save_agent_prompt_to_disk(self, agent_name: str, prompt: str | None) -> None:
        """Persist prompt text to disk (or remove file if text is empty)."""
        prompt_path = self._get_agent_prompt_file(agent_name)
        if prompt is None or not prompt.strip():
            if prompt_path.exists():
                try:
                    prompt_path.unlink()
                    self.logger.info(f"Removed workspace prompt for {agent_name} ({prompt_path})")
                except Exception as exc:
                    self.logger.exception(f"Failed to remove prompt for {agent_name}")
            return

        self._ensure_agent_prompt_dir(agent_name)
        try:
            prompt_path.write_text(prompt, encoding='utf-8')
            self.logger.info(f"Saved workspace prompt for {agent_name} ({prompt_path})")
        except Exception:
            self.logger.exception(f"Failed to save prompt for {agent_name}")

    # ---------------------------------------------------------------------
    # Role management helpers
    # ---------------------------------------------------------------------

    def get_roles(self, refresh: bool = False) -> list[Role]:
        if refresh:
            self.role_manager.reload()
        return self.role_manager.list_roles()

    def add_member_placeholder(self) -> asyncio.Task[AgentProfile] | None:
        backend = self.backend_registry.get_default_backend()
        if backend is None:
            self.logger.error("Cannot add member: no agent backends registered")
            self.add_error("Cannot add member: no agent classes available.", None)
            return None

        display_name = self._generate_new_member_display_name()
        handler_id = backend.class_id
        class_name = backend.display_name or handler_id.capitalize()
        default_executable = handler_id
        description = f"Custom agent ({class_name})"

        overrides = AgentOverrides()
        overrides.backend_id = handler_id

        roles = self.get_roles(refresh=False)
        initial_role = roles[0].role_id if roles else None
        if initial_role:
            overrides.role_id = initial_role

        self.logger.info(
            "Creating placeholder member display_name=%s backend=%s role=%s",
            display_name,
            handler_id,
            initial_role,
        )

        task: asyncio.Task[AgentProfile] = self._create_task(
            self.agent_registry.create_member(
                display_name=display_name,
                handler=handler_id,
                class_name=class_name,
                default_executable=default_executable,
                description=description,
                color=None,
                default_role=initial_role,
                default_enabled=True,
                overrides=overrides,
                metadata={},
            )
        )

        def _on_created(done: asyncio.Task[AgentProfile]) -> None:
            try:
                profile = done.result()
            except asyncio.CancelledError:
                self.logger.debug("Add member task cancelled for %s", display_name)
                return
            except Exception as exc:
                self.logger.error("Failed to create member %s: %s", display_name, exc, exc_info=True)
                self.add_error(f"Failed to add member: {exc}", None)
                return

            descriptor = profile.descriptor
            self.add_status(STATUS_MEMBER_ADDED.format(display_name=descriptor.display_name))

        task.add_done_callback(_on_created)
        return task

    def remove_member(self, agent_id: str) -> asyncio.Task[bool] | None:
        profile = self.agent_profiles.get(agent_id)
        if profile is None:
            self.logger.warning("Attempted to remove unknown member '%s'", agent_id)
            self.add_error(ERROR_MEMBER_UNKNOWN.format(agent_id=agent_id), None)
            return None

        display_name = profile.descriptor.display_name
        self.logger.info("Removing member %s (%s)", display_name, agent_id)

        task: asyncio.Task[bool] = self._create_task(
            self.agent_registry.delete_member(agent_id)
        )

        def _on_removed(done: asyncio.Task[bool]) -> None:
            try:
                removed = done.result()
            except asyncio.CancelledError:
                self.logger.debug("Remove member task cancelled for %s", display_name)
                return
            except Exception as exc:
                self.logger.error("Failed to delete member %s: %s", display_name, exc, exc_info=True)
                self.add_error(ERROR_MEMBER_REMOVE.format(display_name=display_name, exc=exc), None)
                return

            if removed:
                self.add_status(STATUS_MEMBER_REMOVED.format(display_name=display_name))
            else:
                self.add_error(ERROR_MEMBER_NOT_FOUND.format(display_name=display_name), None)

        task.add_done_callback(_on_removed)
        return task

    def _generate_new_member_display_name(self) -> str:
        base = "New member"
        existing: set[str] = set()
        for profile in self.agent_profiles.values():
            existing.add(profile.descriptor.display_name.lower())
            override_name = profile.overrides.display_name
            if isinstance(override_name, str) and override_name.strip():
                existing.add(override_name.strip().lower())

        candidate = base
        index = 2
        while candidate.lower() in existing:
            candidate = f"{base} {index}"
            index += 1
        return candidate

    def get_role(self, role_id: str) -> Role | None:
        return self.role_manager.get_role(role_id)

    def create_role(self, name: str) -> Role:
        role = self.role_manager.create_role(name)
        return role

    def save_role_prompt(self, role_id: str, text: str) -> None:
        self.role_manager.save_prompt(role_id, text)

    def load_role_prompt(self, role_id: str) -> str:
        return self.role_manager.load_prompt(role_id)

    def rename_role(self, role_id: str, name: str) -> None:
        self.role_manager.rename_role(role_id, name)

    def delete_role(self, role_id: str) -> list[str]:
        """
        Delete role and unassign from all agents that use it.

        Returns:
            List of agent names that were using this role and got reset to None.
        """
        affected_agents: list[str] = []

        # Find all agents using this role and reset them
        for agent_name, agent in self.agents.items():
            if agent.get('role_id') == role_id:
                affected_agents.append(agent_name)
                self.set_agent_role(agent_name, None)
                self.logger.info("Reset role for agent %s due to role deletion", agent_name)

        # Delete the role itself
        self.role_manager.delete_role(role_id)
        self.logger.info("Deleted role %s, affected %d agents", role_id, len(affected_agents))

        return affected_agents

    def _get_agent_system_prompt(self, agent_name: str, *, is_init: bool = False) -> str:
        """
        Return system prompt for a given agent.

        Init prompt combines role prompt + init greeting instructions.
        Regular messages use custom/role prompts or fall back to system prompt.
        """
        if is_init:
            # Get agent's role prompt if assigned
            role_id = self.get_agent_role(agent_name)
            role_prompt = ""
            if role_id:
                try:
                    role = self.role_manager.get_role(role_id)
                    if role and role.prompt:
                        role_prompt = role.prompt.strip()
                except Exception as exc:
                    self.logger.warning("Failed to load role %s for init prompt: %s", role_id, exc)

            # Combine: role prompt + init instructions
            if role_prompt:
                return f"{role_prompt}\n\n{self.init_prompt}"
            return self.init_prompt

        custom_prompt = self.agent_prompts.get(agent_name)
        if custom_prompt is None:
            if agent_name in self._agents_without_prompt:
                self.logger.trace(f"ConsiliumAgentTUI:default prompt used for {agent_name}")
                return self.system_prompt
            custom_prompt = self._load_agent_prompt_from_disk(agent_name)
            if custom_prompt:
                self.set_agent_system_prompt(agent_name, custom_prompt, persist=False)
                self.logger.trace(f"Workspace prompt applied for {agent_name}")
            else:
                self._agents_without_prompt.add(agent_name)
                self.logger.trace(f"ConsiliumAgentTUI:prompt missing; default fallback for {agent_name}")
                return self.system_prompt
        return custom_prompt

    def set_agent_system_prompt(
        self,
        agent_name: str,
        prompt: str | None,
        *,
        persist: bool = True
    ) -> None:
        """Update cached system prompt for agent (None -> remove override)."""
        text = None if prompt is None else str(prompt)

        if text is None or not text.strip():
            had_prompt = agent_name in self.agent_prompts
            self.agent_prompts.pop(agent_name, None)
            already_missing = agent_name in self._agents_without_prompt
            self._agents_without_prompt.add(agent_name)
            if had_prompt:
                self.logger.trace(f"Workspace prompt cleared for {agent_name}")
            elif not already_missing:
                self.logger.debug(f"Agent prompt cleared (was empty): {agent_name}")
            if persist:
                self._save_agent_prompt_to_disk(agent_name, None)
            return

        current = self.agent_prompts.get(agent_name)
        if current == text:
            self.logger.debug(f"Agent prompt unchanged for {agent_name}")
            self._agents_without_prompt.discard(agent_name)
            if persist:
                self._save_agent_prompt_to_disk(agent_name, text)
            return

        self.agent_prompts[agent_name] = text
        self._agents_without_prompt.discard(agent_name)
        self.logger.trace(f"Workspace prompt updated for {agent_name} ({len(text)} chars)")
        if persist:
            self._save_agent_prompt_to_disk(agent_name, text)

    @staticmethod
    def _compose_style(color: str | None, *, bold: bool = False, dim: bool = False, italic: bool = False) -> str | None:
        """Build a Rich style string supporting named and hex colours."""
        parts: list[str] = []
        if bold:
            parts.append("bold")
        if dim:
            parts.append("dim")
        if italic:
            parts.append("italic")
        if color:
            parts.append(color)
        if not parts:
            return None
        return " ".join(parts)

    def compose(self) -> ComposeResult:
        """Create UI components"""
        self.logger.trace("ConsiliumAgentTUI:compose building UI components")
        participants_header = ParticipantsHeader(id="participants-header")
        self._participants_header = participants_header
        if self._pending_header_text is not None:
            participants_header.update_text(self._pending_header_text)
            self._pending_header_text = None
        else:
            participants_header.update_text(self._render_participants_header_text())
        yield participants_header
        yield ChatLog(id="chat-log", markup=False, wrap=True, min_width=1, max_lines=2000)
        yield ChatComposer()
        yield AnimatedStatusBar(STATUS_READY, id="status-bar")
        yield ConsiliumFooter(show_command_palette=False)

    def _ensure_exception_handler(self) -> None:
        """Install asyncio exception handler once."""
        if self._asyncio_handler_installed:
            return
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            # Event loop not ready; try again after current tick
            self.call_later(self._ensure_exception_handler)
            return

        def handler(loop: asyncio.AbstractEventLoop, context: dict) -> None:
            err = context.get("exception")
            if err is not None:
                self.logger.error(
                    "Asyncio exception captured: %s context=%s",
                    err,
                    context,
                    exc_info=True,
                )
            else:
                self.logger.error("Asyncio exception captured without exception context=%s", context)
                message = context.get("message")
                if message:
                    self.logger.error("Asyncio exception detail: %s", message)

        loop.set_exception_handler(handler)
        self._asyncio_handler_installed = True
        self.logger.trace("ConsiliumAgentTUI:asyncio exception handler installed")

    def _handle_exception(self, error: Exception) -> None:
        """Log synchronous exceptions before Textual shuts the app down."""
        if self._shutdown_trigger is None:
            self._shutdown_trigger = f"unhandled exception ({type(error).__name__})"
        self.logger.exception("Unhandled exception in ConsiliumAgentTUI", exc_info=error)
        super()._handle_exception(error)

    async def handle_exception(self, error: Exception) -> None:  # type: ignore[override]
        """Log any unhandled exception with full traceback."""
        self.logger.exception("Unhandled exception in ConsiliumAgentTUI")
        self._shutdown_trigger = f"unhandled exception ({type(error).__name__})"
        try:
            hint = f"{ERROR_CRITICAL.format(error=error)}\nLog: {log_file_path}"
            self.add_error(hint, None)
        except Exception:
            pass
        try:
            self.add_status(f"Error details logged to: {log_file_path}")
        except Exception:
            pass
        await super().handle_exception(error)

    def on_mount(self) -> None:
        """Initialize after mounting"""
        self._ensure_exception_handler()
        self.logger.trace("ConsiliumAgentTUI:on_mount start")
        try:
            self._refresh_participants_ui()
            chat_log = self.query_one(ChatLog)

            # Restore chat history
            persisted_history = self.session_manager.load_history()
            history_count = len(persisted_history)
            if persisted_history:
                self.logger.info(f"Restoring {history_count} messages from history")
                for msg in persisted_history:
                    self._display_historical_message(msg)

            self._focus_composer()
            self._update_step_footer_color()
            self._sync_step_binding_label()

            if history_count == 0:
                self._start_gate_active = True
                self._start_gate_event.clear()

                def show_first_launch_status() -> None:
                    self.add_status(STATUS_FIRST_LAUNCH)

                    def remind_ctrl_g() -> None:
                        self.add_status(STATUS_PRESS_CTRL_G)

                    self.call_later(remind_ctrl_g)

                self.call_later(show_first_launch_status)
                self.logger.info("UI mounted, awaiting kickoff (Ctrl+G)")
            else:
                self._start_gate_active = False
                self._start_gate_event.set()
                self.logger.info("UI mounted, starting agents...")
                self.call_later(self.start_agents)
                self.logger.trace("ConsiliumAgentTUI:on_mount scheduled agent startup")

            if self._registry_listener_task is None or self._registry_listener_task.done():
                self._registry_listener_task = self._create_task(
                    self._subscribe_registry_events()
                )
        except Exception as exc:
            self.logger.exception("on_mount failed")
            raise

    def _display_historical_message(self, msg: dict) -> None:
        """Replay a historical message using the standard rendering pipeline."""
        role = msg.get('role')
        content = str(msg.get('content') or "")
        if not content:
            return

        if role == 'user':
            author = 'User'
        elif role == 'assistant':
            author = msg.get('agent', 'Assistant')
        else:
            author = 'System'

        msg_id = msg.get('msg_id')
        reply_to = msg.get('reply_to', msg.get('replyto'))

        # Mirror journal storage for previews
        history_entry = {
            'msg_id': msg_id,
            'author': author,
            'text': content,
            'reply_to': reply_to,
        }
        self.history.append(history_entry)
        if len(self.history) > self.history_limit:
            self.history = self.history[-self.history_limit:]

        metadata: dict[str, Any] = {}
        if isinstance(msg_id, int):
            metadata['msg_id'] = msg_id
        elif isinstance(msg_id, str) and msg_id.strip().isdigit():
            metadata['msg_id'] = int(msg_id.strip())

        if isinstance(reply_to, int):
            metadata['replyto'] = reply_to
        elif isinstance(reply_to, str):
            stripped = reply_to.strip()
            if stripped.isdigit():
                metadata['replyto'] = int(stripped)

        self.add_message(author, content, metadata=metadata or None)

    def start_agents(self) -> None:
        """Initialize agents"""
        self.logger.info("Initializing agents...")
        self.add_status(STATUS_STARTING_AGENTS)

        if self._shutting_down:
            self.logger.debug("Skipping agent initialization during shutdown")
            return

        # Start agents in background (no chat display)
        self._create_task(self.init_agents())
        self.logger.trace("ConsiliumAgentTUI:start_agents task queued")

    async def init_agents(self):
        """Asynchronous agent initialization without forwarding."""
        self.logger.trace("ConsiliumAgentTUI:init_agents begin")
        if self._shutting_down:
            self.logger.debug("Shutdown in progress; skipping agent init sequence")
            return

        # Check if we're restoring an existing session
        persisted_history = self.session_manager.load_history()
        has_existing_session = len(persisted_history) > 0

        if not has_existing_session:
            # New session - send introduction prompt
            self.logger.info("New session - sending introduction prompt")
            init_text = "Hello! Introduce yourself briefly (one sentence)."
            await self.process_message('User', init_text, is_init=True)
        else:
            self.logger.info("Restoring existing session - skipping introduction")

        self.logger.info("Agents connected and ready")
        self.add_status(STATUS_READY)
        for agent_name, agent_config in self.agents.items():
            self.logger.debug(f"{agent_name} session: {agent_config['session_id']}")
        self.logger.trace("ConsiliumAgentTUI:init_agents complete")

    @staticmethod
    def _is_silent_response_text(text: str | None) -> bool:
        """Determine whether the response should be treated as agent silence."""
        if text is None:
            return True
        normalized = str(text).strip()
        if not normalized:
            return True
        normalized = re.sub(r"\s+", "", normalized)
        if not normalized:
            return True
        return bool(SILENT_RESPONSE_PATTERN.fullmatch(normalized))

    @staticmethod
    def _normalize_text_for_display(text: str) -> str:
        """
        Normalize text for RichLog display with smart code block detection.

        Problem: RichLog may truncate text after double newlines.
        Solution: Preserve double newlines inside markdown code blocks (```),
                 normalize them elsewhere to prevent UI truncation.
        """
        if '```' not in text:
            # Fast path: no code blocks, safe to normalize
            return text.replace('\n\n', '\n')

        # Split text into segments by code block markers
        segments = []
        current_pos = 0
        in_code_block = False

        while current_pos < len(text):
            marker_pos = text.find('```', current_pos)

            if marker_pos == -1:
                # No more markers, take the rest
                segment = text[current_pos:]
                if in_code_block:
                    # Inside code block - preserve formatting
                    segments.append(segment)
                else:
                    # Outside code block - normalize
                    segments.append(segment.replace('\n\n', '\n'))
                break

            # Found marker - process segment before it
            segment = text[current_pos:marker_pos]
            if in_code_block:
                # Inside code block - preserve formatting
                segments.append(segment)
            else:
                # Outside code block - normalize
                segments.append(segment.replace('\n\n', '\n'))

            # Add the marker itself
            segments.append('```')

            # Toggle state and move past marker
            in_code_block = not in_code_block
            current_pos = marker_pos + 3

        return ''.join(segments)

    def _publish_entry_from_courier(self, entry: JournalEntry, is_error: bool) -> None:
        """Publish a journal entry to history and chat."""
        author = entry.author
        text = entry.text
        reply_to = entry.metadata.get('replyto') if entry.metadata else None

        self.history.append({
            'msg_id': entry.id,
            'author': author,
            'text': text,
            'reply_to': reply_to,
        })
        if len(self.history) > self.history_limit:
            self.history = self.history[-self.history_limit:]

        history_kwargs = {
            'content': text,
            'msg_id': entry.id,
            'reply_to': reply_to,
            'display_name': author,
        }

        if author == 'User':
            self.session_manager.append_to_history(
                role='user',
                **history_kwargs,
            )
        elif author in self.agents:
            self.session_manager.append_to_history(
                role='assistant',
                agent=author,
                **history_kwargs,
            )

        if is_error:
            self.logger.warning(
                "Agent %s returned error-marked content; forwarding to chat",
                author if author in self.agents else "Unknown",
            )

        self.add_message(author, text, metadata=entry.metadata)

    def add_message(
        self,
        author: str,
        text: str,
        style_override: str = None,
        metadata: dict[str, Any] | None = None,
    ):
        """Add message to chat"""
        chat_log = self._get_chat_log()
        if chat_log is None:
            self.logger.debug(f"Skipping UI message from {author}: {text}")
            return

        # Clear status bar when real message appears in chat
        self.add_status("")

        if author == 'System':
            style = style_override or "bright_white"
            text = self._normalize_text_for_display(text)
            self._write_chat(Text(text, style=style))
            return

        # Color from agent registry or default
        if style_override:
            color = style_override
        elif author == 'User':
            color = self.get_user_color()
        elif author in self.agents:
            agent_entry = self.agents[author]
            color = agent_entry.get('color') or agent_entry.get('color_default') or self.get_default_agent_color()
        else:
            color = self.get_default_agent_color()

        # Normalize and decorate message for chat display
        text = self._normalize_text_for_display(text)

        # For User messages: convert single newlines to Markdown hard breaks (two spaces + newline)
        # This preserves line breaks from Ctrl+J while keeping Markdown rendering
        if author == 'User':
            text = text.replace('\n', '  \n')

        text = self._format_context_headers(text)
        preview = self._build_reply_preview(metadata)
        if preview:
            text = f"{preview}\n\n{text}"
        text = self._highlight_mentions(text)

        # Emoji prefix
        if author == 'User':
            emoji_symbol = self.get_user_avatar()
        elif author in self.agents:
            emoji_symbol = self._resolve_agent_avatar(author)
            color = self.agents[author].get('color', '#ffffff')
        else:
            emoji_symbol = ""

        emoji = f"{emoji_symbol} " if emoji_symbol else ""

        # Use display name (nickname if set, otherwise canonical name)
        # Handle both agents and User
        if author == 'User':
            display_name = self.get_agent_display_name("User")
        elif author in self.agents:
            display_name = self.get_agent_display_name(author)
        else:
            display_name = author

        # Check if message is secret
        is_secret = metadata and metadata.get('status') == 'secret'

        # Format header with markdown bold for all messages
        if is_secret:
            header_md = f"🔒 **{emoji}[{display_name}]:**"
        else:
            header_md = f"**{emoji}[{display_name}]:**"

        # Combine header and text (insert extra newline to keep markdown blocks intact)
        leading = text.lstrip()
        separator = "\n\n" if leading.startswith("```") else "\n"
        full_text = f"{header_md}{separator}{text}"

        try:
            message = Markdown(full_text, code_theme="monokai", style=self._compose_style(color))
        except TypeError:
            message = Markdown(full_text, style=self._compose_style(color))

        self._write_chat(Group(Text(""), message, Text("")))

    def add_tool_call(self, agent: str, tool_name: str, details: str = ""):
        """Add tool call to chat"""
        color_entry = self.agents.get(agent, {})
        color = color_entry.get('color') or color_entry.get('color_default') or self.get_default_agent_color()

        chat_log = self._get_chat_log()
        if chat_log is None:
            self.logger.debug(f"Tool call skipped in UI: {agent} -> {tool_name}")
            return

        # Use display name (nickname if set)
        display_name = self.get_agent_display_name(agent)

        msg = Text()
        msg.append(f"  🔧 {display_name}: ", style=self._compose_style(color, dim=True))
        msg.append(f"{tool_name}", style=self._compose_style(color, italic=True))
        if details:
            msg.append(
                f" ({details[:60]}...)" if len(details) > 60 else f" ({details})",
                style=self._compose_style(color, dim=True)
            )

        self._write_chat(msg)
        self.logger.debug(f"[{agent}] Tool call: {tool_name} | {details}")

    def add_thinking(self, agent: str, thought: str):
        """Add thinking process to chat"""
        chat_log = self._get_chat_log()
        if chat_log is None:
            self.logger.debug(f"Skipping thinking message for {agent}: {thought}")
            return
        # Use display name (nickname if set)
        display_name = self.get_agent_display_name(agent)
        # Simple dark gray italic text, no emoji, no color
        self._write_chat(Text(f"{display_name}: {thought}", style="italic #444444"))
        self.logger.debug(f"[{agent}] Thinking: {thought}")

    def _highlight_mentions(self, text: str) -> str:
        """Bold known @mentions (agents, user aliases, @all)."""
        alias_map = self._build_alias_map_for_mentions()

        def replacer(match: re.Match[str]) -> str:
            token = match.group(1)
            normalized = self._normalize_alias(token)
            if normalized in alias_map:
                return f"**@{token}**"
            return match.group(0)

        return re.sub(r'@([^\s@]+)', replacer, text)

    def _build_reply_preview(self, metadata: dict[str, Any] | None) -> str | None:
        """Return a Markdown quote with a short snippet of the replied-to message."""
        if not metadata:
            return None

        reply_to = metadata.get('replyto')
        if reply_to is None:
            reply_to = metadata.get('reply_to')
        if reply_to in (None, "", []):
            return None

        try:
            reply_id = int(reply_to)
        except (TypeError, ValueError):
            return None

        referenced_entry: dict[str, Any] | None = None
        for entry in reversed(self.history):
            if entry.get('msg_id') == reply_id:
                referenced_entry = entry
                break

        if not referenced_entry:
            return None

        quoted_author = referenced_entry.get('author', 'Unknown')
        if quoted_author == 'User':
            quoted_display = self.get_agent_display_name('User')
        elif quoted_author in self.agents:
            quoted_display = self.get_agent_display_name(quoted_author)
        else:
            quoted_display = quoted_author

        snippet = str(referenced_entry.get('text') or '').strip()
        if not snippet:
            return None

        snippet = re.sub(r'\s+', ' ', snippet)
        limit = 200
        truncated = snippet[:limit]
        if len(snippet) > limit:
            truncated = truncated.rstrip()
            truncated += "..."

        escaped = (
            truncated.replace("*", r"\*")
            .replace("_", r"\_")
            .replace("`", r"\`")
        )

        return f"> **{quoted_display}:** *{escaped}*"

    def _build_alias_map_for_mentions(self) -> dict[str, str]:
        """Return alias map for highlighting mentions."""
        alias_map: dict[str, str] = {
            "all": "__all__",
            "everyone": "__all__",
        }

        participants = ['User'] + list(self.agents.keys())
        for participant in participants:
            display = self.get_agent_display_name(participant)
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

    def _format_context_headers(self, text: str) -> str:
        """Format courier-style headers for readability."""
        if not text.startswith('[#'):
            return text

        lines = text.splitlines()
        new_lines: list[str] = []
        idx = 0

        if idx < len(lines):
            first = lines[idx]
            if 'from:' in first or 'to:' in first:
                first = re.sub(r'\[#(\d+)\]', lambda m: f"**[# {m.group(1)}]**", first)
                first = self._bold_header_token(first, 'from:')
                first = self._bold_header_token(first, 'to:')
                first = first.replace('**to:**', '\n**to:**', 1)
                new_lines.extend(first.splitlines())
                idx += 1
            else:
                new_lines.append(f"**{first}**")
                idx += 1

        while idx < len(lines):
            lower = lines[idx].lower()
            if lower.startswith('from:'):
                new_lines.append(self._bold_header_token(lines[idx], 'from:'))
            elif lower.startswith('to:'):
                new_lines.append(self._bold_header_token(lines[idx], 'to:'))
            else:
                break
            idx += 1

        # Append remaining lines untouched
        if idx < len(lines):
            new_lines.extend(lines[idx:])

        return '\n'.join(new_lines)

    @staticmethod
    def _bold_header_token(line: str, token: str) -> str:
        pattern = re.compile(re.escape(token), re.IGNORECASE)
        return pattern.sub(lambda m: f"**{m.group(0).lower()}**", line, count=1)

    def add_status(self, text: str):
        """Add status message to status bar with auto-clear timer for 'stayed silent' messages"""
        self._pending_status_text = text
        status_bar = self._status_bar_widget
        if status_bar is None:
            self.logger.debug("Status bar not yet mounted; queued status: %s", text)
            return

        status_bar.update_text(text)
        self.logger.debug(f"Status: {text}")

        # Schedule auto-clear for "stayed silent" messages
        self._schedule_status_clear(text)

    def _schedule_status_clear(self, text: str):
        """Schedule auto-clear timer for 'stayed silent' status messages (10 seconds)"""
        # Cancel existing timer if any
        if self._status_clear_timer is not None:
            self._status_clear_timer.stop()
            self._status_clear_timer = None

        # Only auto-clear "stayed silent" messages
        if "stayed silent" not in text.lower():
            return

        def check_and_clear_silent():
            """Check if status bar still shows 'stayed silent', then replace with 'Ready...'"""
            status_bar = self._status_bar_widget
            if status_bar is None:
                return

            current_text = getattr(status_bar, "_text", "")

            # Only clear if status still shows "stayed silent" pattern
            if "stayed silent" in current_text.lower():
                status_bar.update_text(STATUS_READY)
                self._pending_status_text = STATUS_READY
                self.logger.debug("Status auto-cleared (stayed silent → Ready...)")
            self._status_clear_timer = None

        # Set 10-second timer (non-blocking)
        self._status_clear_timer = self.set_timer(10.0, check_and_clear_silent)

    # ------------------------------------------------------------------
    # Status bar registration helpers
    # ------------------------------------------------------------------

    def register_status_bar(self, widget: AnimatedStatusBar) -> None:
        """Record status bar widget and flush any pending status text."""
        self._status_bar_widget = widget
        if self._pending_status_text is None:
            self._pending_status_text = getattr(widget, "_text", None) or STATUS_READY
        widget.update_text(self._pending_status_text)
        self.logger.trace("Status bar registered")

    def unregister_status_bar(self, widget: AnimatedStatusBar) -> None:
        """Forget status bar widget when it is unmounted."""
        if self._status_bar_widget is widget:
            self._status_bar_widget = None
            self.logger.trace("Status bar unregistered")

    def action_kickoff_chat(self) -> None:
        """Handle the one-time Ctrl+G startup command."""
        if not getattr(self, "_start_gate_active", False):
            self.logger.debug("Kickoff ignored: startup gate inactive")
            return
        if self._start_gate_event.is_set():
            self.logger.debug("Kickoff ignored: gate already released")
            return
        self.logger.info("Kickoff command received (Ctrl+G)")
        self._start_gate_event.set()
        self._start_gate_active = False
        self.add_status(STATUS_STARTING_AGENTS)
        self.call_later(self.start_agents)

    def add_error(self, text: str, agent: str | None = None):
        """Display an error message in the chat log."""
        chat_log = self._get_chat_log()
        if chat_log is None:
            self.logger.error(f"Error (UI unavailable): {text}")
            return
        normalized = self._normalize_text_for_display(text)
        msg = Text()
        prefix = "❌ ERROR: "
        msg.append(prefix, style="bold red")
        msg.append(normalized, style="red")
        self._write_chat(msg)
        if agent:
            self.logger.error(f"[{agent}] {normalized}")
        else:
            self.logger.error(f"Chat error displayed: {normalized}")

    async def _call_agent_cli(self, agent: str, cmd: list, event_parser, timeout: int = 1800):
        """Run agent CLI command safely and return (text, actions)."""

        lock = self._get_agent_lock(agent)
        async with lock:
            process = None
            stderr_task = None
            errors: list[tuple[str, bool]] = []

            def record_error(message: str, *, force: bool = False) -> None:
                normalized = str(message).strip()
                if normalized:
                    errors.append((normalized, force))

            def get_error_messages(return_code: int | None) -> list[str]:
                if not errors:
                    return []
                messages: list[str] = []
                for message, force in errors:
                    if force or (return_code is not None and return_code != 0):
                        messages.append(message)
                return messages

            try:
                self.logger.debug(f"[{agent}] Command: {' '.join(cmd[:6])}...")
                self.logger.trace("ConsiliumAgentTUI:_call_agent_cli start agent=%s", agent)

                if self._shutting_down or self._interrupt_requested:
                    self.logger.debug(f"[{agent}] Shutdown/interrupt in progress; skipping CLI call")
                    return None, set(), []

                process = await self._create_subprocess_exec(
                    *cmd,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                    cwd=str(self.workspace_root),
                    limit=STREAM_READER_LIMIT,
                )

                # Save process reference in agent config for potential killing
                if agent in self.agents:
                    self.agents[agent]['process'] = process

                stderr_task = self._create_task(process.stderr.read())

                async def read_stdout():
                    final_text = ""
                    actions = set()
                    non_json_buffer: list[str] = []

                    def flush_non_json_buffer() -> None:
                        if not non_json_buffer:
                            return
                        combined = " ".join(segment.strip() for segment in non_json_buffer if segment.strip())
                        non_json_buffer.clear()
                        if not combined:
                            return
                        lowered = combined.lower()
                        keywords = ("error ", "error:", "failed", "exception", "resource exhausted", "non utf-8", "json decode")
                        force = any(keyword in lowered for keyword in keywords) or "error code" in lowered or "status code" in lowered
                        if force:
                            record_error(combined, force=True)

                    def append_non_json_line(raw_line: str) -> None:
                        stripped_line = raw_line.strip()
                        if stripped_line:
                            non_json_buffer.append(stripped_line)

                    try:
                        while True:
                            # Check if agent was disabled during processing
                            if agent in self.agents and not self.agents[agent].get('enabled', True):
                                self.logger.info(f"[{agent}] Agent was disabled during processing, stopping read loop")
                                # Kill process if it's still running (defensive check)
                                if process and process.returncode is None:
                                    try:
                                        process.kill()
                                        self.logger.debug(f"[{agent}] Killed process from read loop")
                                    except (ProcessLookupError, OSError) as e:
                                        self.logger.debug(f"[{agent}] Process already dead in read loop: {e}")
                                break

                            try:
                                line = await process.stdout.readline()
                            except ValueError as stream_error:
                                error_text = str(stream_error)
                                self.logger.error(f"[{agent}] Stream read error: {error_text}")
                                record_error(error_text, force=True)
                                break
                            except (BrokenPipeError, ConnectionResetError) as pipe_error:
                                # Process was killed, this is expected
                                self.logger.debug(f"[{agent}] Pipe broken (process was killed): {pipe_error}")
                                break
                            except Exception as read_error:
                                self.logger.error(f"[{agent}] Unexpected read error: {read_error}")
                                break
                            if not line:
                                break

                            try:
                                decoded_line = line.decode("utf-8")
                            except UnicodeDecodeError as decode_error:
                                self.logger.error(f"[{agent}] Non UTF-8 output: {decode_error}")
                                record_error(f"{agent}: non UTF-8 output ({decode_error})", force=True)
                                continue

                            try:
                                event = json.loads(decoded_line)
                                flush_non_json_buffer()
                            except json.JSONDecodeError as decode_error:
                                self.logger.error(f"[{agent}] JSON decode error: {decode_error}")
                                append_non_json_line(decoded_line)
                                continue

                            self.logger.trace(f"[{agent}] EVENT: {json.dumps(event, ensure_ascii=False)[:300]}")

                            try:
                                parsed = event_parser(event, final_text)
                                if inspect.isawaitable(parsed):
                                    parsed = await parsed
                            except Exception as parser_error:
                                self.logger.error(f"[{agent}] Event parser error: {parser_error}", exc_info=True)
                                continue

                            new_text = None
                            action_values = []

                            if isinstance(parsed, dict):
                                new_text = parsed.get('text')
                                raw_action = parsed.get('action')
                                error_value = parsed.get('error')
                                if error_value:
                                    record_error(str(error_value), force=True)
                                if raw_action is not None:
                                    if isinstance(raw_action, (list, tuple, set)):
                                        action_values.extend(raw_action)
                                    else:
                                        action_values.append(raw_action)
                            elif isinstance(parsed, tuple) and len(parsed) == 2:
                                new_text, raw_action = parsed
                                if raw_action is not None:
                                    action_values.append(raw_action)
                            else:
                                new_text = parsed

                            if new_text is not None:
                                final_text = new_text

                            for action in action_values:
                                if not action:
                                    continue
                                actions.add(action)
                                if action == 'stop':
                                    return final_text, actions
                    finally:
                        flush_non_json_buffer()

                    return final_text, actions

                final_text, actions = await asyncio.wait_for(read_stdout(), timeout=timeout)

                try:
                    await asyncio.wait_for(process.wait(), timeout=10)
                except asyncio.TimeoutError:
                    self.logger.warning(f"[{agent}] Process did not exit within cleanup timeout, killing")
                    process.kill()
                    await process.wait()

                stderr_output = ""
                if stderr_task:
                    try:
                        stderr_data = await asyncio.wait_for(stderr_task, timeout=1)
                        stderr_output = stderr_data.decode('utf-8', errors='replace').strip()
                    except asyncio.TimeoutError:
                        stderr_task.cancel()
                        try:
                            await stderr_task
                        except asyncio.CancelledError:
                            pass

                if process.returncode and process.returncode != 0:
                    if stderr_output:
                        self.logger.error(f"[{agent}] stderr: {stderr_output}")
                    self.logger.error(f"[{agent}] Exit code: {process.returncode}")
                    self.logger.debug(f"[{agent}] return code {process.returncode} (non-zero)")
                    if final_text and str(final_text).strip():
                        record_error(str(final_text).strip(), force=True)
                    if stderr_output:
                        record_error(stderr_output, force=True)
                    return final_text, actions, get_error_messages(process.returncode)

                if stderr_output:
                    self.logger.debug(f"[{agent}] stderr: {stderr_output}")

                actual_code = process.returncode if process else None
                self.logger.trace(f"[{agent}] return code {actual_code} (normal path)")
                return final_text, actions, get_error_messages(actual_code)

            except asyncio.TimeoutError:
                self.logger.error(f"[{agent}] Timeout after {timeout} seconds")
                actual_code = process.returncode if process else None
                self.logger.warning(f"[{agent}] timed out with return code {actual_code}")
                record_error(f"{agent}: timeout exceeded ({timeout}s)", force=True)
                return None, set(), get_error_messages(actual_code)
            except Exception as error:
                self.logger.error(f"[{agent}] Error: {error}", exc_info=True)
                actual_code = process.returncode if process else None
                self.logger.warning(f"[{agent}] exception handling CLI (return code {actual_code})")
                record_error(f"{agent}: error {error}", force=True)
                return None, set(), get_error_messages(actual_code)
            finally:
                if stderr_task and not stderr_task.done():
                    stderr_task.cancel()
                    try:
                        await stderr_task
                    except asyncio.CancelledError:
                        pass

                if process:
                    # Kill process if still running
                    if process.returncode is None:
                        try:
                            self.logger.warning(f"[{agent}] Killing leftover process")
                            process.kill()
                            # Wait with timeout to prevent hanging on unresponsive processes
                            try:
                                await asyncio.wait_for(process.wait(), timeout=2.0)
                            except asyncio.TimeoutError:
                                self.logger.warning(f"[{agent}] Process did not terminate in time, forcing")
                        except (ProcessLookupError, OSError) as e:
                            self.logger.debug(f"[{agent}] Process already terminated in cleanup: {e}")

                    # Remove from running subprocesses list
                    if process in self._running_subprocesses:
                        try:
                            self._running_subprocesses.remove(process)
                        except ValueError:
                            pass  # Already removed

                    # Clear process reference from agent config
                    if agent in self.agents and self.agents[agent].get('process') == process:
                        self.agents[agent]['process'] = None
                self.logger.trace("ConsiliumAgentTUI:_call_agent_cli end agent=%s", agent)


    # =========================================================================
    # REMOVED: format_history() no longer used
    # =========================================================================
    # def format_history(self) -> str:
    #     """Format history for prompt"""
    #     return "\n".join([f"{author}: {text}" for author, text in self.history])

    def _parse_mentions(self, text: str) -> list[str]:
        """Extract @mentions from text and resolve to canonical agent names."""
        import re

        # Find all @mentions in text
        mention_pattern = r'@(\w+(?:\.\w+)*)'
        found_mentions = re.findall(mention_pattern, text)

        if not found_mentions:
            return []

        # Build reverse map: display_name -> canonical_name
        display_to_canonical: dict[str, str] = {}
        for canonical_name in self.agents.keys():
            display_name = self.get_agent_display_name(canonical_name)
            # Normalize for case-insensitive matching
            display_to_canonical[display_name.lower()] = canonical_name
            display_to_canonical[canonical_name.lower()] = canonical_name

        # Resolve mentions to canonical names
        resolved: list[str] = []
        seen: set[str] = set()

        for mention in found_mentions:
            normalized = mention.lower()
            if normalized in display_to_canonical:
                canonical = display_to_canonical[normalized]
                if canonical not in seen:
                    resolved.append(canonical)
                    seen.add(canonical)

        return resolved

    def _parse_private_mention(self, text: str) -> str | None:
        """Extract @@mention (private) from text and resolve to canonical agent name."""
        import re

        # Find all @@mentions in text
        private_pattern = r'@@(\w+(?:\.\w+)*)'
        found_mentions = re.findall(private_pattern, text)

        if not found_mentions:
            return None

        if len(found_mentions) > 1:
            self.logger.warning("Multiple @@mentions found (%d), using first one only", len(found_mentions))

        # Build reverse map: display_name -> canonical_name
        display_to_canonical: dict[str, str] = {}
        for canonical_name in self.agents.keys():
            display_name = self.get_agent_display_name(canonical_name)
            # Normalize for case-insensitive matching
            display_to_canonical[display_name.lower()] = canonical_name
            display_to_canonical[canonical_name.lower()] = canonical_name

        # Resolve first mention to canonical name
        mention = found_mentions[0]
        normalized = mention.lower()
        if normalized in display_to_canonical:
            return display_to_canonical[normalized]

        return None

    async def process_message(
        self,
        initial_author: str,
        initial_text: str,
        is_init: bool = False
    ):
        """Route an incoming message through the courier."""

        self.logger.trace("ConsiliumAgentTUI:process_message queued author=%s init=%s", initial_author, is_init)
        if self._shutting_down:
            self.logger.debug("Ignoring message processing request during shutdown")
            return

        # Parse @@mentions (private) and @mentions (priority) only from User messages
        # @@ has priority over @ - if @@ found, @ are ignored
        mentions = []
        private_to = None
        metadata = {}

        if initial_author == 'User':
            private_to = self._parse_private_mention(initial_text)
            if private_to:
                metadata['private_to'] = private_to
                metadata['status'] = 'secret'
                self.logger.debug("User sent private message to: %s", private_to)
            else:
                # No @@ found, parse regular @mentions
                mentions = self._parse_mentions(initial_text)
                if mentions:
                    metadata['mentions'] = mentions
                    self.logger.debug("User mentioned: %s", mentions)

        message = CourierMessage(
            author=initial_author,
            text=str(initial_text),
            is_init=is_init,
            metadata=metadata if metadata else None,
        )
        self.courier.enqueue_message(message)
        await self.courier.drain()

    async def on_chat_composer_submitted(self, event: ChatComposer.Submitted) -> None:
        """Handle message submission from composer."""
        user_msg = event.text.strip()
        self.logger.trace("ConsiliumAgentTUI:on_chat_composer_submitted text_len=%d", len(event.text))

        if not user_msg:
            event.composer.reset()
            return

        if self._shutting_down:
            self.logger.debug("Ignoring user input during shutdown")
            event.composer.reset()
            return

        # Reset composer state
        event.composer.reset()

        self.logger.trace(f"[User] INPUT: {user_msg}")

        # Commands
        if user_msg in ['/exit', '/quit']:
            self.logger.info(f"User closed chat ({user_msg})")
            self._shutdown_trigger = f"user command {user_msg}"
            self._create_task(self._shutdown_and_exit())
            return

        if self._start_gate_active and not self._start_gate_event.is_set():
            self.add_status(STATUS_PRESS_CTRL_G)
            return

        # Save to input history
        self._remember_input(user_msg)

        # Start sequential message processing
        self._create_task(self.process_message('User', user_msg))

    async def _shutdown_and_exit(self) -> None:
        """Helper to perform shutdown sequence and exit."""
        self.logger.info("_shutdown_and_exit invoked")
        if self._shutdown_trigger is None:
            self._shutdown_trigger = "programmatic shutdown"
        await self._shutdown()
        self.exit()

    async def action_quit(self) -> None:
        """Exit application"""
        self.logger.info("action_quit invoked - initiating shutdown")
        if self._shutdown_trigger is None:
            self._shutdown_trigger = "action_quit"
        await self._shutdown_and_exit()

    async def on_shutdown(self, event) -> None:
        """Log shutdown event (cleanup already done in action_quit)."""
        self.logger.info("on_shutdown event received (cleanup already completed in action_quit)")

    def exit(self, result: object | None = None) -> None:
        """Log app exit."""
        self.logger.info("App.exit called (result=%s, reason=%s)", result, self._shutdown_trigger or "unknown")

        # Detailed call stack for deep debugging (TRACE level only)
        import traceback
        stack_summary = traceback.extract_stack(limit=10)
        stack = "".join(traceback.format_list(stack_summary[:-1]))
        self.logger.trace("Exit call stack:\n%s", stack)

        return super().exit(result)

    # TEMPORARILY DISABLED: SystemCommand not available in Textual 2.1.2
    # TODO: Implement using Provider API for Textual 2.x
    # def get_system_commands(self, screen: Screen) -> Iterable[SystemCommand]:
    #     """Add custom commands to Ctrl+S command palette"""
    #     yield SystemCommand(
    #         "Edit prompts",
    #         "Open the built-in editor ~/.consilium/prompts.toml",
    #         self.action_edit_prompts
    #     )

    def _open_general_prompt_editor(self) -> None:
        """Open existing general prompt editor."""
        config_path = Path.home() / ".consilium" / "prompts.toml"
        self.logger.trace("ConsiliumAgentTUI:opening general prompt editor")

        # Ensure config exists
        if not config_path.exists():
            self.add_status(STATUS_CREATING_PROMPTS_TOML)
            load_prompts_from_config()  # This will create the file

        def on_editor_dismiss(result: bool) -> None:
            """Handle editor result"""
            if result:
                chat_log = self.query_one(ChatLog)
                chat_log.write(Text(TEXT_PROMPT_SAVED, style="bold white"))
                self.logger.info("Prompts edited and saved")
            else:
                self.add_status(STATUS_EDITING_CANCELLED)
                self.logger.debug("Prompt editing cancelled")

        self.push_screen(PromptEditorScreen(config_path), on_editor_dismiss)

    def _open_agent_prompt_editor(self, agent_name: str) -> None:
        """Open agent-specific prompt editor. ESC returns to previous screen (PromptSelectionScreen)."""
        try:
            self.logger.trace("ConsiliumAgentTUI:opening agent prompt editor %s", agent_name)
            self.push_screen(AgentPromptEditorScreen(agent_name))
        except Exception as exc:
            self.logger.error(f"Error opening agent prompt editor for {agent_name}: {exc}", exc_info=True)
            self.add_error(ERROR_EDITOR_OPEN.format(agent_name=agent_name, exc=exc), None)

    def action_edit_prompts(self) -> None:
        """Open prompt selection menu first."""
        try:
            agent_entries = [
                (name, self.agent_prompt_exists(name))
                for name in self.agents.keys()
            ]

            def on_selection(selection: str | None) -> None:
                try:
                    self.logger.trace("ConsiliumAgentTUI:prompt selection chosen=%s", selection)
                    if selection is None:
                        return
                    if selection == "general":
                        self._open_general_prompt_editor()
                        return
                    if selection.startswith("agent:"):
                        agent_name = selection.split(":", 1)[1]
                        if agent_name in self.agents:
                            self._open_agent_prompt_editor(agent_name)
                        else:
                            self.add_status(STATUS_UNKNOWN_AGENT.format(agent_name=agent_name))
                        return
                    self.add_status(STATUS_PROMPT_SELECTION_FAILED)
                except Exception as exc:
                    self.logger.error(f"Error in prompt selection handler: {exc}", exc_info=True)
                    self.add_error(ERROR_PROMPT_SELECT.format(exc=exc), None)

            self.push_screen(PromptSelectionScreen(agent_entries), on_selection)
        except Exception as exc:
            self.logger.error(f"Error opening prompt selection menu: {exc}", exc_info=True)
            self.add_error(ERROR_PROMPT_MENU.format(exc=exc), None)

    def action_edit_members(self) -> None:
        """Open members management panel."""
        self.push_screen(MembersSelectionScreen())

    def action_edit_roles(self) -> None:
        """Open role management panel."""
        try:
            self.role_manager.reload()
            self.push_screen(RoleSelectionScreen())
        except Exception as exc:
            self.logger.error("Error opening role selection menu: %s", exc, exc_info=True)
            self.add_error(ERROR_ROLE_MENU.format(exc=exc), None)

    def action_edit_system_settings(self) -> None:
        """Open system settings panel."""
        try:
            self.push_screen(SystemSettingsScreen())
        except Exception as exc:
            self.logger.error("Error opening system settings: %s", exc, exc_info=True)
            self.add_error(f"Failed to open system settings: {exc}", None)

    # -------------------------------------------------------------------------
    # Step-by-step mode
    # -------------------------------------------------------------------------

    def action_toggle_step_mode(self) -> None:
        """Toggle step-by-step mode on/off"""
        self.logger.debug("Toggling step-by-step mode state")
        self.step_by_step_mode = not self.step_by_step_mode

        # Track if we already showed a status message
        status_shown = False

        # If disabling mode while waiting, unblock immediately
        if not self.step_by_step_mode and self.waiting_for_step:
            self.waiting_for_step = False
            self.step_event.set()
            self.add_message(
                "System",
                MSG_STEP_MODE_AUTO_CONTINUE,
                style_override="bright_white",
            )
            self.logger.debug("Step mode disabled during pause - auto-continuing")
            status_shown = True

        # Log and display status (only if not already shown above)
        if not status_shown:
            if self.step_by_step_mode:
                self.add_message(
                    "System",
                    MSG_STEP_MODE_ON,
                    style_override="bright_white",
                )
            else:
                self.add_message("System", MSG_STEP_MODE_OFF, style_override="bright_white")
        self.logger.info(f"Step-by-step mode: {'ON' if self.step_by_step_mode else 'OFF'}")
        self._update_step_footer_color()
        self._sync_step_binding_label()

    def _update_step_footer_color(self) -> None:
        """Update footer background color based on step mode state"""
        try:
            footer = self.query_one(Footer)
            if self.step_by_step_mode:
                footer.styles.background = "green"
            else:
                footer.styles.background = "rgb(38,38,38)"  # Default dark gray
            self.logger.debug(f"Footer color updated: {'green' if self.step_by_step_mode else 'gray'}")
        except NoMatches:
            self.logger.debug("Footer not found for color update")

    async def _wait_for_step_permission(self, agent_name: str) -> bool:
        """Pause progression until user allows the next agent to speak."""
        if not self.step_by_step_mode:
            return True
        if self._shutting_down or self._interrupt_requested:
            return False
        if self.waiting_for_step:
            self.logger.debug("Step wait already in progress; reusing existing event")
            await self.step_event.wait()
            return not (self._shutting_down or self._interrupt_requested)

        self.waiting_for_step = True
        self.step_event.clear()
        self.add_message(
            "System",
            f"⏸  {agent_name} is waiting for approval (Ctrl+N)",
            style_override="bright_white",
        )
        self.logger.debug(f"Step mode: waiting before dispatching to {agent_name}")

        try:
            await self.step_event.wait()
        finally:
            self.waiting_for_step = False

        if self._shutting_down or self._interrupt_requested:
            self.logger.debug("Step wait ended due to shutdown/interrupt")
            return False

        self.logger.debug(f"Step mode: continuing, {agent_name} may proceed")
        return True

    def action_next_step(self) -> None:
        """Continue to next agent in step-by-step mode"""
        self.logger.info(f"action_next_step called: mode={self.step_by_step_mode}, waiting={self.waiting_for_step}")

        if not self.step_by_step_mode:
            self.add_message("System", MSG_STEP_MODE_DISABLED, style_override="bold red")
            self.logger.debug("next_step called but step mode is OFF")
            return

        if not self.waiting_for_step:
            self.add_message("System", MSG_NO_PENDING_STEPS, style_override="bright_white")
            return

        # Signal the event - this will unblock process_message
        self.waiting_for_step = False
        self.step_event.set()
        self.logger.debug("User advanced to next step")

    def action_scroll_home(self) -> None:
        """Scroll chat to top (oldest loaded message)"""
        try:
            chat_log = self.query_one("#chat-log", ChatLog)
            chat_log.scroll_home()
            self.logger.debug("Scrolled to top of chat history")
        except NoMatches:
            self.logger.debug("Chat log not available for scroll home")

    def action_scroll_end(self) -> None:
        """Scroll chat to bottom (newest message)"""
        try:
            chat_log = self.query_one("#chat-log", ChatLog)
            chat_log.scroll_end()
            self.logger.debug("Scrolled to bottom of chat history")
        except NoMatches:
            self.logger.debug("Chat log not available for scroll end")

    def action_page_up(self) -> None:
        """Scroll chat up by one page (terminal height)"""
        try:
            chat_log = self.query_one("#chat-log", ChatLog)
            # Get terminal height for page size
            page_size = max(25, chat_log.size.height)
            chat_log.scroll_relative(y=-page_size, animate=False)
            self.logger.debug(f"Scrolled up by {page_size} lines")
        except NoMatches:
            self.logger.debug("Chat log not available for page up")

    def action_page_down(self) -> None:
        """Scroll chat down by one page (terminal height)"""
        try:
            chat_log = self.query_one("#chat-log", ChatLog)
            # Get terminal height for page size
            page_size = max(25, chat_log.size.height)
            chat_log.scroll_relative(y=page_size, animate=False)
            self.logger.debug(f"Scrolled down by {page_size} lines")
        except NoMatches:
            self.logger.debug("Chat log not available for page down")

    def _sync_step_binding_label(self) -> None:
        """Refresh footer so the step-mode label stays in sync."""
        try:
            footer = self.query_one(ConsiliumFooter)
        except NoMatches:
            return

        footer.refresh(layout=True, recompose=True)

    def _focus_composer(self) -> None:
        """Return focus to the chat composer when appropriate."""
        if self._shutting_down:
            return
        try:
            composer = self.query_one(ChatComposer)
            self.set_focus(composer)
        except NoMatches:
            self.logger.debug("Chat composer not found for focus")
        except Exception:  # pragma: no cover - defensive
            self.logger.exception("Failed to focus composer")

    def on_screen_resume(self, event: events.ScreenResume) -> None:
        """Restore composer focus after closing modals."""
        if event.screen is self.screen:
            self.call_later(self._focus_composer)
