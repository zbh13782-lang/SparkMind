"""Planning contracts and models.

Planner implementations decide how to perform a task. They never execute tools
or mutate task state directly; AgentRuntime owns those responsibilities.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Protocol

from sparkos.agent.task import AgentTask
from sparkos.infrastructure.llm.models import ChatMessage


class PlanStepStatus(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    SKIPPED = "skipped"


@dataclass
class PlanStep:
    description: str
    id: str = field(default_factory=lambda: uuid.uuid4().hex)
    depends_on: list[str] = field(default_factory=list)
    status: PlanStepStatus = PlanStepStatus.PENDING


@dataclass
class Plan:
    task_id: str
    steps: list[PlanStep]
    id: str = field(default_factory=lambda: uuid.uuid4().hex)
    version: int = 1


@dataclass(frozen=True)
class PlanningContext:
    """Immutable snapshot supplied to a Planner."""

    session_id: str | None
    summary: str
    recent_messages: tuple[ChatMessage, ...]
    skill_names: tuple[str, ...]
    tool_names: tuple[str, ...]


class Planner(Protocol):
    async def create_plan(
        self,
        task: AgentTask,
        context: PlanningContext,
    ) -> Plan | None:
        """Return a plan or None when direct execution is appropriate."""
        ...


__all__ = [
    "Plan",
    "PlanStep",
    "PlanStepStatus",
    "Planner",
    "PlanningContext",
]
