from __future__ import annotations

from abc import ABC, abstractmethod
from typing import ClassVar

import structlog

from core.config import Tier, settings
from core.logging import get_logger
from core.models import AgentResult, AgentTask, HealthStatus


class BaseAgent(ABC):
    """Abstract base for all Arda agents.

    Subclasses must declare `tier` and `name` as class attributes and
    implement `run`. `health` has a default impl that reports the
    configured model/provider for the agent's tier.
    """

    tier: ClassVar[Tier]
    name: ClassVar[str]

    @abstractmethod
    async def run(self, task: AgentTask) -> AgentResult: ...

    async def health(self) -> HealthStatus:
        return HealthStatus(
            agent=self.name,
            status="healthy",
            model=settings.model_for_tier(self.tier),
            provider=settings.provider_for_tier(self.tier),
        )

    def logger(self) -> structlog.stdlib.BoundLogger:
        return get_logger(f"agent.{self.name}").bind(agent=self.name, tier=self.tier)
