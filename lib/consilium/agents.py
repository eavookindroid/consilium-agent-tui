"""
Consilium Agent - Descriptor Definitions

Provides core data structures for describing built-in and user-defined
agents. This module focuses on immutable metadata that will later be combined
with runtime state stored elsewhere.

Copyright (c) 2025 Artel Team
Licensed under Artel Team Non-Commercial License
"""

from __future__ import annotations

from dataclasses import dataclass, field, fields
from typing import Any, Iterator, Tuple

AgentHandlerId = str


@dataclass(slots=True)
class AgentDescriptor:
    """
    Immutable description of an agent available inside Consilium.

    The descriptor captures metadata that should remain stable between sessions.
    Runtime fields such as session identifiers or process handles are managed
    separately by the session manager or courier.
    """

    agent_id: str
    handler: AgentHandlerId
    class_name: str
    display_name: str
    description: str
    color: str
    default_executable: str
    default_enabled: bool = False
    default_role: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        """Return a serializable representation of core descriptor fields."""
        return {
            "id": self.agent_id,
            "handler": self.handler,
            "class_name": self.class_name,
            "display_name": self.display_name,
            "description": self.description,
            "color": self.color,
            "default_executable": self.default_executable,
            "default_enabled": self.default_enabled,
            "default_role": self.default_role,
            "metadata": dict(self.metadata),
        }


@dataclass(slots=True)
class AgentOverrides:
    """
    User-controlled overrides stored in settings.

    Each field is optional; a value of None means that the default from the
    descriptor should be used. This separation allows us to keep the descriptor
    immutable while still reflecting per-workspace customisation.
    """

    enabled: bool | None = None
    nickname: str | None = None
    avatar: str | None = None
    command_path: str | None = None
    role_id: str | None = None
    display_name: str | None = None
    description: str | None = None
    color: str | None = None
    backend_id: str | None = None
    metadata: dict[str, Any] | None = None

    def as_dict(self) -> dict[str, Any]:
        """Serialize overrides into JSON-friendly dict (skip None)."""
        data: dict[str, Any] = {}
        if self.enabled is not None:
            data["enabled"] = self.enabled
        if self.nickname is not None:
            data["nickname"] = self.nickname
        if self.avatar is not None:
            data["avatar"] = self.avatar
        if self.command_path is not None:
            data["command_path"] = self.command_path
        if self.role_id is not None:
            data["role_id"] = self.role_id
        if self.display_name is not None:
            data["display_name"] = self.display_name
        if self.description is not None:
            data["description"] = self.description
        if self.color is not None:
            data["color"] = self.color
        if self.backend_id is not None:
            data["backend_id"] = self.backend_id
        if self.metadata is not None:
            data["metadata"] = dict(self.metadata)
        return data

    def update_from(self, other: "AgentOverrides", *, allow_none: bool = False) -> None:
        """In-place update from another overrides instance."""
        for field_info in fields(self):
            value = getattr(other, field_info.name)
            if value is not None or allow_none:
                setattr(self, field_info.name, value)

    def items(self) -> Iterator[Tuple[str, Any]]:
        """Iterate over non-None values (utility for registry)."""
        for key, value in self.as_dict().items():
            yield key, value


@dataclass(slots=True)
class AgentProfile:
    """
    Resolved agent profile, combining descriptor with user overrides.

    Consumers should treat `descriptor` as read-only. Any modifications must be
    applied through `overrides` and then persisted via the registry layer.
    """

    descriptor: AgentDescriptor
    overrides: AgentOverrides = field(default_factory=AgentOverrides)

    @property
    def agent_id(self) -> str:
        return self.descriptor.agent_id

    def get_display_name(self) -> str:
        """Return effective display name (override or descriptor default)."""
        return (self.overrides.display_name or self.descriptor.display_name).strip()

    def get_description(self) -> str:
        """Return effective description."""
        return (self.overrides.description or self.descriptor.description).strip()

    def get_color(self) -> str:
        """Return effective color code."""
        color = self.overrides.color or self.descriptor.color
        if isinstance(color, str) and color.strip():
            return color.strip()
        return "#D0D0D0"

    def get_avatar(self) -> str:
        """Return effective avatar symbol."""
        avatar = self.overrides.avatar
        if avatar and avatar.strip():
            return avatar.strip()
        descriptor_avatar = None
        metadata = self.descriptor.metadata
        if isinstance(metadata, dict):
            descriptor_avatar = metadata.get("avatar")
        if isinstance(descriptor_avatar, str) and descriptor_avatar.strip():
            return descriptor_avatar.strip()
        return ""

    def get_command_path(self) -> str:
        """Return effective command path or default executable."""
        command_path = self.overrides.command_path
        if command_path:
            return command_path.strip()
        return self.descriptor.default_executable

    def is_enabled_by_default(self) -> bool:
        """Show default enablement state (ignores override)."""
        return self.descriptor.default_enabled

    def is_enabled(self) -> bool:
        """Return current enable flag including override."""
        if self.overrides.enabled is None:
            return self.descriptor.default_enabled
        return bool(self.overrides.enabled)

    def get_backend_id(self) -> str | None:
        backend_id = self.overrides.backend_id
        if backend_id and backend_id.strip():
            return backend_id.strip()
        return (self.descriptor.handler or "").strip() or None


__all__ = [
    "AgentDescriptor",
    "AgentOverrides",
    "AgentProfile",
    "AgentHandlerId",
]
