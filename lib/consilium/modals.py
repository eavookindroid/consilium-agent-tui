"""
Consilium Agent - Modal Screens

Prompt editors, agent settings, and configuration modals.

Copyright (c) 2025 Artel Team
Licensed under Artel Team Non-Commercial License
"""

import asyncio
import hashlib
import logging
import re
import shutil
from pathlib import Path
from typing import Any, Iterable

from textual.screen import ModalScreen
from textual.widgets import TextArea, Button, Static, Checkbox, OptionList, Input, Select
from textual.containers import Container, Horizontal, VerticalScroll
from textual.binding import Binding
from textual.widgets.option_list import Option
from textual import events
from textual.app import ComposeResult
from textual.css.query import NoMatches

from .constants import (
    STATUS_PROMPT_SAVED, STATUS_EDITING_CANCELLED,
    STATUS_CREATING_PROMPTS_TOML, STATUS_UNKNOWN_AGENT,
    STATUS_PROMPT_SELECTION_FAILED, STATUS_EDIT_CANCELLED_RU,
    STATUS_PROMPT_EMPTY_RU, STATUS_PROMPT_SAVED_RU,
    STATUS_ROLE_CREATED, STATUS_ROLE_SAVED,
    STATUS_ROLE_NAME_REQUIRED, STATUS_ROLE_NAME_EXISTS,
    ERROR_EDITOR_INIT, ERROR_PROMPT_SAVE, ERROR_EDITOR_OPEN,
    ERROR_PROMPT_SELECT, ERROR_PROMPT_MENU, ERROR_ROLE_MENU,
    ERROR_ROLE_EDITOR, ERROR_ROLE_CREATE,
    TEXT_PROMPT_SAVED, STATUS_AGENT_PARTICIPATION,
    SYSTEM_PROMPT_PERIOD,
)
from .utils import load_prompts_from_config

# TOML validation (if available)
try:
    import tomllib
except ModuleNotFoundError:
    try:
        import tomli as tomllib
    except ModuleNotFoundError:
        tomllib = None

class PromptEditorScreen(ModalScreen[bool]):
    """Modal screen for editing prompts.toml"""

    CSS = """
    PromptEditorScreen {
        align: center middle;
    }
    #editor-container {
        width: 100%;
        height: 100%;
        background: $surface;
        border: thick $primary;
        padding: 0;
    }
    #editor-title {
        dock: top;
        height: 1;
        content-align: center middle;
        background: $primary;
        color: $text;
        padding: 0 1;
    }
    #editor-body {
        height: 1fr;
        layout: vertical;
    }
    .editor-pane {
        width: 1fr;
        height: 1fr;
        margin-bottom: 0;
    }
    .editor-pane TextArea {
        height: 1fr;
        border: solid $accent;
    }
    .editor-label {
        height: 1;
        content-align: center middle;
        text-style: bold;
        margin-bottom: 1;
    }
    #editor-hint {
        padding: 1 0;
        color: $text-muted;
        text-align: center;
    }
    #button-container {
        dock: bottom;
        height: auto;
        align: center middle;
        padding: 1;
    }
    Button {
        margin: 0 1;
    }
    """

    BINDINGS = [
        Binding("escape", "cancel", "Cancel"),
        Binding("ctrl+s", "save", "Save"),
    ]

    def __init__(self, config_path: Path):
        super().__init__()
        self.config_path = config_path
        self.logger = logging.getLogger('PromptEditor')
        init_prompt, system_prompt = load_prompts_from_config()
        self.init_prompt = init_prompt.strip()
        self.system_prompt = system_prompt.strip()

    def compose(self) -> ComposeResult:
        """Create editor UI"""
        with Container(id="editor-container"):
            yield Static("📝 Prompt editor ~/.consilium/prompts.toml", id="editor-title")

            with Container(id="editor-body"):
                with Container(id="init-pane", classes="editor-pane"):
                    yield Static("INIT PROMPT", classes="editor-label")
                    yield TextArea(
                        self.init_prompt,
                        id="init-editor",
                        language="text"
                    )
                with Container(id="system-pane", classes="editor-pane"):
                    yield Static("SERVICE PROMPT", classes="editor-label")
                    yield TextArea(
                        self.system_prompt,
                        id="system-editor",
                        language="text"
                    )

            yield Static(
                "Changes are saved for both prompts immediately. Restart the application to apply new texts.",
                id="editor-hint"
            )

            with Horizontal(id="button-container"):
                yield Button("💾 Save (Ctrl+S)", variant="primary", id="save-btn")
                yield Button("❌ Cancel (Esc)", variant="default", id="cancel-btn")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        """Handle button clicks"""
        if event.button.id == "save-btn":
            self.action_save()
        elif event.button.id == "cancel-btn":
            self.action_cancel()

    def action_save(self) -> None:
        """Save edited content"""
        init_editor = self.query_one("#init-editor", TextArea)
        system_editor = self.query_one("#system-editor", TextArea)
        init_text = init_editor.text.strip()
        system_text = system_editor.text.strip()
        toml_content = self._build_prompts_toml(init_text, system_text)

        # Validate TOML before writing to disk (if parser available)
        if tomllib is not None:
            try:
                tomllib.loads(toml_content)
            except Exception as parse_error:
                message = f"⚠️ TOML syntax error: {parse_error}"
                self.logger.exception("Prompt validation failed")
                self.app.add_status(message)
                self.app.bell()
                return

        try:
            # Backup original using copy to avoid losing source on write failure
            backup_path = self.config_path.with_suffix('.toml.backup')
            if self.config_path.exists():
                shutil.copy2(self.config_path, backup_path)
                self.logger.info(f"Created backup: {backup_path}")

            # Save new content
            self.config_path.write_text(toml_content, encoding='utf-8')
            self.logger.info(f"Saved prompts to {self.config_path}")

            # Update prompts in app memory immediately (no restart needed!)
            self.app.init_prompt = init_text
            self.app.system_prompt = system_text
            self.logger.info("Updated prompts in memory")

            self.dismiss(True)
        except Exception:
            self.logger.exception("Failed to save prompts")
            self.app.bell()

    def action_cancel(self) -> None:
        """Cancel editing"""
        self.dismiss(False)

    def on_key(self, event: events.Key) -> None:
        """Stop Escape and Ctrl+S from bubbling to parent app."""
        if event.key == "escape":
            event.stop()
            event.prevent_default()
            self.action_cancel()
            return
        elif event.key == "ctrl+s":
            event.stop()
            event.prevent_default()
            self.action_save()
            return

    @staticmethod
    def _build_prompts_toml(init_text: str, system_text: str) -> str:
        """Assemble TOML string with provided prompt texts."""

        def sanitize(value: str) -> str:
            return value.replace('"""', '\\"\\"\\"')

        init_block = sanitize(init_text)
        system_block = sanitize(system_text)

        return (
            "# Consilium Agent Prompts Configuration\n"
            "# Edit these prompts to customize agent behavior\n\n"
            "[prompts]\n"
            "# Initial prompt - shown only on first agent introduction\n"
            f'init = """\n{init_block}\n"""\n\n'
            "# Service prompt - used for all subsequent messages\n"
            f'system = """\n{system_block}\n"""\n'
        )


# ============================================================================
# PROMPT SELECTION MODAL
# ============================================================================


class PromptSelectionScreen(ModalScreen[str | None]):
    """Modal menu to select which prompt editor to open."""

    CSS = """
    PromptSelectionScreen {
        align: center middle;
    }
    #prompt-menu {
        width: 60;
        background: $surface;
        border: solid $accent;
        padding: 2;
        layout: vertical;
        height: auto;
        min-height: 16;
    }
    #prompt-menu-title {
        text-style: bold;
        content-align: center middle;
        padding-bottom: 1;
    }
    OptionList {
        height: auto;
        min-height: 6;
        border: solid $accent;
    }
    #prompt-menu-hint {
        padding-top: 1;
        color: $text-muted;
        text-align: center;
    }
    """

    BINDINGS = [
        Binding("escape", "cancel", "Cancel"),
    ]

    def __init__(self, agents: list[tuple[str, bool]]):
        super().__init__()
        self.agents = agents
        self._mounted = False

    def compose(self) -> ComposeResult:
        options: list[Option] = [
            Option("General service prompt", id="general"),
        ]
        for agent_name, has_prompt in self.agents:
            prefix = "*" if has_prompt else "-"
            label = f"{prefix} {agent_name}"
            options.append(Option(label, id=f"agent:{agent_name}"))

        with Container(id="prompt-menu"):
            yield Static("Select prompt", id="prompt-menu-title")
            yield OptionList(*options, id="prompt-options")
            yield Static("(*) has custom prompt, (-) uses general", id="prompt-menu-hint")

    def on_mount(self) -> None:
        option_list = self.query_one("#prompt-options", OptionList)
        option_list.highlighted = 0
        option_list.focus()
        # Force visual update after focus
        def activate_highlight():
            option_list.highlighted = 0
            option_list.scroll_to_highlight()
        self.call_later(activate_highlight)
        self._mounted = True

    def action_cancel(self) -> None:
        self.dismiss(None)

    def on_key(self, event: events.Key) -> None:
        """Stop Escape from bubbling to parent app."""
        if event.key == "escape":
            event.stop()
            event.prevent_default()
            self.action_cancel()
            return

    def on_option_list_option_selected(self, event: OptionList.OptionSelected) -> None:
        """Handle option selection - open editor without closing this screen."""
        selection = event.option.id
        if not selection:
            return

        # Open editor on top of this screen instead of dismissing
        if selection == "general":
            self._open_general_editor()
        elif selection.startswith("agent:"):
            agent_name = selection.split(":", 1)[1]
            if agent_name in self.app.agents:
                self._open_agent_editor(agent_name)

    def _open_general_editor(self) -> None:
        """Open general prompt editor on top of this selection screen."""
        config_path = Path.home() / ".consilium" / "prompts.toml"
        if not config_path.exists():
            self.app.add_status(STATUS_CREATING_PROMPTS_TOML)
            load_prompts_from_config()

        def on_editor_close(result: bool) -> None:
            if result:
                self.app.add_status(STATUS_PROMPT_SAVED)
            # Screen stays open, just refresh if needed

        self.app.push_screen(PromptEditorScreen(config_path), on_editor_close)

    def _open_agent_editor(self, agent_name: str) -> None:
        """Open agent prompt editor on top of this selection screen."""
        self.app.push_screen(AgentPromptEditorScreen(agent_name))

    def on_screen_resume(self, event: events.ScreenResume) -> None:
        """Refresh the list when returning from an editor."""
        try:
            self.app.logger.trace(
                "PromptSelectionScreen:on_screen_resume mounted=%s", self._mounted
            )

            if not self._mounted:
                return

            try:
                option_list = self.query_one(OptionList)
            except NoMatches:
                self.app.logger.debug(
                    "PromptSelectionScreen:on_screen_resume: option list missing"
                )
                return

            agent_entries = [
                (name, self.app.agent_prompt_exists(name))
                for name in self.app.agents.keys()
            ]

            options: list[Option] = [
                Option("General service prompt", id="general"),
            ]
            for agent_name, has_prompt in agent_entries:
                prefix = "*" if has_prompt else "-"
                label = f"{prefix} {agent_name}"
                options.append(Option(label, id=f"agent:{agent_name}"))

            option_list.clear_options()
            for option in options:
                option_list.add_option(option)
            option_list.highlighted = 0
            option_list.focus()
            option_list.scroll_to_highlight()
        except Exception as exc:
            self.app.logger.error(
                "PromptSelectionScreen:on_screen_resume error: %s", exc, exc_info=True
            )


class NoRolesWarningScreen(ModalScreen[None]):
    """Warning dialog shown when trying to add agent without any roles."""

    CSS = """
    NoRolesWarningScreen {
        align: center middle;
    }
    #no-roles-container {
        width: 70;
        background: $surface;
        border: solid $accent;
        padding: 2;
        layout: vertical;
        height: auto;
    }
    #no-roles-title {
        text-style: bold;
        content-align: center middle;
        padding-bottom: 1;
        color: $warning;
    }
    #no-roles-message {
        padding: 1 0;
        text-align: center;
    }
    #no-roles-actions {
        layout: horizontal;
        align: center middle;
        padding-top: 1;
    }
    Button {
        margin: 0 1;
    }
    """

    BINDINGS = [
        Binding("escape", "close", "Close"),
    ]

    def compose(self) -> ComposeResult:
        with Container(id="no-roles-container"):
            yield Static("⚠ No Roles Available", id="no-roles-title")
            yield Static(
                "Cannot add new agent until you create at least one role.\n\n"
                "Go to \"Roles\" menu, create a new role and write your first prompt.\n"
                "Don't forget to save it :)",
                id="no-roles-message"
            )
            with Horizontal(id="no-roles-actions"):
                yield Button("OK", id="close-btn", variant="primary")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "close-btn":
            self.action_close()

    def action_close(self) -> None:
        self.dismiss(None)


class RoleNamePrompt(ModalScreen[str | None]):
    """Dialog to capture a new role name before creating it."""

    CSS = """
    RoleNamePrompt {
        align: center middle;
    }
    #role-name-container {
        width: 60;
        background: $surface;
        border: solid $accent;
        padding: 2;
        layout: vertical;
        height: auto;
    }
    #role-name-title {
        text-style: bold;
        content-align: center middle;
        padding-bottom: 1;
    }
    #role-name-error {
        color: $error;
        min-height: 1;
        padding-top: 1;
    }
    #role-name-actions {
        layout: horizontal;
        align: center middle;
        padding-top: 1;
    }
    Button {
        margin: 0 1;
    }
    """

    BINDINGS = [
        Binding("escape", "cancel", "Cancel"),
    ]

    def __init__(self, existing_names: Iterable[str]):
        super().__init__()
        self._existing = {name.strip().lower() for name in existing_names if name}

    def compose(self) -> ComposeResult:
        with Container(id="role-name-container"):
            yield Static("Create new role", id="role-name-title")
            yield Input(placeholder="Role name", id="role-name-input")
            yield Static("", id="role-name-error")
            with Horizontal(id="role-name-actions"):
                yield Button("Cancel", id="cancel-btn")
                yield Button("Continue", id="submit-btn", variant="primary")

    def on_mount(self) -> None:
        self.query_one("#role-name-input", Input).focus()

    def action_cancel(self) -> None:
        self.dismiss(None)

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "cancel-btn":
            self.action_cancel()
        elif event.button.id == "submit-btn":
            self._submit()

    def on_input_submitted(self, event: Input.Submitted) -> None:
        if event.input.id == "role-name-input":
            self._submit()

    def _submit(self) -> None:
        input_widget = self.query_one("#role-name-input", Input)
        error_widget = self.query_one("#role-name-error", Static)
        name = (input_widget.value or "").strip()

        if not name:
            error_widget.update(STATUS_ROLE_NAME_REQUIRED)
            self.app.bell()
            return

        if name.lower() in self._existing:
            error_widget.update(STATUS_ROLE_NAME_EXISTS)
            self.app.bell()
            return

        self.dismiss(name)


class RolePromptEditorScreen(ModalScreen[bool]):
    """Editor for role prompt text."""

    CSS = """
    RolePromptEditorScreen {
        align: center middle;
    }
    #role-editor-container {
        width: 100%;
        background: $surface;
        border: solid $accent;
        padding: 0;
        layout: vertical;
        height: 100%;
        min-height: 24;
    }
    #role-editor-title {
        dock: top;
        height: auto;
        content-align: center middle;
        text-style: bold;
        background: $accent;
        color: $text;
    }
    #role-editor-body {
        height: 1fr;
        layout: vertical;
        padding: 1;
        width: 100%;
    }
    #role-prompt-area {
        height: 1fr;
        border: solid $accent;
        min-height: 16;
        width: 100%;
    }
    #role-editor-actions {
        layout: horizontal;
        align: center middle;
        padding: 1;
        height: auto;
        dock: bottom;
    }
    Button {
        margin: 0 1;
    }
    """

    BINDINGS = [
        Binding("escape", "cancel", "Back"),
        Binding("ctrl+s", "save", "Save"),
    ]

    def __init__(self, role_id: str):
        super().__init__()
        self.role_id = role_id
        self._role_name = ""

    def compose(self) -> ComposeResult:
        with Container(id="role-editor-container"):
            yield Static("Role prompt editor", id="role-editor-title")
            with Container(id="role-editor-body"):
                yield TextArea("", id="role-prompt-area", language="text")
            with Horizontal(id="role-editor-actions"):
                yield Button("⬅ Back (Esc)", id="cancel-role-btn")
                yield Button("🗑 Delete", id="delete-role-btn", variant="error")
                yield Button("💾 Save (Ctrl+S)", id="save-role-btn", variant="primary")

    def on_mount(self) -> None:
        role = self.app.get_role(self.role_id)
        if role is None:
            self.app.logger.error("RolePromptEditorScreen:unknown role %s", self.role_id)
            self.app.add_error(ERROR_ROLE_EDITOR.format(exc="unknown role"), None)
            self.dismiss(False)
            return

        self._role_name = role.name
        title = self.query_one("#role-editor-title", Static)
        title.update(f"Role prompt: {role.name}")

        text_area = self.query_one("#role-prompt-area", TextArea)
        prompt_text = self.app.load_role_prompt(self.role_id)
        text_area.load_text(prompt_text)
        self.call_later(text_area.focus)
        self._sync_save_button()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "cancel-role-btn":
            self.action_cancel()
        elif event.button.id == "delete-role-btn":
            self.action_delete()
        elif event.button.id == "save-role-btn":
            self.action_save()

    def on_input_changed(self, _event: Input.Changed) -> None:
        # No inputs in this screen, provided for completeness.
        pass

    def on_text_area_changed(self, _event: TextArea.Changed) -> None:
        self._sync_save_button()

    def _sync_save_button(self) -> None:
        text_area = self.query_one("#role-prompt-area", TextArea)
        save_btn = self.query_one("#save-role-btn", Button)
        save_btn.disabled = not text_area.text.strip()

    def action_cancel(self) -> None:
        self.dismiss(False)

    def action_delete(self) -> None:
        """Delete this role after confirmation."""
        role = self.app.get_role(self.role_id)
        if role is None:
            self.app.add_error("Role not found", None)
            self.app.bell()
            return

        try:
            affected_agents = self.app.delete_role(self.role_id)
            self.app.role_manager.reload()

            if affected_agents:
                agent_list = ", ".join(affected_agents)
                status_msg = f"Role '{role.name}' deleted. Reset for agents: {agent_list}"
            else:
                status_msg = f"Role '{role.name}' deleted"

            self.app.add_status(status_msg)
            self.dismiss(True)
        except Exception as exc:
            self.app.logger.error("RolePromptEditorScreen:delete failed %s", exc, exc_info=True)
            self.app.add_error(f"Failed to delete role: {exc}", None)
            self.app.bell()

    def action_save(self) -> None:
        text_area = self.query_one("#role-prompt-area", TextArea)
        text = text_area.text.strip()
        if not text:
            self.app.add_status(STATUS_PROMPT_EMPTY_RU)
            self.app.bell()
            return
        try:
            self.app.save_role_prompt(self.role_id, text)
            self.app.add_status(STATUS_ROLE_SAVED)
            self.app.role_manager.reload()
            self.dismiss(True)
        except Exception as exc:
            self.app.logger.error("RolePromptEditorScreen:save failed %s", exc, exc_info=True)
            self.app.add_error(ERROR_ROLE_EDITOR.format(exc=exc), None)
            self.app.bell()


class RoleSelectionScreen(ModalScreen[None]):
    """Panel to manage reusable prompt roles."""

    CSS = """
    RoleSelectionScreen {
        align: center middle;
    }
    #roles-menu {
        width: 60;
        background: $surface;
        border: solid $accent;
        padding: 2;
        layout: vertical;
        height: auto;
        min-height: 16;
    }
    #roles-menu-title {
        text-style: bold;
        content-align: center middle;
        padding-bottom: 1;
    }
    OptionList {
        height: auto;
        min-height: 6;
        border: solid $accent;
    }
    #roles-menu-hint {
        padding-top: 1;
        color: $text-muted;
        text-align: center;
    }
    """

    BINDINGS = [
        Binding("escape", "cancel", "Close"),
    ]

    def __init__(self):
        super().__init__()
        self._mounted = False
        self._pending_focus: str | None = None

    def compose(self) -> ComposeResult:
        options = self._build_options()
        with Container(id="roles-menu"):
            yield Static("Roles", id="roles-menu-title")
            yield OptionList(*options, id="roles-options")
            yield Static("Create reusable prompts that can be assigned to agents later.", id="roles-menu-hint")

    def on_mount(self) -> None:
        option_list = self.query_one("#roles-options", OptionList)
        option_list.highlighted = 0
        option_list.focus()
        self._mounted = True

    def action_cancel(self) -> None:
        self.dismiss(None)

    def on_option_list_option_selected(self, event: OptionList.OptionSelected) -> None:
        option_id = event.option.id
        if option_id == "create":
            self._create_role()
            return
        if option_id and option_id.startswith("role:"):
            role_id = option_id.split(":", 1)[1]
            self._pending_focus = role_id
            self._open_role_editor(role_id)

    def on_screen_resume(self, event: events.ScreenResume) -> None:
        self._refresh_options()

    def _build_options(self) -> list[Option]:
        options: list[Option] = [Option("Create a new role…", id="create")]
        for role in self.app.get_roles():
            options.append(Option(role.name, id=f"role:{role.role_id}"))
        return options

    def _refresh_options(self) -> None:
        if not self._mounted:
            return
        try:
            option_list = self.query_one("#roles-options", OptionList)
        except NoMatches:
            return

        options = self._build_options()
        option_list.clear_options()
        for option in options:
            option_list.add_option(option)

        target_index = 0
        if self._pending_focus:
            target_id = f"role:{self._pending_focus}"
            for idx, option in enumerate(option_list.options):
                if option.id == target_id:
                    target_index = idx
                    break
            self._pending_focus = None

        option_list.highlighted = target_index
        option_list.focus()
        option_list.scroll_to_highlight()

    def _create_role(self) -> None:
        existing_names = [role.name for role in self.app.get_roles()]

        def on_name_selected(name: str | None) -> None:
            if not name:
                return
            try:
                role = self.app.create_role(name)
                self.app.add_status(STATUS_ROLE_CREATED.format(role_name=role.name))
                self._pending_focus = role.role_id
                self.call_later(lambda: self._open_role_editor(role.role_id))
            except Exception as exc:
                self.app.logger.error("RoleSelectionScreen:create_role error %s", exc, exc_info=True)
                self.app.add_error(ERROR_ROLE_CREATE.format(exc=exc), None)

        self.app.push_screen(RoleNamePrompt(existing_names), on_name_selected)

    def _open_role_editor(self, role_id: str) -> None:
        role = self.app.get_role(role_id)
        if role is None:
            self.app.logger.error("RoleSelectionScreen:unknown role %s", role_id)
            return

        def on_close(_result: bool | None) -> None:
            self.app.role_manager.reload()
            self._refresh_options()

        self.app.push_screen(RolePromptEditorScreen(role_id), on_close)


# ============================================================================
# AGENT PROMPT EDITOR PLACEHOLDER
# ============================================================================


class LoggingTextArea(TextArea):
    """TextArea with verbose tracing to diagnose editor crashes."""

    def on_mouse_move(self, event: events.MouseMove) -> None:
        try:
            self.app.logger.trace(
                "AgentPromptEditorScreen:text mouse move x=%s y=%s",
                event.x,
                event.y,
            )
        except Exception:
            pass
        handler = getattr(super(), "on_mouse_move", None)
        if handler:
            return handler(event)
        return None

    def on_focus(self, event: events.Focus) -> None:
        try:
            self.app.logger.trace("AgentPromptEditorScreen:text focus gained")
        except Exception:
            pass
        handler = getattr(super(), "on_focus", None)
        if handler:
            return handler(event)
        return None

    def on_blur(self, event: events.Blur) -> None:
        try:
            self.app.logger.trace("AgentPromptEditorScreen:text focus lost")
        except Exception:
            pass
        handler = getattr(super(), "on_blur", None)
        if handler:
            return handler(event)
        return None

    def on_event(self, event: events.Event) -> None:
        try:
            self.app.logger.trace(
                "AgentPromptEditorScreen:text on_event event=%s",
                type(event).__name__,
            )
        except Exception:
            pass
        handler = getattr(super(), "on_event", None)
        if handler:
            return handler(event)
        return None


class AgentPromptEditorScreen(ModalScreen[bool]):
    """Editor for workspace-specific prompts of an agent."""

    CSS = """
    AgentPromptEditorScreen {
        align: center middle;
    }
    #agent-editor-container {
        width: 100%;
        background: $surface;
        border: solid $accent;
        padding: 0;
        layout: vertical;
        height: 100%;
    }
    #agent-editor-title {
        dock: top;
        height: auto;
        text-style: bold;
        content-align: center middle;
        padding: 0 1;
    }
    #agent-editor-body {
        height: 1fr;
        layout: vertical;
    }
    #agent-editor-hint {
        dock: bottom;
        color: $text-muted;
        height: auto;
        padding: 0 1;
    }
    #agent-text {
        height: 1fr;
        border: solid $accent;
    }
    #agent-editor-actions {
        dock: bottom;
        layout: horizontal;
        align: center middle;
        padding: 1;
        height: auto;
    }
    Button {
        margin: 0 1;
    }
    """

    BINDINGS = [
        Binding("escape", "cancel", "Back"),
        Binding("ctrl+s", "save", "Save"),
    ]

    def __init__(self, agent_name: str):
        super().__init__()
        self.agent_name = agent_name

    def compose(self) -> ComposeResult:
        with Container(id="agent-editor-container"):
            yield Static(f"Prompt editor for {self.agent_name}", id="agent-editor-title")
            with Container(id="agent-editor-body"):
                yield TextArea("", id="agent-text", language="text")
            yield Static("", id="agent-editor-hint")
            with Horizontal(id="agent-editor-actions"):
                yield Button("⬅ Back (Esc)", id="cancel-btn")
                yield Button("💾 Save (Ctrl+S)", id="save-btn", variant="primary")

    def on_mount(self) -> None:
        try:
            self.app.logger.trace("AgentPromptEditorScreen:on_mount agent=%s", self.agent_name)
            text_area = self.query_one("#agent-text", TextArea)
            hint = self.query_one("#agent-editor-hint", Static)

            prompt_text = self.app._load_agent_prompt_from_disk(self.agent_name)
            if prompt_text is None:
                prompt_text = self.app.system_prompt.strip()
                hint.update("Currently using general service prompt. Edit and save to create a custom one.")
            else:
                prompt_path = self.app._get_agent_prompt_file(self.agent_name)
                try:
                    workspace_relative = prompt_path.relative_to(self.app._prompts_root.parent)
                    display_path = f"{workspace_relative}"
                except ValueError:
                    display_path = str(prompt_path)
                hint.update(f"Prompt stored in workspace: {display_path}")

            # Set text and sync button state
            text_area.load_text(prompt_text.strip())
            self._sync_save_button()
            self.app.logger.trace("AgentPromptEditorScreen:initial text set length=%d", len(text_area.text))
            # Defer focus to avoid event cascade during mount
            self.call_later(text_area.focus)
        except Exception as exc:
            self.app.logger.error(f"AgentPromptEditorScreen:on_mount error for {self.agent_name}: {exc}", exc_info=True)
            self.app.add_error(ERROR_EDITOR_INIT.format(agent_name=self.agent_name, exc=exc), None)
            raise

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "save-btn":
            self.action_save()
        elif event.button.id == "cancel-btn":
            self.action_cancel()

    def action_cancel(self) -> None:
        display_name = self.app.get_agent_display_name(self.agent_name)
        self.app.add_status(STATUS_EDIT_CANCELLED_RU.format(display_name=display_name))
        self.app.logger.trace("AgentPromptEditorScreen:cancel triggered %s", self.agent_name)
        self.dismiss(False)

    def on_key(self, event: events.Key) -> None:
        """Stop Escape and Ctrl+S from bubbling to parent app."""
        if event.key == "escape":
            event.stop()
            event.prevent_default()
            self.action_cancel()
            return
        elif event.key == "ctrl+s":
            event.stop()
            event.prevent_default()
            self.action_save()
            return
        elif event.key == "tab":
            event.stop()
            event.prevent_default()
            text_area = self.query_one("#agent-text", TextArea)
            text_area.insert("\t")
            return

    def on_text_area_changed(self, _event: TextArea.Changed) -> None:
        self._sync_save_button()

    def _sync_save_button(self) -> None:
        try:
            text_area = self.query_one("#agent-text", TextArea)
            save_btn = self.query_one("#save-btn", Button)
            save_btn.disabled = not text_area.text.strip()
            self.app.logger.trace("AgentPromptEditorScreen:save button state disabled=%s", save_btn.disabled)
        except Exception as exc:
            self.app.logger.error(f"AgentPromptEditorScreen:_sync_save_button error: {exc}", exc_info=True)

    def action_save(self) -> None:
        text_area = self.query_one("#agent-text", TextArea)
        text = text_area.text.strip()
        if not text:
            self.app.add_status(STATUS_PROMPT_EMPTY_RU)
            self.app.bell()
            self.app.logger.trace("AgentPromptEditorScreen:save rejected empty text for %s", self.agent_name)
            return
        try:
            self.app.set_agent_system_prompt(self.agent_name, text)
            display_name = self.app.get_agent_display_name(self.agent_name)
            self.app.add_status(STATUS_PROMPT_SAVED_RU.format(display_name=display_name))
            self.app.logger.trace("AgentPromptEditorScreen:saved prompt for %s length=%d", self.agent_name, len(text))
            self.dismiss(True)
        except Exception as exc:
            self.app.add_error(ERROR_PROMPT_SAVE.format(exc=exc), self.agent_name)
            self.app.bell()
            self.app.logger.exception("AgentPromptEditorScreen:save error for %s", self.agent_name)


# ============================================================================
# SYSTEM SETTINGS MODAL
# ============================================================================


class SystemSettingsScreen(ModalScreen[None]):
    """System-wide configuration settings."""

    CSS = """
    SystemSettingsScreen {
        align: center middle;
    }
    #system-settings-container {
        width: 70;
        background: $surface;
        border: solid $accent;
        padding: 2;
        layout: vertical;
        height: auto;
    }
    #system-settings-title {
        text-style: bold;
        content-align: center middle;
        padding-bottom: 1;
    }
    #system-settings-form {
        layout: vertical;
        padding: 1 0;
    }
    .system-setting-row {
        layout: horizontal;
        align: left middle;
        padding-bottom: 1;
        min-height: 3;
    }
    .system-setting-label {
        width: 28;
        min-width: 28;
        height: 3;
        text-style: bold;
        color: #e5e7eb;
        content-align: left middle;
    }
    .system-setting-value {
        width: 1fr;
    }
    .system-setting-value Select {
        width: 100%;
    }
    #system-settings-actions {
        layout: horizontal;
        align: center middle;
        padding-top: 1;
    }
    #system-settings-actions Button {
        margin: 0 1;
    }
    """

    BINDINGS = [
        Binding("escape", "close", "Close"),
    ]

    def __init__(self):
        super().__init__()
        self._period_select: Select | None = None

    def compose(self) -> ComposeResult:
        period_options = self._build_period_options()
        self._period_select = Select(period_options, allow_blank=False, id="system-period")

        with Container(id="system-settings-container"):
            yield Static("System Settings", id="system-settings-title")
            with Container(id="system-settings-form"):
                yield Horizontal(
                    Static("Prompt refresh period", classes="system-setting-label"),
                    Container(self._period_select, classes="system-setting-value"),
                    classes="system-setting-row",
                )
            with Container(id="system-settings-actions"):
                yield Button("Save", id="system-save", variant="primary")
                yield Button("Close", id="system-close")

    def on_mount(self) -> None:
        # Load current setting
        current_period = getattr(self.app, 'system_prompt_period', SYSTEM_PROMPT_PERIOD)

        # Set value via call_later to avoid mount-time issues
        def _set_value():
            if self._period_select:
                # Find closest valid value if current not in list
                valid_values = [0, 1, 2, 3, 5, 8, 13, 21, 34, 55, 89]
                if current_period not in valid_values:
                    # Find closest value
                    current_period_safe = min(valid_values, key=lambda x: abs(x - current_period))
                else:
                    current_period_safe = current_period
                self._period_select.value = current_period_safe

        self.call_later(_set_value)

    def action_close(self) -> None:
        self.dismiss(None)

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "system-save":
            self._handle_save()
        elif event.button.id == "system-close":
            self.action_close()

    def _handle_save(self) -> None:
        if self._period_select:
            period = int(self._period_select.value)
            self.app.system_prompt_period = period
            self.app._save_user_settings()
            self.app.add_status(f"System prompt period set to: {period}")
        self.dismiss(None)

    def _build_period_options(self) -> list[tuple[str, int]]:
        """Build period options list (Fibonacci sequence)."""
        return [
            ("Once (only init)", 0),
            ("Every message", 1),
            ("Every 2nd message", 2),
            ("Every 3rd message", 3),
            ("Every 5th message", 5),
            ("Every 8th message", 8),
            ("Every 13th message", 13),
            ("Every 21st message", 21),
            ("Every 34th message", 34),
            ("Every 55th message", 55),
            ("Every 89th message", 89),
        ]


# ============================================================================
# MEMBERS MANAGEMENT MODALS
# ============================================================================


class MembersSelectionScreen(ModalScreen[None]):
    """Two-stage management panel for chat members."""

    CSS = """
    MembersSelectionScreen {
        align: center middle;
    }
    #members-menu {
        width: 60;
        min-height: 18;
        padding: 2;
        layout: vertical;
        height: auto;
        background: #202225;
        border: solid #3f3f46;
    }
    #members-menu-title {
        text-style: bold;
        content-align: center middle;
        padding-bottom: 1;
    }
    OptionList {
        height: auto;
        min-height: 9;
        border: solid #3f3f46;
    }
    #members-menu-hint {
        padding-top: 1;
        color: #9ca3af;
        text-align: center;
    }
    """

    BINDINGS = [
        Binding("escape", "close", "Close"),
    ]

    def __init__(self) -> None:
        super().__init__()
        self._mounted = False
        self._pending_focus: str | None = None

    def compose(self) -> ComposeResult:
        options = self._build_options()
        with Container(id="members-menu"):
            yield Static("Members", id="members-menu-title")
            yield OptionList(*options, id="members-options")
            yield Static("Manage chat participants via list and editor.", id="members-menu-hint")

    def on_mount(self) -> None:
        option_list = self.query_one("#members-options", OptionList)
        if option_list.options:
            option_list.highlighted = 0
        option_list.focus()
        option_list.scroll_to_highlight()
        self._mounted = True

    def action_close(self) -> None:
        self.dismiss(None)

    def on_screen_resume(self, event: events.ScreenResume) -> None:
        self._refresh_options()

    def on_option_list_option_selected(self, event: OptionList.OptionSelected) -> None:
        option_id = event.option.id or ""
        if option_id == "create":
            self._create_member()
            return
        if option_id == "user":
            self._pending_focus = "user"
            self._open_member_editor(None, is_user=True)
            return
        if option_id.startswith("agent:"):
            agent_id = option_id.split(":", 1)[1]
            self._pending_focus = option_id
            self._open_member_editor(agent_id)

    def _build_options(self) -> list[Option]:
        options: list[Option] = [Option("Add member…", id="create")]
        user_display = self.app.get_agent_display_name("User")
        user_avatar = self.app.get_user_avatar() or "👤"
        options.append(Option(f"🔊 - {user_avatar} Local user: {user_display}", id="user"))
        order_raw = list(getattr(self.app, "_participant_order_ids", []))
        seen_ids: set[str] = set()
        order: list[str] = []
        for agent_id in order_raw:
            if agent_id not in seen_ids:
                seen_ids.add(agent_id)
                order.append(agent_id)
        fallback: list[str] = []
        for name, entry in self.app.agents.items():
            agent_id = entry.get('agent_id')
            if agent_id and agent_id not in seen_ids:
                fallback.append(agent_id)
                seen_ids.add(agent_id)
        for agent_id in order + fallback:
            agent_name = self.app._agent_id_to_name.get(agent_id)
            if not agent_name:
                for name, entry in self.app.agents.items():
                    if entry.get('agent_id') == agent_id:
                        agent_name = name
                        break
            if not agent_name:
                continue
            entry = self.app.agents.get(agent_name, {})
            display = self.app.get_agent_display_name(agent_name)
            class_name = entry.get('class_name') or entry.get('backend_id') or "-"
            enabled = entry.get('enabled', True)
            avatar = self.app.get_agent_avatar(agent_name) or "⚪"
            status_icon = "🔊" if enabled else "🔇"
            label = f"{status_icon} - {avatar} {display} ({class_name})"
            options.append(Option(label, id=f"agent:{agent_id}"))
        return options

    def _refresh_options(self) -> None:
        if not self._mounted:
            return
        try:
            option_list = self.query_one("#members-options", OptionList)
        except NoMatches:
            return
        current_index = option_list.highlighted
        current_id = None
        if 0 <= current_index < len(option_list.options):
            current_id = option_list.options[current_index].id
        options = self._build_options()
        option_list.clear_options()
        for option in options:
            option_list.add_option(option)
        default_focus = "create"
        target_id = self._pending_focus or current_id or default_focus
        target_index = 0
        for idx, option in enumerate(option_list.options):
            if option.id == target_id:
                target_index = idx
                break
        if option_list.options:
            option_list.highlighted = target_index
        option_list.focus()
        option_list.scroll_to_highlight()
        self._pending_focus = None

    def _create_member(self) -> None:
        # Check if at least one role exists
        roles = self.app.get_roles(refresh=False)
        if not roles:
            self.app.push_screen(NoRolesWarningScreen())
            return

        try:
            task = self.app.add_member_placeholder()
        except Exception as exc:
            if getattr(self.app, "logger", None):
                self.app.logger.exception("MembersSelectionScreen:add member failed")
            self.app.add_error(f"Failed to add member: {exc}", None)
            return
        if task is None:
            return

        def _after_create(done):
            try:
                profile = done.result()
            except Exception:
                return
            agent_id = profile.descriptor.agent_id
            def _open_new():
                self._pending_focus = f"agent:{agent_id}"
                self._refresh_options()
                self._open_member_editor(agent_id)
            self.call_later(_open_new)

        task.add_done_callback(_after_create)

    def _open_member_editor(self, agent_id: str | None, *, is_user: bool = False) -> None:
        target_id = "user" if is_user else (f"agent:{agent_id}" if agent_id else "user")

        def _on_close(result: str | None) -> None:
            if result == "deleted":
                self._pending_focus = "user"
            else:
                self._pending_focus = target_id
            self._refresh_options()

        self.app.push_screen(MemberEditorScreen(agent_id, is_user=is_user), _on_close)


class MemberEditorScreen(ModalScreen[str | None]):
    """Detailed editor for a single member or the local user."""

    CSS = """
    MemberEditorScreen {
        align: center middle;
    }
    #member-editor {
        width: 90%;
        height: 90%;
        padding: 2;
        layout: grid;
        grid-rows: auto 1fr auto;
        grid-gutter: 1 0;
        background: #202225;
        border: solid #3f3f46;
    }
    #member-editor-title {
        text-style: bold;
        content-align: center middle;
        padding-bottom: 1;
    }
    #member-editor-scroll {
        width: 100%;
        min-height: 0;
        overflow-y: auto;
    }
    #member-form {
        layout: vertical;
        padding-bottom: 0;
    }
    .member-field {
        layout: horizontal;
        align: left middle;
        padding-bottom: 1;
        min-height: 3;
    }
    .member-label {
        width: 18;
        min-width: 18;
        height: 3;
        text-style: bold;
        color: #e5e7eb;
        content-align: left middle;
    }
    .member-value {
        width: 1fr;
    }
    .member-value Input,
    .member-value Select {
        width: 100%;
    }
    .member-value Checkbox {
        width: auto;
    }
    #member-actions {
        layout: horizontal;
        content-align: center middle;
        padding-top: 1;
    }
    #member-actions Button {
        margin: 0 1;
    }
    """

    BINDINGS = [
        Binding("escape", "close", "Close"),
    ]

    def __init__(self, agent_id: str | None, *, is_user: bool = False) -> None:
        super().__init__()
        self._agent_id = agent_id
        self._is_user = is_user
        self._agent_name: str | None = None
        self._initial: dict[str, Any] = {}
        self._title_widget: Static | None = None
        self._enabled_checkbox: Checkbox | None = None
        self._avatar_input: Input | None = None
        self._nickname_input: Input | None = None
        self._color_select: Select | None = None
        self._backend_select: Select | None = None
        self._command_path_input: Input | None = None
        self._role_select: Select | None = None

    def compose(self) -> ComposeResult:
        backend_options = self._build_backend_options()
        role_options = self._build_role_options()
        color_options = self._build_color_options()

        self._title_widget = Static("", id="member-editor-title")
        self._avatar_input = Input(id="member-avatar")
        self._nickname_input = Input(id="member-nickname")
        self._color_select = Select(color_options, allow_blank=True, id="member-color")
        if not self._is_user:
            self._enabled_checkbox = Checkbox(label="", id="member-enabled")
            self._backend_select = Select(backend_options, allow_blank=False, id="member-backend")
            self._command_path_input = Input(id="member-path")
            self._role_select = Select(role_options, allow_blank=False, id="member-role")

        with Container(id="member-editor"):
            yield self._title_widget
            with VerticalScroll(id="member-editor-scroll"):
                with Container(id="member-form"):
                    if not self._is_user and self._enabled_checkbox is not None:
                        yield self._field_row("Enabled", self._enabled_checkbox)
                    yield self._field_row("Avatar", self._avatar_input)
                    yield self._field_row("Nickname", self._nickname_input)
                    yield self._field_row("Color", self._color_select)
                    if not self._is_user and self._backend_select is not None:
                        yield self._field_row("Agent type", self._backend_select)
                    if not self._is_user and self._command_path_input is not None:
                        yield self._field_row("Command path", self._command_path_input)
                    if not self._is_user and self._role_select is not None:
                        yield self._field_row("Role", self._role_select)
            with Container(id="member-actions"):
                yield Button("Save", id="member-save", variant="primary")
                if not self._is_user:
                    yield Button("Delete", id="member-delete", variant="error")
                yield Button("Close", id="member-close")

    def action_close(self) -> None:
        self.dismiss(None)

    def on_mount(self) -> None:
        try:
            self._populate_fields()
        except Exception as exc:
            if getattr(self.app, "logger", None):
                self.app.logger.exception("MemberEditorScreen:on_mount failed")
            self.app.add_error(f"Failed to load member: {exc}", None)
            self.dismiss(None)

    def on_button_pressed(self, event: Button.Pressed) -> None:
        button_id = event.button.id
        if button_id == "member-save":
            self._handle_save()
            return
        if button_id == "member-delete":
            self._handle_delete()
            return
        if button_id == "member-close":
            self.action_close()

    def _field_row(self, label: str, widget) -> Horizontal:
        return Horizontal(
            Static(label, classes="member-label"),
            Container(widget, classes="member-value"),
            classes="member-field",
        )

    def _build_backend_options(self) -> list[tuple[str, str]]:
        options = self.app.list_backend_options()
        if options:
            return options
        return [("-", "")]

    def _build_role_options(self) -> list[tuple[str, str]]:
        roles = self.app.get_roles()
        options: list[tuple[str, str]] = [("-", "none")]
        for role in roles:
            options.append((role.name, role.role_id))
        return options

    def _build_color_options(self) -> list[tuple[str, str]]:
        """Build color palette with rainbow colors in 4 tones each."""
        options: list[tuple[str, str]] = [
            ("[#FFFFFF]█████████[/#FFFFFF] White", "#FFFFFF"),
            ("[#CCCCCC]█████████[/#CCCCCC] Light Gray", "#CCCCCC"),
            ("[#888888]█████████[/#888888] Gray", "#888888"),
            ("[#444444]█████████[/#444444] Dark Gray", "#444444"),
            ("[black on white]█████████[/black on white] Black", "#000000"),
            # Red
            ("[#FF4444]█████████[/#FF4444] Red Bright", "#FF4444"),
            ("[#CC3333]█████████[/#CC3333] Red Medium", "#CC3333"),
            ("[#993333]█████████[/#993333] Red Muted", "#993333"),
            ("[#662222]█████████[/#662222] Red Dark", "#662222"),
            # Orange
            ("[#FF8844]█████████[/#FF8844] Orange Bright", "#FF8844"),
            ("[#CC6633]█████████[/#CC6633] Orange Medium", "#CC6633"),
            ("[#995533]█████████[/#995533] Orange Muted", "#995533"),
            ("[#663322]█████████[/#663322] Orange Dark", "#663322"),
            # Yellow
            ("[#FFDD44]█████████[/#FFDD44] Yellow Bright", "#FFDD44"),
            ("[#CCAA33]█████████[/#CCAA33] Yellow Medium", "#CCAA33"),
            ("[#998833]█████████[/#998833] Yellow Muted", "#998833"),
            ("[#665522]█████████[/#665522] Yellow Dark", "#665522"),
            # Green
            ("[#44FF44]█████████[/#44FF44] Green Bright", "#44FF44"),
            ("[#33CC33]█████████[/#33CC33] Green Medium", "#33CC33"),
            ("[#339933]█████████[/#339933] Green Muted", "#339933"),
            ("[#226622]█████████[/#226622] Green Dark", "#226622"),
            # Cyan
            ("[#44FFFF]█████████[/#44FFFF] Cyan Bright", "#44FFFF"),
            ("[#33CCCC]█████████[/#33CCCC] Cyan Medium", "#33CCCC"),
            ("[#339999]█████████[/#339999] Cyan Muted", "#339999"),
            ("[#226666]█████████[/#226666] Cyan Dark", "#226666"),
            # Blue
            ("[#4444FF]█████████[/#4444FF] Blue Bright", "#4444FF"),
            ("[#3333CC]█████████[/#3333CC] Blue Medium", "#3333CC"),
            ("[#333399]█████████[/#333399] Blue Muted", "#333399"),
            ("[#222266]█████████[/#222266] Blue Dark", "#222266"),
            # Purple
            ("[#BB44FF]█████████[/#BB44FF] Purple Bright", "#BB44FF"),
            ("[#9933CC]█████████[/#9933CC] Purple Medium", "#9933CC"),
            ("[#773399]█████████[/#773399] Purple Muted", "#773399"),
            ("[#552266]█████████[/#552266] Purple Dark", "#552266"),
            # Pink
            ("[#FF44BB]█████████[/#FF44BB] Pink Bright", "#FF44BB"),
            ("[#CC3399]█████████[/#CC3399] Pink Medium", "#CC3399"),
            ("[#993377]█████████[/#993377] Pink Muted", "#993377"),
            ("[#662255]█████████[/#662255] Pink Dark", "#662255"),
        ]
        return options

    def _populate_fields(self) -> None:
        if self._title_widget is None or self._avatar_input is None or self._nickname_input is None or self._color_select is None:
            raise RuntimeError("Member editor widgets not initialized")

        if self._is_user:
            nickname = self.app.get_user_nickname() or ""
            avatar_override = self.app.get_user_avatar() or ""
            avatar_default = "👤"
            color_override = self.app.get_user_color(override_only=True) or ""
            color_default = self.app.get_user_color() or ""
            self._title_widget.update("Local user")
            self._avatar_input.placeholder = avatar_default
            self._avatar_input.value = avatar_override
            self._nickname_input.placeholder = "Nickname"
            self._nickname_input.value = nickname
            if self._color_select:
                if color_override:
                    self._color_select.value = color_override
                # If no override, Select will show blank (allow_blank=True)
            self._initial = {
                "nickname": nickname,
                "avatar": avatar_override,
                "color": color_override,
            }
            self.call_later(self._nickname_input.focus)
            return

        agent_id = self._agent_id
        if not agent_id:
            raise ValueError("Agent identifier is required")
        agent_name = self.app._agent_id_to_name.get(agent_id)
        if not agent_name:
            for name, entry in self.app.agents.items():
                if entry.get('agent_id') == agent_id:
                    agent_name = name
                    break
        if not agent_name:
            raise KeyError(f"Unknown agent id {agent_id}")
        self._agent_name = agent_name

        entry = self.app.agents.get(agent_name, {})
        profile = self.app.agent_profiles.get(agent_id)
        descriptor = profile.descriptor if profile else None

        display_name = self.app.get_agent_display_name(agent_name)
        class_name = entry.get('class_name') or entry.get('backend_id') or "-"
        self._title_widget.update(f"Member: {display_name} ({class_name})")

        enabled = bool(entry.get('enabled', descriptor.default_enabled if descriptor else True))
        nickname = self.app.get_agent_nickname(agent_name) or ""
        avatar_override = profile.overrides.avatar if profile else None
        avatar_default = entry.get('avatar_default') or self.app._resolve_agent_avatar(agent_name)
        color_override_raw = profile.overrides.color if profile and profile.overrides.color else entry.get('color_override')
        color_override = self.app._normalize_color_value(color_override_raw) if color_override_raw else ""
        color_default = entry.get('color_default') or self.app.get_default_agent_color()
        backend_value = self.app.get_agent_backend_id(agent_name) or self.app.get_default_backend_id() or ""
        command_override = profile.overrides.command_path if profile and profile.overrides.command_path else ""
        current_role = self.app.get_agent_role(agent_name) or "none"
        executable_hint = entry.get('executable') or agent_id

        if self._enabled_checkbox:
            self._enabled_checkbox.value = enabled
        self._avatar_input.placeholder = avatar_default or "Emoji"
        self._avatar_input.value = avatar_override or ""
        self._nickname_input.placeholder = descriptor.display_name if descriptor else agent_name
        self._nickname_input.value = nickname
        if self._color_select:
            if color_override:
                self._color_select.value = color_override
            # If no override, Select will show blank (allow_blank=True)
        if self._backend_select:
            backend_options = self._build_backend_options()

            def _apply_backend_options() -> None:
                self._backend_select.set_options(backend_options)
                if backend_value:
                    self._backend_select.value = backend_value

            self.call_later(_apply_backend_options)
        if self._command_path_input:
            self._command_path_input.placeholder = executable_hint
            self._command_path_input.value = command_override
        if self._role_select:
            role_options = self._build_role_options()
            if current_role not in [value for _, value in role_options] and current_role not in (None, "none"):
                role_options.append((f"Missing {current_role}", current_role))
            disabled = len(role_options) <= 1

            def _apply_role_options() -> None:
                self._role_select.set_options(role_options)
                self._role_select.value = current_role
                self._role_select.disabled = disabled

            self.call_later(_apply_role_options)

        self._initial = {
            "enabled": enabled,
            "nickname": nickname,
            "avatar": avatar_override or "",
            "color": color_override or "",
            "backend": backend_value or "",
            "path": command_override,
            "role": current_role,
        }
        self.call_later(self._nickname_input.focus)

    def _handle_save(self) -> None:
        if self._is_user:
            nickname = self._nickname_input.value.strip() if self._nickname_input else ""
            avatar = self._avatar_input.value.strip() if self._avatar_input else ""
            color = str(self._color_select.value) if self._color_select and self._color_select.value != Select.BLANK else ""
            if nickname != self._initial.get('nickname'):
                self.app.set_user_nickname(nickname or None)
            if avatar != self._initial.get('avatar'):
                self.app.set_user_avatar(avatar or None)
            if color != self._initial.get('color'):
                self.app.set_user_color(color or None)
            self.dismiss("saved")
            return

        agent_name = self._agent_name
        if not agent_name:
            self.app.add_error("Unknown agent", None)
            return

        enabled = bool(self._enabled_checkbox.value) if self._enabled_checkbox else True
        nickname = self._nickname_input.value.strip() if self._nickname_input else ""
        avatar = self._avatar_input.value.strip() if self._avatar_input else ""
        color = str(self._color_select.value) if self._color_select and self._color_select.value != Select.BLANK else ""
        backend_id = self._backend_select.value if self._backend_select else ""
        command_path = self._command_path_input.value.strip() if self._command_path_input else ""
        role_id = self._role_select.value if self._role_select else "none"

        if enabled != self._initial.get('enabled'):
            self.app.set_agent_enabled(agent_name, enabled)
        if nickname != self._initial.get('nickname'):
            self.app.set_agent_nickname(agent_name, nickname or None)
        if avatar != self._initial.get('avatar'):
            self.app.set_agent_avatar(agent_name, avatar or None)
        if color != self._initial.get('color'):
            self.app.set_agent_color(agent_name, color or None)
        if backend_id and backend_id != self._initial.get('backend'):
            self.app.set_agent_backend(agent_name, backend_id)
        if command_path != self._initial.get('path'):
            self.app.set_agent_path(agent_name, command_path or None)
        if role_id != self._initial.get('role'):
            self.app.set_agent_role(agent_name, None if role_id == "none" else role_id)

        self.dismiss("saved")

    def _handle_delete(self) -> None:
        if self._is_user or not self._agent_id:
            return
        task = self.app.remove_member(self._agent_id)
        if task is None:
            return

        def _after_remove(done):
            try:
                removed = done.result()
            except Exception:
                return
            if removed:
                async def _close() -> None:
                    await self.dismiss("deleted")
                asyncio.create_task(_close())

        task.add_done_callback(_after_remove)


# ============================================================================
# CHAT COMPOSER
# ============================================================================



__all__ = [
    'PromptEditorScreen',
    'PromptSelectionScreen',
    'RoleSelectionScreen',
    'RolePromptEditorScreen',
    'RoleNamePrompt',
    'NoRolesWarningScreen',
    'AgentPromptEditorScreen',
    'SystemSettingsScreen',
    'MembersSelectionScreen',
    'MemberEditorScreen',
    'LoggingTextArea',
]
