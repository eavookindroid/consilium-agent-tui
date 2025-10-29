"""
Role management utilities for Consilium Agent.

Provides persistence for reusable prompt roles stored in
~/.consilium/roles/<role_id>/ and bootstraps default roles shipped with the app.

Each role directory contains:
    metadata.json  -> {"id": "...", "name": "...", "prompt": "...", "locale": "..."}
"""

from __future__ import annotations

import locale
import json
import logging
import os
import shutil
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional

logger = logging.getLogger("ConsiliumRoles")

DEFAULT_ROLES_LOCALE = "en"


@dataclass(frozen=True)
class Role:
    """Represents a reusable prompt role."""

    role_id: str
    name: str
    directory: Path
    prompt: str = ""
    locale: Optional[str] = None

    @property
    def metadata_path(self) -> Path:
        return self.directory / "metadata.json"


class RoleManager:
    """Handles loading and saving roles on disk."""

    def __init__(
        self,
        root: Optional[Path] = None,
        *,
        defaults_root: Optional[Path] = None,
        locale_hint: Optional[str] = None,
    ) -> None:
        self._root = root or (Path.home() / ".consilium" / "roles")
        self._root.mkdir(parents=True, exist_ok=True)
        self._defaults_root = self._resolve_defaults_root(defaults_root)
        self._requested_locale = self._normalize_locale(locale_hint)
        self._roles: dict[str, Role] = {}
        self.reload()

    @property
    def root(self) -> Path:
        return self._root

    def reload(self) -> None:
        """Reload roles from disk."""
        self._roles.clear()
        for child in sorted(self._root.iterdir()):
            if not child.is_dir():
                continue
            role = self._load_role(child)
            if role:
                self._roles[role.role_id] = role

    def list_roles(self) -> List[Role]:
        """Return roles sorted by name."""
        return sorted(self._roles.values(), key=lambda role: role.name.lower())

    def get_role(self, role_id: str) -> Optional[Role]:
        return self._roles.get(role_id)

    def create_role(self, name: str) -> Role:
        """Create a new role with empty prompt."""
        role_id = uuid.uuid4().hex
        directory = self._root / role_id
        directory.mkdir(parents=True, exist_ok=True)

        role = Role(role_id=role_id, name=name, directory=directory, prompt="", locale=self._requested_locale)
        self._write_metadata(role, "")

        self._roles[role.role_id] = role
        return role

    def save_prompt(self, role_id: str, text: str) -> None:
        """Write prompt text to disk."""
        role = self._roles.get(role_id)
        if not role:
            raise KeyError(f"Unknown role '{role_id}'")
        updated = Role(
            role_id=role.role_id,
            name=role.name,
            directory=role.directory,
            prompt=text,
            locale=role.locale,
        )
        self._roles[role_id] = updated
        self._write_metadata(updated, text)
        prompt_file = updated.directory / "prompt.txt"
        if prompt_file.exists():
            try:
                prompt_file.unlink()
            except Exception:
                logger.warning("Failed to remove legacy prompt file %s", prompt_file, exc_info=True)

    def load_prompt(self, role_id: str) -> str:
        role = self._roles.get(role_id)
        if not role:
            raise KeyError(f"Unknown role '{role_id}'")
        if role.prompt:
            return role.prompt
        return ""

    def rename_role(self, role_id: str, new_name: str) -> None:
        """Update role display name."""
        role = self._roles.get(role_id)
        if not role:
            raise KeyError(f"Unknown role '{role_id}'")

        updated = Role(
            role_id=role.role_id,
            name=new_name,
            directory=role.directory,
            prompt=role.prompt,
            locale=role.locale,
        )
        self._roles[role_id] = updated
        self._write_metadata(updated)

    def delete_role(self, role_id: str) -> None:
        """Delete role from disk and cache."""
        role = self._roles.get(role_id)
        if not role:
            raise KeyError(f"Unknown role '{role_id}'")

        # Remove from disk
        if role.directory.exists():
            try:
                shutil.rmtree(role.directory)
                logger.info("Deleted role directory: %s", role.directory)
            except Exception as exc:
                logger.error("Failed to delete role directory %s: %s", role.directory, exc, exc_info=True)
                raise

        # Remove from cache
        self._roles.pop(role_id, None)
        logger.debug("Removed role %s from cache", role_id)

    def _load_role(self, directory: Path) -> Optional[Role]:
        metadata_path = directory / "metadata.json"
        role_id = directory.name
        name: Optional[str] = None
        prompt: Optional[str] = None

        locale_value: Optional[str] = None

        if metadata_path.exists():
            try:
                data = json.loads(metadata_path.read_text(encoding="utf-8"))
                role_id = str(data.get("id") or role_id)
                name = str(data.get("name") or role_id)
                prompt = data.get("prompt")
                if prompt is not None:
                    prompt = str(prompt)
                locale_raw = data.get("locale")
                if locale_raw:
                    locale_value = str(locale_raw).split(".")[0]
            except Exception:
                logger.warning("Failed to read metadata for role directory %s", directory, exc_info=True)

        if not name:
            name = f"Role {role_id[:8]}"
            try:
                self._write_metadata(
                    Role(
                        role_id=role_id,
                        name=name,
                        directory=directory,
                        prompt=prompt or "",
                        locale=locale_value,
                    )
                )
            except Exception:
                logger.debug("Unable to write default metadata for %s", directory, exc_info=True)

        if prompt is None:
            legacy_path = directory / "prompt.txt"
            if legacy_path.exists():
                try:
                    prompt = legacy_path.read_text(encoding="utf-8")
                    self._write_metadata(
                        Role(
                            role_id=role_id,
                            name=name,
                            directory=directory,
                            prompt=prompt,
                            locale=locale_value,
                        )
                    )
                    try:
                        legacy_path.unlink()
                    except Exception:
                        logger.warning("Failed to remove legacy prompt file %s", legacy_path, exc_info=True)
                except Exception:
                    prompt = ""

        return Role(role_id=role_id, name=name, directory=directory, prompt=prompt or "", locale=locale_value)

    def _write_metadata(self, role: Role, prompt: Optional[str] = None) -> None:
        payload = {
            "id": role.role_id,
            "name": role.name,
            "prompt": prompt if prompt is not None else role.prompt,
        }
        if role.locale:
            payload["locale"] = role.locale
        role.metadata_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    # ------------------------------------------------------------------
    # Default roles bootstrap
    # ------------------------------------------------------------------

    def bootstrap_defaults(self) -> None:
        """Install bundled default roles to user profile (called via --install flag)."""
        if not self._defaults_root or not self._defaults_root.exists():
            return

        preferred_locale = self._requested_locale or DEFAULT_ROLES_LOCALE
        locale_candidates: list[tuple[str, Path]] = []

        preferred_dir = self._defaults_root / preferred_locale
        if preferred_dir.exists():
            locale_candidates.append((preferred_locale, preferred_dir))
        elif preferred_locale != DEFAULT_ROLES_LOCALE:
            fallback_dir = self._defaults_root / DEFAULT_ROLES_LOCALE
            if fallback_dir.exists():
                locale_candidates.append((DEFAULT_ROLES_LOCALE, fallback_dir))
        else:
            locale_candidates.append((DEFAULT_ROLES_LOCALE, self._defaults_root / DEFAULT_ROLES_LOCALE))

        for locale_name, locale_dir in locale_candidates:
            if not locale_dir.exists():
                continue
            for role_dir in sorted(locale_dir.iterdir()):
                if not role_dir.is_dir():
                    continue
                target_dir = self._root / role_dir.name
                if target_dir.exists():
                    continue
                try:
                    shutil.copytree(role_dir, target_dir)
                    logger.debug("Bootstrapped default role %s from %s", role_dir.name, locale_name)
                except Exception:
                    logger.warning("Failed to copy default role from %s", role_dir, exc_info=True)

    def _normalize_locale(self, value: Optional[str]) -> Optional[str]:
        locale_value = value or os.environ.get("CONSILIUM_LOCALE")
        if locale_value:
            normalized = locale_value.split(".")[0].replace("-", "_")
            if "_" in normalized:
                normalized = normalized.split("_", 1)[0]
            return normalized.lower()
        system_locale, _ = locale.getdefaultlocale()
        if system_locale:
            normalized = system_locale.replace("-", "_")
            if "_" in normalized:
                normalized = normalized.split("_", 1)[0]
            return normalized.lower()
        return None

    def _resolve_defaults_root(self, provided: Optional[Path]) -> Optional[Path]:
        if provided:
            return provided if provided.exists() else None
        candidate = Path(__file__).resolve().parent.parent / "roles"
        if candidate.exists():
            return candidate
        return None


__all__ = ["Role", "RoleManager"]
