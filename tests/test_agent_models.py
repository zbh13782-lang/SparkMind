from __future__ import annotations

import unittest
from dataclasses import FrozenInstanceError

from sparkos.agent.events import TaskCompleted, TextDelta, ToolCompleted
from sparkos.agent.planner import Plan, PlanStep
from sparkos.agent.retry import RetryPolicy
from sparkos.agent.step import (
    ArtifactRef,
    StepResult,
    StepRun,
    StepStatus,
    StepVerification,
)
from sparkos.agent.task import AgentTask, TaskStatus
from sparkos.infrastructure.llm.models import ChatMessage, ToolCall


class ChatModelTests(unittest.TestCase):
    def test_tool_message_serializes_tool_call_id(self) -> None:
        message = ChatMessage(role="tool", content="ok", tool_call_id="call-1")

        self.assertEqual(
            message.to_api_dict(),
            {"role": "tool", "content": "ok", "tool_call_id": "call-1"},
        )

    def test_assistant_message_serializes_tool_calls(self) -> None:
        call = ToolCall(call_id="call-1", name="read_file", arguments='{"path":"x"}')
        message = ChatMessage(
            role="assistant",
            content="",
            tool_calls=[call.to_api_dict()],
        )

        self.assertEqual(
            message.to_api_dict(),
            {
                "role": "assistant",
                "content": "",
                "tool_calls": [
                    {
                        "id": "call-1",
                        "type": "function",
                        "function": {
                            "name": "read_file",
                            "arguments": '{"path":"x"}',
                        },
                    }
                ],
            },
        )


class AgentTaskTests(unittest.TestCase):
    def test_task_can_enter_planning_state(self) -> None:
        task = AgentTask(goal="analyze")

        task.start_planning()

        self.assertEqual(task.status, TaskStatus.PLANNING)

    def test_task_can_wait_for_clarification(self) -> None:
        task = AgentTask(goal="analyze")

        task.wait_for_input("  Which file should I analyze?  ")

        self.assertEqual(task.status, TaskStatus.WAITING_INPUT)
        self.assertEqual(task.clarification_question, "Which file should I analyze?")

    def test_task_lifecycle_records_result(self) -> None:
        task = AgentTask(goal="analyze")

        task.start()
        task.succeed("done")

        self.assertEqual(task.status, TaskStatus.SUCCEEDED)
        self.assertEqual(task.result, "done")
        self.assertIsNone(task.error)

    def test_task_failure_records_error(self) -> None:
        task = AgentTask(goal="analyze")

        task.start()
        task.fail("boom")

        self.assertEqual(task.status, TaskStatus.FAILED)
        self.assertEqual(task.error, "boom")


class PlanningModelTests(unittest.TestCase):
    def test_plan_definition_is_immutable_and_has_success_criteria(self) -> None:
        step = PlanStep(
            id="s1",
            description="read input",
            depends_on=(),
            success_criteria="input is available",
        )
        plan = Plan(task_id="task-1", steps=(step,), version=2)

        self.assertEqual(plan.version, 2)
        self.assertEqual(plan.steps[0].success_criteria, "input is available")
        self.assertFalse(hasattr(step, "status"))
        with self.assertRaises(FrozenInstanceError):
            step.description = "changed"  # type: ignore[misc]

    def test_plan_rejects_duplicate_ids_and_dependency_cycles(self) -> None:
        duplicate = PlanStep(id="same", description="one")
        with self.assertRaisesRegex(ValueError, "重复"):
            Plan(task_id="task-1", steps=(duplicate, duplicate))

        with self.assertRaisesRegex(ValueError, "环"):
            Plan(
                task_id="task-1",
                steps=(
                    PlanStep(id="s1", description="one", depends_on=("s2",)),
                    PlanStep(id="s2", description="two", depends_on=("s1",)),
                ),
            )


class StepRunTests(unittest.TestCase):
    def test_step_run_owns_execution_state_and_result(self) -> None:
        result = StepResult(
            success=True,
            output="loaded",
            evidence=("120 rows",),
            artifacts=(ArtifactRef(uri="file:///tmp/data.csv", kind="file"),),
        )
        run = StepRun(step_id="s1")

        run.start()
        run.succeed(result)

        self.assertEqual(run.status, StepStatus.SUCCEEDED)
        self.assertEqual(run.attempt_count, 1)
        self.assertIs(run.result, result)
        self.assertIsNone(run.error)

    def test_step_run_records_failure_and_blocking(self) -> None:
        failed = StepRun(step_id="s1")
        blocked = StepRun(step_id="s2")

        failed.start()
        failed.fail("boom")
        blocked.block("dependency failed")

        self.assertEqual(failed.status, StepStatus.FAILED)
        self.assertEqual(failed.error, "boom")
        self.assertEqual(blocked.status, StepStatus.BLOCKED)

    def test_step_run_records_transcript_and_cancellation(self) -> None:
        run = StepRun(step_id="s1")
        run.start()

        run.record_transcript(
            (
                {"role": "assistant", "content": "reading"},
                {
                    "role": "tool",
                    "content": "loaded",
                    "tool_call_id": "c1",
                },
            )
        )
        run.cancel()

        self.assertEqual(run.status, StepStatus.CANCELLED)
        self.assertEqual(run.transcript[-1]["tool_call_id"], "c1")

    def test_step_verification_rejection_can_prepare_one_retry(self) -> None:
        run = StepRun(step_id="s1")
        candidate = StepResult(success=True, output="weather unavailable")
        verification = StepVerification(
            passed=False,
            reason="missing temperature",
            retryable=True,
        )
        run.start()
        run.record_transcript([{"role": "assistant", "content": candidate.output}])

        run.begin_verification(candidate)
        run.record_verification(verification)

        self.assertEqual(run.status, StepStatus.VERIFYING)
        self.assertTrue(RetryPolicy(max_attempts=2).should_retry(run, verification))

        run.prepare_retry()

        self.assertEqual(run.status, StepStatus.PENDING)
        self.assertIsNone(run.result)
        self.assertEqual(run.result_history, [candidate])
        self.assertEqual(len(run.transcript_history), 1)
        self.assertEqual(run.verification_history, [verification])

    def test_retry_policy_stops_at_attempt_limit(self) -> None:
        run = StepRun(step_id="s1", attempt_count=2)
        verification = StepVerification(False, "still incomplete", True)

        self.assertFalse(RetryPolicy(max_attempts=2).should_retry(run, verification))

    def test_retry_policy_rejects_more_than_two_attempts(self) -> None:
        with self.assertRaisesRegex(ValueError, "最多允许 2 次"):
            RetryPolicy(max_attempts=3)


class EventModelTests(unittest.TestCase):
    def test_runtime_events_carry_domain_objects(self) -> None:
        task = AgentTask(goal="answer")
        call = ToolCall(call_id="call-1", name="read_file", arguments="{}")

        self.assertEqual(TextDelta("hello").text, "hello")
        self.assertIs(ToolCompleted(call).tool_call, call)
        self.assertIs(TaskCompleted(task).task, task)


if __name__ == "__main__":
    unittest.main()
