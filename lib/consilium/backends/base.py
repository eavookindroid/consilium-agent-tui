"""
Consilium Agent - Backend base classes.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from ..app import ConsiliumAgentTUI


class AgentBackend:
    """Abstract base class for CLI backends."""

    class_id: str = ""
    display_name: str = ""
    description: str = ""

    def __init__(self) -> None:
        identifier = self.class_id or self.__class__.__name__
        self.logger = logging.getLogger(f"ConsiliumBackend.{identifier}")

    async def run(
        self,
        app: ConsiliumAgentTUI,
        agent_name: str,
        message: str,
        *,
        is_init: bool = False,
        skip_log: bool = False,
    ) -> Any:
        raise NotImplementedError

