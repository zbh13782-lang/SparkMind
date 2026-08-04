"""Typed events emitted by AgentRuntime."""

from __future__ import annotations

from dataclasses import dataclass

from sparkos.agent.planner import Plan
from sparkos.agent.task import AgentTask
from sparkos.infrastructure.llm.models import ToolCall


@dataclass(frozen=True)
class TaskStarted:
    task: AgentTask


@dataclass(frozen=True)
class PlanCreated:
    plan: Plan


@dataclass(frozen=True)
class TextDelta:
    text: str


@dataclass(frozen=True)
class ToolCompleted:
    tool_call: ToolCall


@dataclass(frozen=True)
class TaskCompleted:
    task: AgentTask


@dataclass(frozen=True)
class TaskFailed:
    task: AgentTask


type AgentEvent = (
    TaskStarted | PlanCreated | TextDelta | ToolCompleted | TaskCompleted | TaskFailed
)


__all__ = [
    "AgentEvent",
    "PlanCreated",
    "TaskCompleted",
    "TaskFailed",
    "TaskStarted",
    "TextDelta",
    "ToolCompleted",
]
