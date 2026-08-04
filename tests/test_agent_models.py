from __future__ import annotations

import unittest

from sparkos.agent.events import TaskCompleted, TextDelta, ToolCompleted
from sparkos.agent.planner import Plan, PlanStep, PlanStepStatus
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
    def test_plan_has_versioned_steps(self) -> None:
        step = PlanStep(description="read input")
        plan = Plan(task_id="task-1", steps=[step], version=2)

        self.assertEqual(plan.version, 2)
        self.assertEqual(plan.steps[0].status, PlanStepStatus.PENDING)


class EventModelTests(unittest.TestCase):
    def test_runtime_events_carry_domain_objects(self) -> None:
        task = AgentTask(goal="answer")
        call = ToolCall(call_id="call-1", name="read_file", arguments="{}")

        self.assertEqual(TextDelta("hello").text, "hello")
        self.assertIs(ToolCompleted(call).tool_call, call)
        self.assertIs(TaskCompleted(task).task, task)


if __name__ == "__main__":
    unittest.main()
