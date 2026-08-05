"""Agent task domain model.

A task describes what one runtime invocation should accomplish. It is separate
from a conversation session and from the plan used to accomplish it.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any


class TaskStatus(StrEnum):
    PENDING = "pending"
    PLANNING = "planning"
    RUNNING = "running"
    WAITING_INPUT = "waiting_input"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"


@dataclass
class AgentTask:
    """A durable unit of intent passed into the agent runtime."""

    goal: str
    id: str = field(default_factory=lambda: uuid.uuid4().hex)
    input: dict[str, Any] = field(default_factory=dict)
    status: TaskStatus = TaskStatus.PENDING
    parent_task_id: str | None = None
    active_plan_id: str | None = None
    result: str | None = None
    error: str | None = None

    def start_planning(self) -> None:
        self.status = TaskStatus.PLANNING
        self.error = None

    def start(self) -> None:
        self.status = TaskStatus.RUNNING
        self.error = None

    def succeed(self, result: str) -> None:
        self.status = TaskStatus.SUCCEEDED
        self.result = result
        self.error = None

    def fail(self, error: str) -> None:
        self.status = TaskStatus.FAILED
        self.error = error

    def cancel(self) -> None:
        self.status = TaskStatus.CANCELLED


__all__ = ["AgentTask", "TaskStatus"]
