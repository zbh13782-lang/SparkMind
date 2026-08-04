from __future__ import annotations

import unittest
from collections.abc import AsyncIterator
from unittest.mock import Mock

from sparkos.agent.context import WINDOW, AgentContext
from sparkos.agent.events import (
    PlanCreated,
    TaskCompleted,
    TaskStarted,
    TextDelta,
    ToolCompleted,
)
from sparkos.agent.planner import Plan, PlanStep
from sparkos.agent.runtime import AgentRuntime
from sparkos.agent.task import AgentTask, TaskStatus
from sparkos.infrastructure.llm.models import ChatMessage, ToolCall


class FakeClient:
    def __init__(
        self,
        turns: list[list[str | ToolCall]],
        summary: str = "compacted",
    ) -> None:
        self.turns = list(turns)
        self.summary = summary
        self.requests: list[list[dict]] = []
        self.compaction_requests: list[list[dict]] = []

    async def chat_stream(
        self,
        messages: list[ChatMessage],
        tools: list[dict] | None = None,
    ) -> AsyncIterator[str | ToolCall]:
        del tools
        self.requests.append([message.to_api_dict() for message in messages])
        for item in self.turns.pop(0):
            yield item

    async def chat_once(self, messages: list[dict]) -> str:
        self.compaction_requests.append(messages)
        return self.summary


class FakePlanner:
    def __init__(self) -> None:
        self.calls: list[tuple[AgentTask, object]] = []

    async def create_plan(self, task: AgentTask, context: object) -> Plan:
        self.calls.append((task, context))
        return Plan(task_id=task.id, steps=[PlanStep(description="inspect")])


def context_without_disk() -> AgentContext:
    context = AgentContext()
    context.ensure_session = Mock()  # type: ignore[method-assign]
    context.persist = Mock()  # type: ignore[method-assign]
    return context


async def collect_events(runtime: AgentRuntime, task: AgentTask) -> list[object]:
    return [event async for event in runtime.run(task)]


class AgentRuntimeTests(unittest.IsolatedAsyncioTestCase):
    async def test_no_tool_task_emits_lifecycle_and_text_events(self) -> None:
        client = FakeClient(turns=[["hello", " world"]])
        context = context_without_disk()
        runtime = AgentRuntime(context=context, client=client, skills=[], tools=[])
        task = AgentTask(goal="answer")

        events = await collect_events(runtime, task)

        self.assertIsInstance(events[0], TaskStarted)
        self.assertEqual(
            [event.text for event in events if isinstance(event, TextDelta)],
            ["hello", " world"],
        )
        self.assertIsInstance(events[-1], TaskCompleted)
        self.assertEqual(task.status, TaskStatus.SUCCEEDED)
        self.assertEqual(task.result, "hello world")
        self.assertEqual(
            [message.role for message in context.history],
            ["user", "assistant"],
        )

    async def test_runtime_records_assistant_call_before_tool_result(self) -> None:
        call = ToolCall(call_id="c1", name="read_file", arguments="{}")
        client = FakeClient(turns=[[call], ["done"]])
        context = context_without_disk()
        runtime = AgentRuntime(
            context=context,
            client=client,
            skills=[],
            tools=[],
            tool_executor=lambda *_: "ok",
        )

        events = await collect_events(runtime, AgentTask(goal="work"))

        self.assertEqual(
            [message.role for message in context.history],
            ["user", "assistant", "tool", "assistant"],
        )
        self.assertEqual(context.history[1].tool_calls, [call.to_api_dict()])
        self.assertEqual(context.history[2].tool_call_id, "c1")
        self.assertEqual(context.history[2].content, "ok")
        self.assertEqual(
            [
                event.tool_call.result
                for event in events
                if isinstance(event, ToolCompleted)
            ],
            ["ok"],
        )
        self.assertEqual(client.requests[1][-2]["tool_calls"], [call.to_api_dict()])
        self.assertEqual(client.requests[1][-1]["tool_call_id"], "c1")

    async def test_runtime_invokes_optional_planner(self) -> None:
        planner = FakePlanner()
        runtime = AgentRuntime(
            context=context_without_disk(),
            client=FakeClient(turns=[["done"]]),
            planner=planner,
            skills=[],
            tools=[],
        )
        task = AgentTask(goal="work")

        events = await collect_events(runtime, task)

        plan_events = [event for event in events if isinstance(event, PlanCreated)]
        self.assertEqual(len(planner.calls), 1)
        self.assertEqual(len(plan_events), 1)
        self.assertEqual(task.active_plan_id, plan_events[0].plan.id)
        request_contents = [
            message["content"] for message in runtime.client.requests[0]
        ]
        self.assertTrue(
            any("inspect" in content for content in request_contents),
            "created plan must guide the executor model",
        )

    async def test_runtime_compacts_before_building_model_context(self) -> None:
        context = context_without_disk()
        for index in range(WINDOW + 1):
            context.record_user(str(index))
        client = FakeClient(turns=[["done"]], summary="earlier turns")
        runtime = AgentRuntime(context=context, client=client, skills=[], tools=[])

        await collect_events(runtime, AgentTask(goal="new task"))

        self.assertEqual(context.summary, "earlier turns")
        self.assertEqual(context.summary_upto, 2)
        request_contents = [item["content"] for item in client.requests[0]]
        self.assertFalse("0" in request_contents)
        self.assertFalse("1" in request_contents)
        self.assertTrue(any("earlier turns" in item for item in request_contents))

    async def test_runtime_limits_tool_rounds_and_keeps_transcript_valid(self) -> None:
        first = ToolCall(call_id="c1", name="shell", arguments="{}")
        second = ToolCall(call_id="c2", name="shell", arguments="{}")
        context = context_without_disk()
        runtime = AgentRuntime(
            context=context,
            client=FakeClient(turns=[[first], [second]]),
            skills=[],
            tools=[],
            tool_executor=lambda *_: "ok",
            max_tool_rounds=1,
        )
        task = AgentTask(goal="loop")

        with self.assertRaisesRegex(RuntimeError, "工具调用轮数超过限制"):
            await collect_events(runtime, task)

        self.assertEqual(task.status, TaskStatus.FAILED)
        self.assertEqual(context.history[-2].role, "assistant")
        self.assertEqual(context.history[-1].role, "tool")
        self.assertEqual(context.history[-1].tool_call_id, "c2")


if __name__ == "__main__":
    unittest.main()
