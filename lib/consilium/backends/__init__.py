"""
Consilium Agent - Backend Registry

Provides backend registry and default CLI backend implementations.
"""

from __future__ import annotations

from typing import Iterable, List, Optional, Sequence, Type

from .base import AgentBackend
from .codex import CodexBackend
from .claude import ClaudeBackend
from .gemini import GeminiBackend


DEFAULT_BACKEND_CLASSES: tuple[Type[AgentBackend], ...] = (
    CodexBackend,
    ClaudeBackend,
    GeminiBackend,
)


def _instantiate_backends(classes: Iterable[Type[AgentBackend]]) -> List[AgentBackend]:
    return [backend_cls() for backend_cls in classes]


class AgentBackendRegistry:
    """Registry that maps class identifiers to backend instances."""

    def __init__(self, backend_classes: Sequence[Type[AgentBackend]] | None = None) -> None:
        self._backends: dict[str, AgentBackend] = {}
        self._default_backend_id: str | None = None
        self._initialize_defaults(backend_classes)

    def _initialize_defaults(self, backend_classes: Sequence[Type[AgentBackend]] | None) -> None:
        classes = backend_classes or DEFAULT_BACKEND_CLASSES
        instantiated = _instantiate_backends(classes)
        for backend in instantiated:
            self.register(backend)
        if instantiated:
            self._default_backend_id = instantiated[0].class_id.lower()

    def register(self, backend: AgentBackend) -> None:
        class_id = backend.class_id.lower()
        self._backends[class_id] = backend

    def get_backend(self, class_id: str | None) -> Optional[AgentBackend]:
        if not class_id:
            return self.get_default_backend()
        return self._backends.get(class_id.lower()) or self.get_default_backend()

    def get_default_backend(self) -> Optional[AgentBackend]:
        if self._default_backend_id is None:
            return None
        return self._backends.get(self._default_backend_id)

    def list_backends(self) -> List[AgentBackend]:
        return list(self._backends.values())

    def list_backend_ids(self) -> List[str]:
        return sorted(self._backends.keys())


__all__ = [
    "AgentBackend",
    "AgentBackendRegistry",
    "DEFAULT_BACKEND_CLASSES",
]
