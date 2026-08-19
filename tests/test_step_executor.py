from __future__ import annotations

import json
import unittest
from collections.abc import AsyncIterator

from sparkos.agent.planner import PlanStep
from sparkos.agent.step import ArtifactRef, StepResult
from sparkos.agent.step_executor import StepExecutionError, StepExecutor
from sparkos.agent.task import AgentTask
from sparkos.infrastructure.llm.models import ChatMessage, ToolCall


class FakeStepClient:
    def __init__(self, turns: list[list[str | ToolCall]]) -> None:
        self.turns = list(turns)
        self.requests: list[list[dict]] = []

    async def chat_stream(
        self,
        messages: list[ChatMessage],
        tools: list[dict] | None = None,
    ) -> AsyncIterator[str | ToolCall]:
        del tools
        self.requests.append([message.to_api_dict() for message in messages])
        for item in self.turns.pop(0):
            yield item


def sample_step() -> PlanStep:
    return PlanStep(
        id="s2",
        description="analyze data",
        depends_on=("s1",),
        success_criteria="analysis contains totals",
    )


def base_messages() -> list[ChatMessage]:
    return [
        ChatMessage(role="system", content="base prompt"),
        ChatMessage(role="user", content="analyze sales"),
    ]


class StepExecutorTests(unittest.IsolatedAsyncioTestCase):
    async def test_execution_preserves_final_assistant_chunks(self) -> None:
        executor = StepExecutor(
            client=FakeStepClient(turns=[["hello", " ", "world"]]),
            tools=[],
            tool_executor=None,
        )

        execution = await executor.execute(
            task=AgentTask(goal="greet"),
            step=sample_step(),
            dependency_results={},
            base_messages=base_messages(),
        )

        self.assertEqual(execution.text_chunks, ("hello", " ", "world"))
        self.assertEqual(execution.result.output, "hello world")

    async def test_executor_injects_current_step_and_dependency_results(self) -> None:
        client = FakeStepClient(turns=[["analysis done"]])
        executor = StepExecutor(client=client, tools=[], tool_executor=None)
        dependency = StepResult(
            success=True,
            output="120 rows loaded",
            evidence=("schema validated",),
            artifacts=(ArtifactRef(uri="artifact://sales", kind="table"),),
        )

        execution = await executor.execute(
            task=AgentTask(
                id="task-1",
                goal="analyze sales",
                input={"dataset": "sales.csv"},
            ),
            step=sample_step(),
            dependency_results={"s1": dependency},
            base_messages=base_messages(),
        )

        self.assertTrue(execution.result.success)
        self.assertEqual(execution.result.output, "analysis done")
        system_messages = [message for message in client.requests[0] if message["role"] == "system"]
        payload = json.loads(system_messages[-1]["content"].split("\n", 1)[1])
        self.assertEqual(payload["task_input"], {"dataset": "sales.csv"})
        self.assertEqual(payload["current_step"]["id"], "s2")
        self.assertEqual(
            payload["current_step"]["success_criteria"],
            "analysis contains totals",
        )
        self.assertEqual(
            payload["dependencies"]["s1"]["output"],
            "120 rows loaded",
        )
        self.assertEqual(
            payload["dependencies"]["s1"]["artifacts"][0]["uri"],
            "artifact://sales",
        )

    async def test_executor_keeps_tool_transcript_local_to_step(self) -> None:
        call = ToolCall(call_id="c1", name="read_file", arguments="{}")
        client = FakeStepClient(turns=[[call], ["done"]])
        original_messages = base_messages()
        executor = StepExecutor(
            client=client,
            tools=[],
            tool_executor=lambda *_: "file contents",
        )

        execution = await executor.execute(
            task=AgentTask(id="task-1", goal="analyze sales"),
            step=sample_step(),
            dependency_results={},
            base_messages=original_messages,
        )

        self.assertEqual(execution.result.output, "done")
        self.assertEqual(execution.tool_calls[0].result, "file contents")
        self.assertEqual(client.requests[1][-2]["tool_calls"][0]["id"], "c1")
        self.assertEqual(client.requests[1][-1]["tool_call_id"], "c1")
        self.assertEqual(len(original_messages), 2)

    async def test_large_tool_result_is_bounded_before_next_model_round(self) -> None:
        call = ToolCall(call_id="c1", name="run_spark_job", arguments="{}")
        client = FakeStepClient(turns=[[call], ["done"]])
        executor = StepExecutor(
            client=client,
            tools=[],
            tool_executor=lambda *_: "x" * 30_000,
        )

        execution = await executor.execute(
            task=AgentTask(goal="query"),
            step=sample_step(),
            dependency_results={},
            base_messages=base_messages(),
        )

        self.assertEqual(execution.result.output, "done")
        tool_message = client.requests[1][-1]
        self.assertLessEqual(len(tool_message["content"]), StepExecutor.MAX_TOOL_RESULT_CHARS)
        self.assertIn("结果已截断", tool_message["content"])

    async def test_tool_context_is_bounded_across_multiple_rounds(self) -> None:
        calls = [ToolCall(call_id=f"c{i}", name="run_spark_job", arguments="{}") for i in range(3)]
        client = FakeStepClient(turns=[[calls[0]], [calls[1]], [calls[2]], ["done"]])
        executor = StepExecutor(
            client=client,
            tools=[],
            tool_executor=lambda *_: "y" * 11_000,
        )

        await executor.execute(
            task=AgentTask(goal="query"),
            step=sample_step(),
            dependency_results={},
            base_messages=base_messages(),
        )

        final_request = client.requests[-1]
        tool_chars = sum(
            len(message.get("content", ""))
            for message in final_request
            if message.get("role") == "tool"
        )
        self.assertLessEqual(tool_chars, StepExecutor.MAX_TOOL_CONTEXT_CHARS)

    async def test_result_uses_only_final_non_tool_assistant_turn(self) -> None:
        call = ToolCall(call_id="c1", name="read_file", arguments="{}")
        client = FakeStepClient(turns=[["reading", call], ["final result"]])
        executor = StepExecutor(
            client=client,
            tools=[],
            tool_executor=lambda *_: "file contents",
        )

        execution = await executor.execute(
            task=AgentTask(goal="analyze"),
            step=sample_step(),
            dependency_results={},
            base_messages=base_messages(),
        )

        self.assertEqual(execution.result.output, "final result")
        self.assertEqual(
            [message["role"] for message in execution.transcript[-3:]],
            ["assistant", "tool", "assistant"],
        )

    async def test_retries_empty_final_turn_after_successful_tool(self) -> None:
        call = ToolCall(call_id="c1", name="web_fetch", arguments="{}")
        client = FakeStepClient(
            turns=[
                [call],
                [],
                ["上海今天晴，33°C。"],
            ]
        )
        executor = StepExecutor(
            client=client,
            tools=[],
            tool_executor=lambda *_: "Shanghai: sunny, 33C",
        )

        execution = await executor.execute(
            task=AgentTask(goal="上海"),
            step=sample_step(),
            dependency_results={},
            base_messages=base_messages(),
        )

        self.assertTrue(execution.result.success)
        self.assertEqual(execution.result.output, "上海今天晴，33°C。")
        self.assertEqual(len(client.requests), 3)
        self.assertTrue(any("已有工具结果" in message["content"] for message in client.requests[2]))

    async def test_executor_rejects_empty_step_output(self) -> None:
        executor = StepExecutor(
            client=FakeStepClient(turns=[[]]),
            tools=[],
            tool_executor=None,
        )

        execution = await executor.execute(
            task=AgentTask(goal="work"),
            step=sample_step(),
            dependency_results={},
            base_messages=base_messages(),
        )

        self.assertFalse(execution.result.success)
        self.assertEqual(execution.result.error, "步骤未产出结果")

    async def test_executor_limits_tool_rounds(self) -> None:
        first = ToolCall(call_id="c1", name="read_file", arguments="{}")
        second = ToolCall(call_id="c2", name="read_file", arguments="{}")
        executor = StepExecutor(
            client=FakeStepClient(turns=[[first], [second]]),
            tools=[],
            tool_executor=lambda *_: "ok",
            max_tool_rounds=1,
        )

        with self.assertRaisesRegex(StepExecutionError, "工具调用轮数超过限制"):
            await executor.execute(
                task=AgentTask(goal="loop"),
                step=sample_step(),
                dependency_results={},
                base_messages=base_messages(),
            )

    async def test_advisor_call_limit_enforced_within_step(self) -> None:
        advisor_call_1 = ToolCall(
            call_id="a1",
            name="ask_advisor",
            arguments='{"question":"q1","context":"c","attempts":"a"}',
        )
        advisor_call_2 = ToolCall(
            call_id="a2",
            name="ask_advisor",
            arguments='{"question":"q2","context":"c","attempts":"b"}',
        )
        final_text = "Decided without second advisor."
        # Both advisor calls in same model turn; second should be budget-blocked
        client = FakeStepClient(turns=[[advisor_call_1, advisor_call_2], [final_text]])
        executor = StepExecutor(
            client=client,
            tools=[],
            tool_executor=lambda *_: "advisor response",
            tool_call_limits={"ask_advisor": 1},
        )

        execution = await executor.execute(
            task=AgentTask(goal="decide"),
            step=sample_step(),
            dependency_results={},
            base_messages=base_messages(),
        )

        self.assertTrue(execution.result.success)
        self.assertEqual(execution.result.output, final_text)
        # First advisor call dispatched, second blocked — only 1 in executed_calls
        self.assertEqual(len(execution.tool_calls), 1)
        self.assertEqual(execution.tool_calls[0].result, "advisor response")
        # The budget-blocked message was sent to the model as a tool result
        tool_messages = [m for m in execution.transcript if m["role"] == "tool"]
        self.assertTrue(any("超过单步限制" in m["content"] for m in tool_messages))

    async def test_unlimited_tool_dispatches_normally(self) -> None:
        first = ToolCall(call_id="c1", name="read_file", arguments='{"path":"x"}')
        second = ToolCall(call_id="c2", name="read_file", arguments='{"path":"y"}')
        final_text = "result"
        client = FakeStepClient(turns=[[first], [second], [final_text]])
        executor = StepExecutor(
            client=client,
            tools=[],
            tool_executor=lambda *_: "content",
            max_tool_rounds=2,
        )

        execution = await executor.execute(
            task=AgentTask(goal="read"),
            step=sample_step(),
            dependency_results={},
            base_messages=base_messages(),
        )

        self.assertTrue(execution.result.success)
        self.assertEqual(execution.result.output, final_text)
        self.assertEqual(len(execution.tool_calls), 2)


if __name__ == "__main__":
    unittest.main()
