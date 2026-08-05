from __future__ import annotations

import unittest

from rich.cells import cell_len

from sparkos.agent.events import (
    ClarificationRequested,
    PlanCreated,
    PlanReplanned,
    StepCompleted,
    StepFailed,
    StepRetrying,
    StepStarted,
    StepToolCompleted,
    StepVerificationCompleted,
    TaskCompleted,
    TaskFailed,
    TaskStarted,
    TextDelta,
)
from sparkos.agent.planner import Plan, PlanStep
from sparkos.agent.step import StepResult, StepVerification
from sparkos.agent.task import AgentTask
from sparkos.infrastructure.llm.models import ToolCall
from sparkos.ui.runtime_panel import RuntimeTrace


def sample_task() -> AgentTask:
    return AgentTask(id="task-12345678", goal="Analyze [bold]sales[/bold]")


def sample_plan(task_id: str = "task-12345678") -> Plan:
    return Plan(
        id="plan-1",
        task_id=task_id,
        version=1,
        source="planner",
        steps=(
            PlanStep(
                id="load",
                description="Load [cyan]data[/cyan]",
                success_criteria="data loaded",
            ),
            PlanStep(
                id="report",
                description="Write report",
                depends_on=("load",),
                success_criteria="report written",
            ),
        ),
    )


class RuntimeTraceTests(unittest.TestCase):
    def test_clarification_request_enters_waiting_input_state(self) -> None:
        task = sample_task()
        task.wait_for_input("Which file?")
        trace = RuntimeTrace()
        trace.begin_task(task)
        trace.apply(TaskStarted(task))

        trace.apply(ClarificationRequested(task, "Which file?"))

        self.assertEqual(trace.task_status, "waiting_input")
        self.assertEqual(trace.phase, "waiting_input")
        self.assertIn("等待补充", trace.render_text().plain)
        self.assertIn("Which file?", trace.render_text().plain)

    def test_tracks_task_plan_tool_verification_and_response(self) -> None:
        task = sample_task()
        plan = sample_plan()
        load = plan.steps[0]
        tool_call = ToolCall(
            call_id="call-1",
            name="read_file",
            arguments='{"path":"sales.csv"}',
            result="loaded",
        )
        trace = RuntimeTrace()

        trace.begin_task(task)
        trace.apply(TaskStarted(task))
        self.assertEqual(trace.phase, "planning")

        trace.apply(PlanCreated(plan))
        self.assertEqual(trace.phase, "executing")
        self.assertEqual(trace.step_order, ["load", "report"])
        self.assertEqual(trace.steps["load"].status, "pending")

        trace.apply(StepStarted(load))
        self.assertEqual(trace.steps["load"].status, "running")
        self.assertEqual(trace.steps["load"].attempt, 1)

        trace.apply(StepToolCompleted(load, tool_call))
        self.assertEqual(trace.phase, "tooling")
        self.assertEqual(trace.steps["load"].tool_count, 1)

        trace.apply(
            StepVerificationCompleted(
                load,
                StepVerification(True, "source loaded", False),
            )
        )
        self.assertEqual(trace.phase, "verifying")

        trace.apply(StepCompleted(load, StepResult(True, "loaded")))
        self.assertEqual(trace.steps["load"].status, "succeeded")

        trace.apply(TextDelta("final"))
        self.assertEqual(trace.phase, "responding")
        trace.apply(TaskCompleted(task))
        self.assertEqual(trace.phase, "completed")
        self.assertEqual(trace.task_status, "succeeded")

        rendered = trace.render_text()
        self.assertIn("Analyze [bold]sales[/bold]", rendered.plain)
        self.assertIn("Load [cyan]data[/cyan]", rendered.plain)
        self.assertIn("read_file", rendered.plain)
        self.assertIn("1/2", rendered.plain)
        plain_lines = rendered.plain.splitlines()
        self.assertLessEqual(max(cell_len(line) for line in plain_lines), 34)

    def test_tracks_retry_replan_and_failure(self) -> None:
        task = sample_task()
        original = sample_plan()
        load = original.steps[0]
        fallback = PlanStep(
            id="fallback",
            description="Load fallback",
            success_criteria="fallback loaded",
        )
        revised = Plan(
            id="plan-2",
            task_id=task.id,
            version=2,
            source="replan",
            steps=(load, fallback),
        )
        trace = RuntimeTrace()

        trace.begin_task(task)
        trace.apply(TaskStarted(task))
        trace.apply(PlanCreated(original))
        trace.apply(StepStarted(load))
        trace.apply(
            StepVerificationCompleted(
                load,
                StepVerification(False, "incomplete", True),
            )
        )
        trace.apply(StepRetrying(load, attempt=2, reason="incomplete"))

        self.assertEqual(trace.steps["load"].status, "retrying")
        self.assertEqual(trace.steps["load"].attempt, 2)

        trace.apply(StepStarted(load))
        trace.apply(StepCompleted(load, StepResult(True, "loaded")))
        trace.apply(PlanReplanned(original, revised, "source changed"))

        self.assertEqual(trace.plan_version, 2)
        self.assertEqual(trace.steps["load"].status, "succeeded")
        self.assertEqual(trace.steps["fallback"].status, "pending")

        trace.apply(StepStarted(fallback))
        trace.apply(StepFailed(fallback, "not found"))
        task.fail("not found")
        trace.apply(TaskFailed(task))

        self.assertEqual(trace.phase, "failed")
        self.assertEqual(trace.task_status, "failed")
        self.assertEqual(trace.steps["fallback"].status, "failed")
        self.assertIn("重新规划", trace.render_text().plain)

    def test_cancelled_task_marks_active_step_cancelled(self) -> None:
        task = sample_task()
        plan = sample_plan()
        trace = RuntimeTrace()
        trace.begin_task(task)
        trace.apply(TaskStarted(task))
        trace.apply(PlanCreated(plan))
        trace.apply(StepStarted(plan.steps[0]))

        trace.cancel()

        self.assertEqual(trace.phase, "cancelled")
        self.assertEqual(trace.task_status, "cancelled")
        self.assertEqual(trace.steps["load"].status, "cancelled")
        self.assertEqual(trace.steps["load"].attempt, 1)
        self.assertIn("已停止", trace.render_text().plain)

    def test_replan_resets_completed_step_when_definition_changes(self) -> None:
        task = sample_task()
        original = sample_plan()
        load = original.steps[0]
        changed_load = PlanStep(
            id=load.id,
            description=load.description,
            success_criteria="new acceptance criteria",
        )
        revised = Plan(
            id="plan-2",
            task_id=task.id,
            version=2,
            source="replan",
            steps=(changed_load,),
        )
        trace = RuntimeTrace()
        trace.begin_task(task)
        trace.apply(PlanCreated(original))
        trace.apply(StepStarted(load))
        trace.apply(StepCompleted(load, StepResult(True, "loaded")))

        trace.apply(PlanReplanned(original, revised, "criteria changed"))

        self.assertEqual(trace.steps["load"].status, "pending")

    def test_last_completed_step_enters_responding_before_text_arrives(self) -> None:
        task = sample_task()
        step = PlanStep(id="only", description="Do work")
        plan = Plan(task_id=task.id, steps=(step,))
        trace = RuntimeTrace()
        trace.begin_task(task)
        trace.apply(PlanCreated(plan))
        trace.apply(StepStarted(step))

        trace.apply(StepCompleted(step, StepResult(True, "done")))

        self.assertEqual(trace.phase, "responding")
        self.assertIn("准备最终回答", trace.render_text().plain)

    def test_replan_with_only_preserved_completed_steps_enters_responding(
        self,
    ) -> None:
        task = sample_task()
        step = PlanStep(id="done", description="Already done")
        original = Plan(task_id=task.id, steps=(step,))
        revised = Plan(
            task_id=task.id,
            steps=(step,),
            version=2,
            source="replan",
        )
        trace = RuntimeTrace()
        trace.begin_task(task)
        trace.apply(PlanCreated(original))
        trace.apply(StepStarted(step))
        trace.apply(StepCompleted(step, StepResult(True, "done")))

        trace.apply(PlanReplanned(original, revised, "keep completed"))

        self.assertEqual(trace.steps["done"].status, "succeeded")
        self.assertEqual(trace.phase, "responding")


if __name__ == "__main__":
    unittest.main()
