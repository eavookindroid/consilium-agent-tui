"""
Consilium Agent - UI Widgets

Chat composer, chat log, status bar, footer, and command palette provider.

Copyright (c) 2025 Artel Team
Licensed under Artel Team Non-Commercial License
"""

import asyncio
import logging
import math
from textual.widgets import TextArea, RichLog, Footer, Static
from textual.widgets._footer import FooterKey
from textual.binding import Binding
from textual.command import SimpleCommand, SimpleProvider
from textual.screen import Screen
from textual.message import Message
from textual.app import ComposeResult
from textual import events
from textual.containers import Container
from rich.text import Text

from .constants import STATUS_READY

class ChatComposer(TextArea):
    """Multiline input field with dynamic height and submit message."""

    DEFAULT_CSS = """
    ChatComposer {
        background: $surface;
        color: #98e024;
        border: tall #444444;
        margin: 1;
        padding: 0 1;
        height: 5;
        min-height: 5;
        max-height: 10;
        overflow-y: auto;
        width: 1fr;
    }
    ChatComposer:focus-within {
        border: tall $accent;
    }
    ChatComposer TextArea {
        background: transparent;
    }
    ChatComposer .text-area--cursor {
        color: $surface;
        background: $accent;
    }
    ChatComposer .text-area--cursor-line {
        background: rgba(255, 255, 255, 0.05);
    }
    """

    class Submitted(Message):
        """Emitted when the composer submits the text."""

        def __init__(self, composer: "ChatComposer", text: str) -> None:
            super().__init__()
            self.composer = composer
            self.text = text

    def __init__(self) -> None:
        super().__init__(id="chat-composer")
        self.min_lines = 5
        self.max_lines = 10
        self._update_height()

    def on_mount(self) -> None:
        """Ensure input focus on mount."""
        self.focus()

    def reset(self) -> None:
        """Clear the text and restore minimum height."""
        self.text = ""
        self._update_height()

    def _update_height(self) -> None:
        lines = self.text.count("\n") + 1
        target = max(self.min_lines, min(self.max_lines, lines))
        self.styles.height = target
        self.styles.min_height = self.min_lines
        self.styles.max_height = self.max_lines
        self.refresh()

    def on_text_area_changed(self, _event: TextArea.Changed) -> None:
        self._update_height()

    def _apply_history_text(self, value: str) -> None:
        self.text = value
        try:
            self.cursor_position = len(self.text)
        except Exception:
            if hasattr(self.app, "logger"):
                self.app.logger.debug("Failed to reposition composer cursor", exc_info=True)
        self._update_height()

    def on_key(self, event: events.Key) -> None:
        key_name = (event.name or "").lower()
        aliases = tuple(alias.lower() for alias in (event.aliases or []))
        name_aliases = tuple(alias.lower() for alias in (event.name_aliases or []))

        def _has_combo(combo: str) -> bool:
            combo = combo.lower()
            underscore = combo.replace("+", "_")
            minus = combo.replace("+", "-")
            candidates = {combo, underscore, minus}
            return any(
                candidate in aliases or candidate in name_aliases
                for candidate in candidates
            )

        # Debug logging for key events with modifiers (helps diagnose shortcuts)
        if hasattr(self.app, "logger") and any("ctrl" in alias or "control" in alias for alias in (*aliases, *name_aliases)):
            self.app.logger.debug(
                f"ChatComposer key event: key={event.key!r}, name={event.name!r}, "
                f"aliases={event.aliases}, name_aliases={event.name_aliases}"
            )

        # Handle Ctrl+C for clearing composer
        if any(alias == "ctrl+c" for alias in aliases):
            self.clear()
            event.stop()
            return

        # Handle Alt+Up for input history navigation (previous)
        if _has_combo("alt+up") or _has_combo("meta+up"):
            prev_msg = self.app.get_previous_input() if hasattr(self.app, "get_previous_input") else None
            if prev_msg is not None:
                self._apply_history_text(prev_msg)
            event.stop()
            return

        # Handle Alt+Down for input history navigation (next)
        if _has_combo("alt+down") or _has_combo("meta+down"):
            next_msg = self.app.get_next_input() if hasattr(self.app, "get_next_input") else None
            if next_msg is not None:
                self._apply_history_text(next_msg)
            event.stop()
            return

        # Toggle step mode from composer
        if _has_combo("ctrl+o") or _has_combo("control+o"):
            if hasattr(self.app, "action_toggle_step_mode"):
                self.app.action_toggle_step_mode()
            event.stop()
            return

        # Continue to next step from composer
        def _matches(combo: str) -> bool:
            combo = combo.lower()
            return combo in aliases or combo.replace("+", "_") in name_aliases

        is_ctrl_space = _matches("ctrl+space") or _matches("control+space")
        is_ctrl_n = _matches("ctrl+n") or _matches("control+n")
        is_shift_space = _matches("shift+space")

        if is_ctrl_space or is_ctrl_n or is_shift_space:
            if hasattr(self.app, "action_next_step"):
                self.app.action_next_step()
            event.stop()
            return

        # Pass Ctrl+Home/End/PgUp/PgDown to app for chat history navigation
        # (regular Home/End/PgUp/PgDown work in TextArea for cursor/text navigation)
        chat_nav_keys = ("ctrl+home", "ctrl+end", "ctrl+pageup", "ctrl+pagedown")
        if any(alias in chat_nav_keys for alias in aliases):
            # Don't stop - let event bubble to App bindings
            return

        # Handle Enter: submit message
        if key_name == "enter":
            event.stop()
            self.post_message(self.Submitted(self, self.text.rstrip()))
        # Handle Ctrl+J: insert newline
        elif key_name == "newline" or any(alias == "ctrl+j" for alias in aliases):
            self.insert("\n")
            event.stop()


class ChatLog(RichLog):
    """Read-only chat log that keeps focus on the composer."""

    can_focus = False
    can_focus_children = False

    def on_focus(self, event: events.Focus) -> None:  # pragma: no cover - defensive
        event.stop()
        if hasattr(self.app, "_focus_composer"):
            self.app.call_later(self.app._focus_composer)

# COMMAND PALETTE PROVIDER
# ============================================================================


class ConsiliumCommandProvider(SimpleProvider):
    """Adds Consilium-specific entries to the command palette."""

    def __init__(self, screen: Screen, match_style=None) -> None:
        commands: list[SimpleCommand] = []
        if getattr(screen.app, "enable_prompt_editor_command", False):
            commands.append(
                SimpleCommand(
                    "Edit prompts",
                    screen.app.action_edit_prompts,
                    "Open the built-in editor ~/.consilium/prompts.toml",
                )
            )
        commands.extend(
            [
                SimpleCommand(
                    "System Settings",
                    screen.app.action_edit_system_settings,
                    "Configure system-wide settings (prompt refresh period)",
                ),
                SimpleCommand(
                    "Roles",
                    screen.app.action_edit_roles,
                    "Manage reusable prompt roles",
                ),
                SimpleCommand(
                    "Members",
                    screen.app.action_edit_members,
                    "Manage agent participation in the chat",
                ),
            ]
        )
        super().__init__(screen, commands)


# ============================================================================
# Consilium Agent APPLICATION
# ============================================================================


class ConsiliumFooter(Footer):
    """Footer with stable ordering and dynamic step-mode labels."""

    ORDER = (
        "toggle_step_mode",
        "next_step",
        "interrupt_conversation",
        "clear",
        "edit_roles",
        "command_palette",
        "quit",
    )

    def compose(self) -> ComposeResult:  # type: ignore[override]
        if not self._bindings_ready:
            return

        bindings_map = self.screen.active_bindings

        def get_binding(action: str) -> tuple[Binding, bool, str] | None:
            for _, binding, enabled, tooltip in bindings_map.values():
                if binding.action == action and binding.show:
                    return binding, enabled, tooltip
            return None

        ordered_actions = [action for action in self.ORDER if get_binding(action)]

        self.styles.grid_size_columns = len(ordered_actions)

        for action in ordered_actions:
            binding_info = get_binding(action)
            if not binding_info:
                continue
            binding, enabled, tooltip = binding_info

            if action == "toggle_step_mode":
                description = "STEP ON" if getattr(self.app, "step_by_step_mode", False) else "STEP OFF"
                key_display = "Ctrl+O"
            elif action == "next_step":
                key_display = "Ctrl+N"
                description = binding.description or "Next Step"
            elif action == "interrupt_conversation":
                key_display = "Esc"
                description = binding.description or "Interrupt"
            else:
                key_display = binding.key_display or self.app.get_key_display(binding)
                description = binding.description

            yield FooterKey(
                binding.key,
                key_display,
                description,
                binding.action,
                disabled=not enabled,
                tooltip=tooltip,
            ).data_bind(Footer.compact)


# ============================================================================
# ANIMATED STATUS BAR
# ============================================================================


class AnimatedStatusBar(Static):
    """Status bar with smooth brightness pulsation."""

    _BRIGHT = 0xD0  # maximum gray brightness
    _DARK = 0x20    # minimum brightness (almost black)
    _TIMER_INTERVAL = 0.05  # Update interval in seconds
    _PHASE_STEP = 0.015     # Phase increment per tick (slower = 0.05 / 3)

    def __init__(self, text: str = "", **kwargs):
        super().__init__("", markup=False, **kwargs)
        self._text = text
        self._phase = 0.0
        self._timer = None
        self._visual = None
        import logging
        logging.getLogger("AnimatedStatusBar").info(f"AnimatedStatusBar.__init__ called with text='{text}'")

    def on_mount(self) -> None:
        """Start the timer-driven pulse animation."""
        import logging
        logging.getLogger("AnimatedStatusBar").info(f"on_mount called, text='{self._text}'")
        if hasattr(self.app, 'logger'):
            self.app.logger.info(f"AnimatedStatusBar.on_mount called, initial_text='{self._text}'")
        self._update_content(self._phase)
        self._timer = self.set_interval(self._TIMER_INTERVAL, self._tick)
        if hasattr(self.app, "register_status_bar"):
            try:
                self.app.register_status_bar(self)  # type: ignore[attr-defined]
                if hasattr(self.app, 'logger'):
                    self.app.logger.debug("AnimatedStatusBar registered successfully")
            except Exception as exc:  # pragma: no cover - defensive
                if hasattr(self.app, 'logger'):
                    self.app.logger.warning(f"Failed to register status bar widget: {exc}", exc_info=True)

    def on_unmount(self) -> None:
        """Stop the pulse when the widget is removed."""
        if self._timer is not None:
            self._timer.stop()
            self._timer = None
        if hasattr(self.app, "unregister_status_bar"):
            try:
                self.app.unregister_status_bar(self)  # type: ignore[attr-defined]
            except Exception:  # pragma: no cover - defensive
                self.app.logger.debug("Failed to unregister status bar widget", exc_info=True)

    def _tick(self) -> None:
        """Advance the phase and refresh the display."""
        self._phase = (self._phase + self._PHASE_STEP) % 1.0
        self._update_content(self._phase)

    def _resolve_color(self, phase: float) -> str:
        """Convert phase to a grayscale hex colour."""
        value = int(self._DARK + (self._BRIGHT - self._DARK) * (0.5 + 0.5 * math.sin(phase * math.tau)))
        return f"#{value:02x}{value:02x}{value:02x}"

    def _update_content(self, phase: float) -> None:
        """Rebuild the status text using the current brightness."""
        text = self._text or ""
        color = self._resolve_color(phase)
        import logging
        logging.getLogger("AnimatedStatusBar").debug(f"_update_content: text='{text}', color={color}")
        # Try using update() instead of direct _content assignment for Nuitka compatibility
        self.update(Text(text, style=color))
        # self._content = Text(text, style=color)
        # self._visual = None
        # self.refresh(layout=False)

    def update_text(self, text: str) -> None:
        """Update the status bar message without resetting phase."""
        self._text = text
        self._update_content(self._phase)
        if hasattr(self.app, 'logger'):
            self.app.logger.debug(f"AnimatedStatusBar.update_text: '{text}'")



__all__ = ['ChatComposer', 'ChatLog', 'ConsiliumCommandProvider', 'ConsiliumFooter', 'AnimatedStatusBar']
