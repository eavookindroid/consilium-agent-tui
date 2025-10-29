"""
Consilium Agent - Registry Interface

Defines an event-driven registry responsible for loading agent descriptors from
user settings, applying runtime overrides, and notifying subscribers about
changes. Implementations should avoid synchronous side effects on listeners and
emit events through the provided asynchronous hooks.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Awaitable, Callable, Dict, List, Optional

from .agents import AgentDescriptor, AgentProfile, AgentOverrides

RegistryListener = Callable[["AgentRegistryEvent"], Awaitable[None]]

DESCRIPTOR_KEYS = {
    "id",
    "handler",
    "class_name",
    "display_name",
    "description",
    "color",
    "default_executable",
    "default_enabled",
    "default_role",
    "metadata",
}

OVERRIDE_KEYS = {
    "enabled",
    "nickname",
    "avatar",
    "command_path",
    "role_id",
    "display_name",
    "description",
    "color",
    "backend_id",
    "metadata",
}



@dataclass(slots=True)
class AgentRegistryEvent:
    """Represents a change emitted by the agent registry."""

    event_type: str
    agent_id: str
    profile: AgentProfile | None = None
    payload: dict | None = None


class AgentRegistry:
    """
    Event-driven agent store that backs Consilium runtime.

    Responsibilities:
    - load agent descriptors from settings (`members` section)
    - keep resolved `AgentProfile` instances in memory
    - persist user overrides on demand
    - notify subscribers about structural or override changes
    """

    def __init__(self, settings_path: Path) -> None:
        self._settings_path = settings_path
        self._profiles: Dict[str, AgentProfile] = {}
        self._listeners: List[RegistryListener] = []
        self._settings_cache: Dict[str, Any] = {}
        self._loaded = False
        self._lock: asyncio.Lock | None = None
        self._lock_loop: asyncio.AbstractEventLoop | None = None
        self._logger = logging.getLogger("AgentRegistry")

    async def load(self) -> None:
        """Load profiles from settings and emit `registry-loaded` event."""
        lock = self._ensure_lock()
        async with lock:
            count = self._load_without_lock()

        await self._emit(
            AgentRegistryEvent(
                event_type="registry-loaded",
                agent_id="*",
                payload={"count": count},
            )
        )

    def load_sync(self) -> None:
        """Synchronous variant used during startup before event loop runs."""
        self._load_without_lock()

    def list_profiles(self) -> List[AgentProfile]:
        """Return the current snapshot of agent profiles."""
        return list(self._profiles.values())

    def get_profile(self, agent_id: str) -> Optional[AgentProfile]:
        """Return single profile or None."""
        return self._profiles.get(agent_id)

    async def create_member(
        self,
        *,
        display_name: str,
        handler: str,
        class_name: str,
        default_executable: str,
        description: str | None = None,
        color: str | None = None,
        default_role: str | None = None,
        default_enabled: bool = False,
        overrides: AgentOverrides | None = None,
        metadata: Dict[str, Any] | None = None,
    ) -> AgentProfile:
        """
        Create a brand-new member profile, persist it, and emit `profile-created`.
        """
        overrides_obj = overrides or AgentOverrides()
        overrides_copy = AgentOverrides()
        overrides_copy.update_from(overrides_obj, allow_none=True)
        metadata_payload = metadata or {}

        lock = self._ensure_lock()
        async with lock:
            if not self._loaded:
                self._load_without_lock()

            agent_id = self._generate_placeholder_id(display_name)
            effective_color = color or self._derive_color(agent_id)
            descriptor = AgentDescriptor(
                agent_id=agent_id,
                handler=handler,
                class_name=class_name,
                display_name=display_name,
                description=description or f"Custom agent '{display_name}'",
                color=effective_color,
                default_executable=default_executable,
                default_enabled=bool(default_enabled),
                default_role=default_role,
                metadata=dict(metadata_payload),
            )

            profile = AgentProfile(descriptor=descriptor, overrides=overrides_copy)
            self._profiles[agent_id] = profile
            self._update_settings_entry(profile)
            self._write_settings(self._settings_cache)
            self._loaded = True

        await self._emit(
            AgentRegistryEvent(
                event_type="profile-created",
                agent_id=descriptor.agent_id,
                profile=profile,
            )
        )
        return profile

    async def delete_member(self, agent_id: str) -> bool:
        """Remove an existing member profile and emit `profile-removed`."""
        lock = self._ensure_lock()
        async with lock:
            if not self._loaded:
                self._load_without_lock()

            profile = self._profiles.pop(agent_id, None)
            if profile is None:
                return False

            self._remove_settings_entry(agent_id)
            self._write_settings(self._settings_cache)

        await self._emit(
            AgentRegistryEvent(
                event_type="profile-removed",
                agent_id=agent_id,
                profile=profile,
            )
        )
        return True

    async def upsert_profile(
        self,
        descriptor: AgentDescriptor,
        overrides: AgentOverrides | None = None,
    ) -> AgentProfile:
        """
        Insert or update agent profile, persisting changes and emitting
        `profile-updated` or `profile-created` event.
        """
        lock = self._ensure_lock()
        async with lock:
            if not self._loaded:
                self._load_without_lock()
            agent_id = descriptor.agent_id
            existing = self._profiles.get(agent_id)
            overrides = overrides or AgentOverrides()

            profile = AgentProfile(descriptor=descriptor, overrides=overrides)
            self._profiles[agent_id] = profile

            self._update_settings_entry(profile)
            self._write_settings(self._settings_cache)

        event_type = "profile-updated" if existing else "profile-created"
        await self._emit(
            AgentRegistryEvent(
                event_type=event_type,
                agent_id=descriptor.agent_id,
                profile=profile,
            )
        )
        return profile

    async def update_overrides(
        self,
        agent_id: str,
        overrides: AgentOverrides,
        *,
        allow_none: bool = False,
    ) -> AgentProfile:
        """Update overrides for existing agent and emit `profile-updated`."""
        lock = self._ensure_lock()
        async with lock:
            profile = self._profiles.get(agent_id)
            if profile is None:
                raise KeyError(f"Agent '{agent_id}' not found in registry")

            profile.overrides.update_from(overrides, allow_none=allow_none)
            self._logger.trace(
                "Registry received override update %s: %s",
                agent_id,
                dict(overrides.items()),
            )

            self._update_settings_entry(profile)
            self._write_settings(self._settings_cache)

        await self._emit(
            AgentRegistryEvent(
                event_type="profile-updated",
                agent_id=agent_id,
                profile=profile,
                payload={"changes": dict(overrides.items())},
            )
        )
        return profile

    async def patch_overrides(
        self,
        agent_id: str,
        updates: Dict[str, Any],
    ) -> AgentProfile:
        """Update specific override fields using a mapping."""
        if not updates:
            return self._profiles[agent_id]

        valid_updates = {
            key: value
            for key, value in updates.items()
            if key in OVERRIDE_KEYS
        }
        lock = self._ensure_lock()
        async with lock:
            profile = self._profiles.get(agent_id)
            if profile is None:
                raise KeyError(f"Agent '{agent_id}' not found in registry")

            for key, value in valid_updates.items():
                setattr(profile.overrides, key, value)

            if valid_updates:
                self._logger.trace(
                    "Registry patch overrides %s: %s",
                    agent_id,
                    valid_updates,
                )

            self._update_settings_entry(profile)
            self._write_settings(self._settings_cache)

        if not valid_updates:
            return profile

        await self._emit(
            AgentRegistryEvent(
                event_type="profile-updated",
                agent_id=agent_id,
                profile=profile,
                payload={"changes": valid_updates},
            )
        )
        return profile

    async def remove_profile(self, agent_id: str) -> None:
        """Remove profile from registry and emit `profile-removed`."""
        lock = self._ensure_lock()
        async with lock:
            removed = self._profiles.pop(agent_id, None)
            if removed is None:
                self._logger.warning("Attempted to remove unknown agent '%s'", agent_id)
                return
            self._remove_settings_entry(agent_id)
            self._write_settings(self._settings_cache)

        await self._emit(
            AgentRegistryEvent(
                event_type="profile-removed",
                agent_id=agent_id,
            )
        )

    async def subscribe(self, listener: RegistryListener) -> None:
        """Register asynchronous listener for registry events."""
        self._listeners.append(listener)

    async def unsubscribe(self, listener: RegistryListener) -> None:
        """Remove listener from registry notifications."""
        try:
            self._listeners.remove(listener)
        except ValueError:
            pass

    async def _emit(self, event: AgentRegistryEvent) -> None:
        """Emit event to all listeners (await sequentially)."""
        for listener in list(self._listeners):
            await listener(event)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _ensure_lock(self) -> asyncio.Lock:
        loop = asyncio.get_running_loop()
        if self._lock is None or self._lock_loop is not loop:
            self._lock = asyncio.Lock()
            self._lock_loop = loop
        return self._lock

    def _load_without_lock(self) -> int:
        settings = self._read_settings()
        members = settings.get("members")
        if not isinstance(members, list):
            migrated = self._maybe_migrate_legacy(settings)
            members = migrated if migrated is not None else []
            settings["members"] = members
            self._write_settings(settings)
        else:
            migrated = self._maybe_migrate_legacy(settings)
            if migrated is not None:
                members = migrated
                settings["members"] = members
                self._write_settings(settings)
        self._settings_cache = settings
        self._profiles = {}

        for raw_entry in members:
            if not isinstance(raw_entry, dict):
                self._logger.warning("Skipping malformed member entry: %r", raw_entry)
                continue
            try:
                descriptor = self._entry_to_descriptor(raw_entry)
            except KeyError as exc:
                self._logger.error("Member missing required key %s: %r", exc, raw_entry)
                continue
            except Exception:
                self._logger.exception("Failed to parse member descriptor: %r", raw_entry)
                continue

            overrides_data = raw_entry.get("overrides")
            overrides = self._entry_to_overrides(overrides_data)
            profile = AgentProfile(descriptor=descriptor, overrides=overrides)
            self._profiles[descriptor.agent_id] = profile
            self._logger.trace(
                "Registry loaded profile %s overrides=%s",
                descriptor.agent_id,
                profile.overrides.as_dict(),
            )

        self._loaded = True
        return len(self._profiles)

    def _read_settings(self) -> Dict[str, Any]:
        if not self._settings_path.exists():
            return {}
        try:
            with self._settings_path.open("r", encoding="utf-8") as handle:
                return json.load(handle)
        except Exception as exc:
            self._logger.exception("Failed to read settings file: %s", exc)
            return {}

    def _write_settings(self, settings: Dict[str, Any]) -> None:
        self._settings_path.parent.mkdir(parents=True, exist_ok=True)
        with self._settings_path.open("w", encoding="utf-8") as handle:
            json.dump(settings, handle, indent=2, ensure_ascii=False)
        members = settings.get("members") if isinstance(settings.get("members"), list) else []
        self._logger.trace("Registry wrote settings (%d members)", len(members))
        if members:
            snapshot = {
                entry.get("id"): entry.get("overrides")
                for entry in members
                if isinstance(entry, dict)
            }
            self._logger.trace("Registry overrides snapshot: %s", snapshot)

    def _entry_to_descriptor(self, entry: Dict[str, Any]) -> AgentDescriptor:
        descriptor_kwargs = {
            key: entry[key]
            for key in DESCRIPTOR_KEYS
            if key in entry
        }
        identifier = descriptor_kwargs.pop("id", entry.get("agent_id"))
        if not isinstance(identifier, str) or not identifier.strip():
            raise KeyError("id")
        descriptor_kwargs["agent_id"] = identifier.strip()
        # Ensure metadata default is dict
        metadata = descriptor_kwargs.get("metadata")
        if metadata is None:
            descriptor_kwargs["metadata"] = {}
        return AgentDescriptor(**descriptor_kwargs)  # type: ignore[arg-type]

    def _entry_to_overrides(self, overrides_entry: Any) -> AgentOverrides:
        if not isinstance(overrides_entry, dict):
            return AgentOverrides()
        filtered: Dict[str, Any] = {
            key: overrides_entry[key]
            for key in OVERRIDE_KEYS
            if key in overrides_entry
        }
        return AgentOverrides(**filtered)  # type: ignore[arg-type]

    def _update_settings_entry(self, profile: AgentProfile) -> None:
        members = self._settings_cache.setdefault("members", [])
        if not isinstance(members, list):
            members = []
            self._settings_cache["members"] = members

        entry = None
        for existing in members:
            if isinstance(existing, dict) and existing.get("id") == profile.agent_id:
                entry = existing
                break

        serialized_descriptor = profile.descriptor.as_dict()
        serialized_overrides = profile.overrides.as_dict()
        self._logger.trace(
            "Registry updating entry %s overrides=%s",
            profile.agent_id,
            serialized_overrides,
        )

        if entry is None:
            entry = serialized_descriptor
            if serialized_overrides:
                entry["overrides"] = serialized_overrides
            members.append(entry)
        else:
            entry.clear()
            entry.update(serialized_descriptor)
            if serialized_overrides:
                entry["overrides"] = serialized_overrides
            elif "overrides" in entry:
                entry.pop("overrides", None)

    def _remove_settings_entry(self, agent_id: str) -> None:
        members = self._settings_cache.get("members")
        if not isinstance(members, list):
            return
        new_members = [
            entry for entry in members
            if not (isinstance(entry, dict) and entry.get("id") == agent_id)
        ]
        self._settings_cache["members"] = new_members

    def _maybe_migrate_legacy(self, settings: Dict[str, Any]) -> list[Dict[str, Any]] | None:
        members = settings.get("members")
        legacy_enabled = settings.get("agents_enabled")
        legacy_roles = settings.get("agent_roles")

        if isinstance(members, list) and members and (
            isinstance(legacy_enabled, dict) or isinstance(legacy_roles, dict)
        ):
            self._logger.trace(
                "Registry skipping legacy migration (members already configured); removing legacy keys",
            )
            updated = False
            if "agents_enabled" in settings:
                settings.pop("agents_enabled", None)
                updated = True
            if "agent_roles" in settings:
                settings.pop("agent_roles", None)
                updated = True
            if updated:
                self._write_settings(settings)
            return None

        if not isinstance(legacy_enabled, dict) and not isinstance(legacy_roles, dict):
            return None

        legacy_enabled = legacy_enabled or {}
        legacy_roles = legacy_roles or {}

        used_ids: set[str] = set()
        migrated_members: list[Dict[str, Any]] = []
        overrides_per_agent: Dict[str, AgentOverrides] = {}

        items = legacy_enabled.items()
        if isinstance(items, list):
            iterable = items
        else:
            iterable = list(items)

        for legacy_name, enabled in iterable:
            agent_id = self._generate_unique_agent_id(legacy_name, used_ids)

            descriptor = AgentDescriptor(
                agent_id=agent_id,
                handler=self._infer_backend_id(legacy_name),
                class_name=self._derive_class_name(legacy_name),
                display_name=legacy_name,
                description=f"Migrated agent '{legacy_name}'",
                color=self._derive_color(agent_id),
                default_executable=agent_id,
                default_enabled=bool(enabled),
                default_role=legacy_roles.get(legacy_name),
                metadata={},
            )

            overrides = AgentOverrides()
            overrides.enabled = bool(enabled)
            role_override = legacy_roles.get(legacy_name)
            if isinstance(role_override, str) and role_override.strip():
                overrides.role_id = role_override.strip()

            overrides_per_agent[agent_id] = overrides

            entry = descriptor.as_dict()
            override_payload = overrides.as_dict()
            if override_payload:
                entry["overrides"] = override_payload
            migrated_members.append(entry)

        # Legacy workspace overrides are no longer loaded; global settings.json is the single source of truth.
        # Update migrated members with final overrides values
        for entry in migrated_members:
            agent_id = entry.get("id")
            overrides = overrides_per_agent.get(agent_id)
            if overrides:
                overrides_dict = overrides.as_dict()
                if overrides_dict:
                    entry["overrides"] = overrides_dict
                elif "overrides" in entry:
                    entry.pop("overrides", None)

        # Remove legacy keys to avoid repeated migration
        settings.pop("agents_enabled", None)
        settings.pop("agent_roles", None)

        return migrated_members

    def _generate_unique_agent_id(self, legacy_name: str, used: set[str]) -> str:
        base = self._slugify(legacy_name)
        candidate = base or "agent"
        index = 2
        while candidate in used:
            candidate = f"{base}-{index}"
            index += 1
        used.add(candidate)
        return candidate

    def _generate_placeholder_id(self, display_name: str) -> str:
        base = self._slugify(display_name) or "agent"
        existing: set[str] = set(self._profiles.keys())
        members = self._settings_cache.get("members")
        if isinstance(members, list):
            for entry in members:
                if isinstance(entry, dict):
                    entry_id = entry.get("id")
                    if isinstance(entry_id, str):
                        existing.add(entry_id)

        candidate = base
        suffix = 2
        while candidate in existing:
            candidate = f"{base}-{suffix}"
            suffix += 1
        return candidate

    def _slugify(self, value: str) -> str:
        value = value.strip().lower()
        value = re.sub(r"[^a-z0-9]+", "-", value)
        return value.strip("-")

    def _derive_color(self, agent_id: str) -> str:
        digest = hashlib.sha1(agent_id.encode("utf-8")).hexdigest()
        return f"#{digest[:6]}"

    def _derive_class_name(self, legacy_name: str) -> str:
        normalized = legacy_name.strip()
        if not normalized:
            return "Migrated Agent"
        return f"{normalized} Agent"

    def _infer_backend_id(self, legacy_name: str) -> str:
        name = legacy_name.lower()
        if "claude" in name:
            return "claude"
        if "gemini" in name:
            return "gemini"
        return "codex"
