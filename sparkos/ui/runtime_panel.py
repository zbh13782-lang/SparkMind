"""Runtime execution state projected into a compact Textual dashboard."""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass

from rich.cells import cell_len
from rich.text import Text
from textual.widgets import Static

from sparkos.agent.events import (
    AgentEvent,
    ClarificationRequested,
    PlanCreated,
    PlanReplanned,
    StepCompleted,
    StepFailed,
    StepStarted,
    StepToolCompleted,
    TaskCompleted,
    TaskFailed,
    TaskStarted,
    TextDelta,
)
from sparkos.agent.planner import Plan, PlanStep
from sparkos.agent.task import AgentTask
from sparkos.infrastructure.llm.models import ChatMessage
from utils._tiktoken import count_messages

_PHASES = (
    ("planning", "规划"),
    ("executing", "执行"),
    ("tooling", "工具"),
    ("responding", "回答"),
)

_TASK_LABELS = {
    "idle": "空闲",
    "pending": "待开始",
    "planning": "规划中",
    "running": "运行中",
    "waiting_input": "等待补充",
    "succeeded": "已完成",
    "failed": "失败",
    "cancelled": "已停止",
}

_TASK_COLORS = {
    "idle": "dim",
    "pending": "yellow",
    "planning": "cyan",
    "running": "cyan",
    "waiting_input": "yellow",
    "succeeded": "green",
    "failed": "red",
    "cancelled": "yellow",
}

_STEP_MARKS = {
    "pending": ("○", "dim"),
    "running": ("▶", "cyan"),
    "tooling": ("◆", "magenta"),
    "succeeded": ("✓", "green"),
    "failed": ("×", "red"),
    "cancelled": ("■", "yellow"),
}


def _shorten(text: str, limit: int) -> str:
    normalized = " ".join(text.split())
    if cell_len(normalized) <= limit:
        return normalized
    shortened: list[str] = []
    used = cell_len("…")
    for character in normalized:
        width = cell_len(character)
        if used + width > limit:
            break
        shortened.append(character)
        used += width
    return f"{''.join(shortened)}…"


@dataclass
class RuntimeStepView:
    id: str
    description: str
    status: str = "pending"
    attempt: int = 0
    tool_count: int = 0


class RuntimeTrace:
    """Presentation state derived exclusively from typed runtime events."""

    def __init__(self) -> None:
        self.reset()

    def reset(self) -> None:
        self.task_id = ""
        self.goal = ""
        self.task_status = "idle"
        self.phase = "idle"
        self.plan_version: int | None = None
        self.plan_source = ""
        self.steps: dict[str, RuntimeStepView] = {}
        self.step_order: list[str] = []
        self.visited_phases: set[str] = set()
        self.activity: deque[str] = deque(maxlen=7)
        self.total_tokens = 0

    def begin_task(self, task: AgentTask) -> None:
        self.reset()
        self.task_id = task.id
        self.goal = task.goal
        self.task_status = "pending"
        self._record("任务已创建")

    def apply(self, event: AgentEvent) -> None:
        if isinstance(event, TaskStarted):
            self.task_status = "planning"
            self._set_phase("planning")
            self._record("开始规划任务")
        elif isinstance(event, PlanCreated):
            self._load_plan(event.plan)
            self.task_status = "running"
            self._set_phase("executing")
            self._record(f"计划已生成 · {len(event.plan.steps)} 步")
        elif isinstance(event, ClarificationRequested):
            self.task_status = "waiting_input"
            self.phase = "waiting_input"
            self._record(f"等待补充 · {event.question}")
        elif isinstance(event, PlanReplanned):
            previous_steps = {step.id: step for step in event.previous_plan.steps}
            unchanged_steps = {
                step.id
                for step in event.plan.steps
                if previous_steps.get(step.id) == step
            }
            self._load_plan(
                event.plan,
                preserve_completed_ids=unchanged_steps,
            )
            self.task_status = "running"
            self._record(f"重新规划 · v{event.plan.version}")
            if self.step_order and all(
                self.steps[step_id].status == "succeeded" for step_id in self.step_order
            ):
                self._set_phase("responding")
                self._record("准备最终回答")
            else:
                self._set_phase("executing")
        elif isinstance(event, StepStarted):
            step = self._ensure_step(event.step)
            step.status = "running"
            if step.attempt == 0:
                step.attempt = 1
            self._set_phase("executing")
            self._record(f"{step.id} · 开始执行")
        elif isinstance(event, StepToolCompleted):
            step = self._ensure_step(event.step)
            step.status = "tooling"
            step.tool_count += 1
            self._set_phase("tooling")
            self._record(f"{step.id} · 工具 {event.tool_call.name}")
            self.total_tokens += count_messages(
                [ChatMessage(role="tool", content=event.tool_call.result)]
            )
        elif isinstance(event, StepCompleted):
            step = self._ensure_step(event.step)
            step.status = "succeeded"
            self._record(f"{step.id} · 步骤完成")
            if self.step_order and all(
                self.steps[step_id].status == "succeeded" for step_id in self.step_order
            ):
                self._set_phase("responding")
                self._record("准备最终回答")
            else:
                self._set_phase("executing")
        elif isinstance(event, StepFailed):
            step = self._ensure_step(event.step)
            step.status = "failed"
            self._record(f"{step.id} · 步骤失败")
        elif isinstance(event, TextDelta):
            if self.phase != "responding":
                self._set_phase("responding")
                self._record("生成最终回答")
            self.total_tokens += count_messages(
                [ChatMessage(role="assistant", content=event.text)]
            )
        elif isinstance(event, TaskCompleted):
            self.task_status = "succeeded"
            self.phase = "completed"
            self.visited_phases.add("responding")
            self._record("任务完成")
        elif isinstance(event, TaskFailed):
            self.task_status = "failed"
            self.phase = "failed"
            self._record("任务失败")

    def cancel(self) -> None:
        if self.task_status in {"idle", "succeeded", "failed", "cancelled"}:
            return
        transient_statuses = {"running", "tooling"}
        for step in self.steps.values():
            if step.status in transient_statuses:
                step.status = "cancelled"
        self.task_status = "cancelled"
        self.phase = "cancelled"
        self._record("任务已停止")

    def render_text(self) -> Text:
        if self.task_status == "idle":
            idle = Text("RUNTIME", style="bold cyan")
            idle.append("\n\n")
            idle.append("Hot Things", style="dim")
            return idle

        task_color = _TASK_COLORS[self.task_status]
        task_label = _TASK_LABELS[self.task_status]
        output = Text("RUNTIME", style="bold cyan")
        output.append("\n")
        output.append(f"● {task_label}", style=task_color)
        output.append("  ")
        output.append(self.task_id[:8], style="dim")
        output.append("\n")
        output.append(_shorten(self.goal, 34))
        output.append("\n\n")
        output.append("流程", style="bold")
        output.append("\n")
        output.append(self._render_phases())

        if self.step_order:
            completed = sum(
                self.steps[step_id].status == "succeeded" for step_id in self.step_order
            )
            plan_meta = f"PLAN v{self.plan_version}  {completed}/{len(self.step_order)}"
            output.append("\n\n")
            output.append(plan_meta, style="bold")
            output.append("\n")
            output.append(self._render_steps())

        if self.activity:
            output.append("\n\n")
            output.append("最近事件", style="bold")
            output.append("\n")
            output.append(self._render_activity())

        if self.total_tokens > 0:
            output.append("\n\n")
            output.append(f"累计 tokens: {self.total_tokens}", style="dim")

        return output

    def _load_plan(
        self,
        plan: Plan,
        preserve_completed_ids: set[str] | None = None,
    ) -> None:
        prior = self.steps
        preserve_completed_ids = preserve_completed_ids or set()
        steps: dict[str, RuntimeStepView] = {}
        for plan_step in plan.steps:
            previous = prior.get(plan_step.id)
            if (
                plan_step.id in preserve_completed_ids
                and previous is not None
                and previous.status == "succeeded"
            ):
                steps[plan_step.id] = previous
            else:
                steps[plan_step.id] = RuntimeStepView(
                    id=plan_step.id,
                    description=plan_step.description,
                )
        self.steps = steps
        self.step_order = [step.id for step in plan.steps]
        self.plan_version = plan.version
        self.plan_source = plan.source

    def _ensure_step(self, step: PlanStep) -> RuntimeStepView:
        view = self.steps.get(step.id)
        if view is None:
            view = RuntimeStepView(id=step.id, description=step.description)
            self.steps[step.id] = view
            self.step_order.append(step.id)
        return view

    def _set_phase(self, phase: str) -> None:
        if self.phase in {name for name, _ in _PHASES}:
            self.visited_phases.add(self.phase)
        self.phase = phase

    def _record(self, message: str) -> None:
        self.activity.append(message)

    def _render_phases(self) -> Text:
        terminal = self.phase in {
            "completed",
            "failed",
            "cancelled",
            "waiting_input",
        }
        output = Text()
        for index, (phase, label) in enumerate(_PHASES):
            if self.phase == phase:
                output.append(f"▶ {label}", style="bold cyan")
            elif phase in self.visited_phases:
                output.append(f"✓ {label}", style="green")
            elif terminal:
                output.append(f"– {label}", style="dim")
            else:
                output.append(f"· {label}", style="dim")
            if index == 2:
                output.append("\n")
            elif index < len(_PHASES) - 1:
                output.append("  ")
        return output

    def _render_steps(self) -> Text:
        output = Text()
        for index, step_id in enumerate(self.step_order, start=1):
            step = self.steps[step_id]
            mark, color = _STEP_MARKS.get(step.status, ("○", "dim"))
            description = _shorten(step.description, 26)
            metadata: list[str] = []
            if step.tool_count:
                metadata.append(f"tool×{step.tool_count}")
            if step.attempt > 1:
                metadata.append(f"try {step.attempt}")
            output.append(mark, style=color)
            output.append(f" {index}. {description}")
            if metadata:
                output.append("\n")
                output.append(f"   · {' · '.join(metadata)}", style="dim")
            if index < len(self.step_order):
                output.append("\n")
        return output

    def _render_activity(self) -> Text:
        output = Text()
        for index, message in enumerate(self.activity, start=1):
            output.append(f"{index:02d}", style="dim")
            output.append(f" {_shorten(message, 29)}")
            if index < len(self.activity):
                output.append("\n")
        return output


class RuntimePanel(Static):
    """Textual widget that renders a :class:`RuntimeTrace`."""

    def __init__(
        self,
        *,
        name: str | None = None,
        id: str | None = None,
        classes: str | None = None,
        disabled: bool = False,
    ) -> None:
        super().__init__(
            "",
            markup=False,
            name=name,
            id=id,
            classes=classes,
            disabled=disabled,
        )
        self.trace = RuntimeTrace()

    def on_mount(self) -> None:
        self._refresh_content()

    def begin_task(self, task: AgentTask) -> None:
        self.trace.begin_task(task)
        self._refresh_content()

    def handle_event(self, event: AgentEvent) -> None:
        self.trace.apply(event)
        self._refresh_content()

    def cancel(self) -> None:
        self.trace.cancel()
        self._refresh_content()

    def reset(self) -> None:
        self.trace.reset()
        self._refresh_content()

    def _refresh_content(self) -> None:
        self.update(self.trace.render_text())


__all__ = ["RuntimePanel", "RuntimeStepView", "RuntimeTrace"]
