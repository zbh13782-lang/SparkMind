"""Planning contracts and models.

Planner implementations decide how to perform a task. They never execute tools
or mutate task state directly; AgentRuntime owns those responsibilities.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Protocol

from sparkos.agent.task import AgentTask
from sparkos.infrastructure.llm.models import ChatMessage

if TYPE_CHECKING:
    from sparkos.agent.step import StepRun


@dataclass(frozen=True)
class ClarificationRequest:
    """A planner decision that execution must wait for missing user input."""

    question: str


@dataclass(frozen=True)
class PlanStep:
    description: str
    id: str = field(default_factory=lambda: uuid.uuid4().hex)
    depends_on: tuple[str, ...] = ()
    success_criteria: str = ""


@dataclass(frozen=True)
class Plan:
    task_id: str
    steps: tuple[PlanStep, ...]
    id: str = field(default_factory=lambda: uuid.uuid4().hex)
    version: int = 1
    source: str = "planner"

    def __post_init__(self) -> None:
        if not self.steps:
            raise ValueError("Plan 至少需要一个步骤")

        step_ids = [step.id for step in self.steps]
        if len(step_ids) != len(set(step_ids)):
            raise ValueError("Plan 包含重复的步骤 id")

        known_ids = set(step_ids)
        graph = {step.id: step.depends_on for step in self.steps}
        for step in self.steps:
            for dependency in step.depends_on:
                if dependency not in known_ids:
                    raise ValueError(f"步骤 {step.id} 引用了未知依赖：{dependency}")
                if dependency == step.id:
                    raise ValueError(f"步骤 {step.id} 不能依赖自身")

        visiting: set[str] = set()
        visited: set[str] = set()

        def visit(step_id: str) -> None:
            if step_id in visiting:
                raise ValueError("Plan 依赖图存在环")
            if step_id in visited:
                return
            visiting.add(step_id)
            for dependency in graph[step_id]:
                visit(dependency)
            visiting.remove(step_id)
            visited.add(step_id)

        for step_id in graph:
            visit(step_id)


@dataclass(frozen=True)
class SkillCapability:
    """Skill metadata available to a Planner without loading its full body."""

    name: str
    description: str


@dataclass(frozen=True)
class PlanningContext:
    """Immutable snapshot supplied to a Planner."""

    session_id: str | None
    summary: str
    recent_messages: tuple[ChatMessage, ...]
    skills: tuple[SkillCapability, ...]
    tool_names: tuple[str, ...]

    @property
    def skill_names(self) -> tuple[str, ...]:
        """Compatibility view for Planner implementations that only need names."""
        return tuple(skill.name for skill in self.skills)


class Planner(Protocol):
    async def create_plan(
        self,
        task: AgentTask,
        context: PlanningContext,
    ) -> Plan | ClarificationRequest | None:
        """Return a plan or None when direct execution is appropriate."""
        ...


class Replanner(Protocol):
    async def revise_plan(
        self,
        task: AgentTask,
        context: PlanningContext,
        current_plan: Plan,
        step_runs: dict[str, StepRun],
        failed_step: PlanStep,
        reason: str,
    ) -> Plan | None:
        """Return a complete replacement Plan or None when revision fails."""
        ...


__all__ = [
    "ClarificationRequest",
    "Plan",
    "PlanStep",
    "Planner",
    "PlanningContext",
    "Replanner",
    "SkillCapability",
]
