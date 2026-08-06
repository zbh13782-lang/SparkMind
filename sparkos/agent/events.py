"""Typed events emitted by AgentRuntime."""

from __future__ import annotations

from dataclasses import dataclass

from sparkos.agent.planner import Plan, PlanStep
from sparkos.agent.step import StepResult
from sparkos.agent.task import AgentTask
from sparkos.infrastructure.llm.models import ToolCall


@dataclass(frozen=True)
class TaskStarted:
    task: AgentTask


@dataclass(frozen=True)
class PlanCreated:
    plan: Plan


@dataclass(frozen=True)
class PlanReplanned:
    previous_plan: Plan
    plan: Plan
    reason: str


@dataclass(frozen=True)
class ClarificationRequested:
    task: AgentTask
    question: str


@dataclass(frozen=True)
class TextDelta:
    text: str


@dataclass(frozen=True)
class ToolCompleted:
    tool_call: ToolCall


@dataclass(frozen=True)
class StepStarted:
    step: PlanStep


@dataclass(frozen=True)
class StepToolCompleted:
    step: PlanStep
    tool_call: ToolCall


@dataclass(frozen=True)
class StepCompleted:
    step: PlanStep
    result: StepResult


@dataclass(frozen=True)
class StepFailed:
    step: PlanStep
    error: str


@dataclass(frozen=True)
class TaskCompleted:
    task: AgentTask


@dataclass(frozen=True)
class TaskFailed:
    task: AgentTask


type AgentEvent = (
    TaskStarted
    | ClarificationRequested
    | PlanCreated
    | PlanReplanned
    | StepStarted
    | StepToolCompleted
    | StepCompleted
    | StepFailed
    | TextDelta
    | ToolCompleted
    | TaskCompleted
    | TaskFailed
)


__all__ = [
    "AgentEvent",
    "ClarificationRequested",
    "PlanCreated",
    "PlanReplanned",
    "StepCompleted",
    "StepFailed",
    "StepStarted",
    "StepToolCompleted",
    "TaskCompleted",
    "TaskFailed",
    "TaskStarted",
    "TextDelta",
    "ToolCompleted",
]
