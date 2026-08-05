"""Persistence port for task execution snapshots."""

from __future__ import annotations

from typing import Protocol

from sparkos.agent.planner import Plan
from sparkos.agent.step import StepRun
from sparkos.agent.task import AgentTask


class TaskStore(Protocol):
    def save(
        self,
        task: AgentTask,
        plan: Plan,
        step_runs: dict[str, StepRun],
    ) -> None: ...


__all__ = ["TaskStore"]
