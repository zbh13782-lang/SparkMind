from __future__ import annotations

import asyncio
import unittest
from collections.abc import AsyncIterator
from pathlib import Path
from unittest.mock import Mock

from sparkos.agent.context import WINDOW, AgentContext
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
from sparkos.agent.planner import ClarificationRequest, Plan, PlanStep
from sparkos.agent.runtime import AgentRuntime
from sparkos.agent.skills.loader import Skill
from sparkos.agent.step import StepRun, StepStatus, StepVerification
from sparkos.agent.step_executor import StepExecutionError
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
    def __init__(self, plan: Plan) -> None:
        self.plan = plan
        self.calls: list[tuple[AgentTask, object]] = []

    async def create_plan(self, task: AgentTask, context: object) -> Plan:
        self.calls.append((task, context))
        return Plan(
            id=self.plan.id,
            task_id=task.id,
            steps=self.plan.steps,
            version=self.plan.version,
            source=self.plan.source,
        )


class FakeClarifyingPlanner:
    def __init__(self, question: str) -> None:
        self.question = question

    async def create_plan(
        self,
        task: AgentTask,
        context: object,
    ) -> ClarificationRequest:
        del task, context
        return ClarificationRequest(self.question)


class FailingPlanner:
    async def create_plan(self, task: AgentTask, context: object) -> Plan | None:
        del task, context
        raise RuntimeError("planner unavailable")


class FakeVerifier:
    def __init__(self, responses: list[StepVerification]) -> None:
        self.responses = list(responses)
        self.calls: list[tuple[AgentTask, PlanStep, object, object]] = []

    async def verify(
        self,
        task: AgentTask,
        step: PlanStep,
        result: object,
        dependency_results: object,
    ) -> StepVerification:
        self.calls.append((task, step, result, dependency_results))
        return self.responses.pop(0)


class FakeReplanner:
    def __init__(self, plan: Plan | None) -> None:
        self.plan = plan
        self.calls: list[dict] = []

    async def revise_plan(
        self,
        task: AgentTask,
        context: object,
        current_plan: Plan,
        step_runs: dict[str, StepRun],
        failed_step: PlanStep,
        reason: str,
    ) -> Plan | None:
        self.calls.append(
            {
                "task": task,
                "context": context,
                "current_plan": current_plan,
                "step_runs": step_runs,
                "failed_step": failed_step,
                "reason": reason,
            }
        )
        if self.plan is None:
            return None
        return Plan(
            id=self.plan.id,
            task_id=task.id,
            version=self.plan.version,
            source=self.plan.source,
            steps=self.plan.steps,
        )


class FakeTaskStore:
    def __init__(self) -> None:
        self.snapshots: list[dict] = []

    def save(
        self,
        task: AgentTask,
        plan: Plan | None,
        step_runs: dict[str, StepRun],
    ) -> None:
        self.snapshots.append(
            {
                "task_status": task.status,
                "task_result": task.result,
                "plan_id": plan.id if plan is not None else None,
                "step_statuses": {
                    step_id: run.status for step_id, run in step_runs.items()
                },
                "attempt_counts": {
                    step_id: run.attempt_count for step_id, run in step_runs.items()
                },
                "transcripts": {
                    step_id: list(getattr(run, "transcript", []))
                    for step_id, run in step_runs.items()
                },
            }
        )


class FailingTaskStore:
    def save(
        self,
        task: AgentTask,
        plan: Plan,
        step_runs: dict[str, StepRun],
    ) -> None:
        del task, plan, step_runs
        raise OSError("disk full")


class BlockingClient(FakeClient):
    def __init__(self) -> None:
        super().__init__(turns=[])
        self.started = asyncio.Event()

    async def chat_stream(
        self,
        messages: list[ChatMessage],
        tools: list[dict] | None = None,
    ) -> AsyncIterator[str | ToolCall]:
        del messages, tools
        self.started.set()
        await asyncio.Event().wait()
        yield "unreachable"


class BlockingVerifier:
    def __init__(self) -> None:
        self.started = asyncio.Event()

    async def verify(
        self,
        task: AgentTask,
        step: PlanStep,
        result: object,
        dependency_results: object,
    ) -> StepVerification:
        del task, step, result, dependency_results
        self.started.set()
        await asyncio.Event().wait()
        return StepVerification(True, "unreachable", False)


def two_step_plan(task_id: str = "placeholder") -> Plan:
    return Plan(
        id="plan-1",
        task_id=task_id,
        steps=(
            PlanStep(
                id="s1",
                description="inspect input",
                success_criteria="input inspected",
            ),
            PlanStep(
                id="s2",
                description="produce report",
                depends_on=("s1",),
                success_criteria="report produced",
            ),
        ),
    )


def context_without_disk() -> AgentContext:
    context = AgentContext()
    context.ensure_session = Mock()  # type: ignore[method-assign]
    context.persist = Mock()  # type: ignore[method-assign]
    return context


async def collect_events(runtime: AgentRuntime, task: AgentTask) -> list[object]:
    return [event async for event in runtime.run(task)]


class AgentRuntimeTests(unittest.IsolatedAsyncioTestCase):
    async def test_planner_can_pause_task_and_ask_for_clarification(self) -> None:
        context = context_without_disk()
        store = FakeTaskStore()
        client = FakeClient(turns=[])
        runtime = AgentRuntime(
            context=context,
            client=client,
            planner=FakeClarifyingPlanner("请提供要分析的文件路径。"),
            skills=[],
            tools=[],
            task_store=store,
        )
        task = AgentTask(goal="帮我分析一下")

        events = await collect_events(runtime, task)

        self.assertEqual(
            [type(event) for event in events],
            [TaskStarted, ClarificationRequested],
        )
        clarification = events[-1]
        assert isinstance(clarification, ClarificationRequested)
        self.assertEqual(clarification.question, "请提供要分析的文件路径。")
        self.assertEqual(task.status, TaskStatus.WAITING_INPUT)
        self.assertEqual(client.requests, [])
        self.assertEqual(
            [(message.role, message.content) for message in context.history],
            [
                ("user", "帮我分析一下"),
                ("assistant", "请提供要分析的文件路径。"),
            ],
        )
        self.assertEqual(store.snapshots[-1]["plan_id"], None)

    async def test_runtime_rejects_more_than_one_replan(self) -> None:
        with self.assertRaisesRegex(ValueError, "最多允许 1 次"):
            AgentRuntime(
                context=context_without_disk(),
                client=FakeClient(turns=[]),
                skills=[],
                tools=[],
                max_replans=2,
            )

    async def test_simple_task_uses_direct_step_and_records_only_final_answer(
        self,
    ) -> None:
        client = FakeClient(turns=[["hello", " world"]])
        context = context_without_disk()
        store = FakeTaskStore()
        runtime = AgentRuntime(
            context=context,
            client=client,
            skills=[],
            tools=[],
            task_store=store,
        )
        task = AgentTask(goal="answer")

        events = await collect_events(runtime, task)

        self.assertIsInstance(events[0], TaskStarted)
        self.assertEqual(
            [event.plan.source for event in events if isinstance(event, PlanCreated)],
            ["direct"],
        )
        self.assertEqual(
            [event.step.id for event in events if isinstance(event, StepStarted)],
            ["direct"],
        )
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
        self.assertEqual(context.history[-1].content, "hello world")
        self.assertEqual(store.snapshots[-1]["task_status"], TaskStatus.SUCCEEDED)

    async def test_runtime_executes_plan_steps_in_dependency_order_and_synthesizes(
        self,
    ) -> None:
        planner = FakePlanner(two_step_plan())
        client = FakeClient(
            turns=[["inspected"], ["report data"], ["final ", "answer"]]
        )
        store = FakeTaskStore()
        runtime = AgentRuntime(
            context=context_without_disk(),
            client=client,
            planner=planner,
            skills=[
                Skill(
                    name="spark-sql",
                    description="Generate and optimize Spark SQL",
                    path=Path("spark-sql/SKILL.md"),
                )
            ],
            tools=[],
            task_store=store,
        )
        task = AgentTask(goal="analyze")

        events = await collect_events(runtime, task)

        self.assertEqual(
            [event.step.id for event in events if isinstance(event, StepStarted)],
            ["s1", "s2"],
        )
        self.assertEqual(
            [event.step.id for event in events if isinstance(event, StepCompleted)],
            ["s1", "s2"],
        )
        self.assertEqual(
            [event.text for event in events if isinstance(event, TextDelta)],
            ["final ", "answer"],
        )
        self.assertEqual(task.result, "final answer")
        self.assertEqual(
            [message.role for message in runtime.context.history],
            ["user", "assistant"],
        )
        second_step_system = [
            message["content"]
            for message in client.requests[1]
            if message["role"] == "system"
        ][-1]
        self.assertIn("inspected", second_step_system)
        final_system = [
            message["content"]
            for message in client.requests[2]
            if message["role"] == "system"
        ][-1]
        self.assertIn("report data", final_system)
        self.assertGreaterEqual(len(store.snapshots), 6)
        planning_snapshot = planner.calls[0][1]
        self.assertEqual(planning_snapshot.skills[0].name, "spark-sql")
        self.assertEqual(
            planning_snapshot.skills[0].description,
            "Generate and optimize Spark SQL",
        )

    async def test_final_synthesis_rejects_textual_tool_call_markup(self) -> None:
        raw_tool_call = (
            "<tool_call><tool_name>web_fetch</tool_name>"
            "<parameter=url>https://wttr.in/Shanghai</parameter></tool_call>"
        )
        runtime = AgentRuntime(
            context=context_without_disk(),
            client=FakeClient(turns=[["inspected"], ["final report"], [raw_tool_call]]),
            planner=FakePlanner(two_step_plan()),
            skills=[],
            tools=[],
            task_store=FakeTaskStore(),
        )
        task = AgentTask(goal="analyze")

        events = await collect_events(runtime, task)

        self.assertEqual(
            [event.text for event in events if isinstance(event, TextDelta)],
            ["final report"],
        )
        self.assertEqual(task.result, "final report")
        self.assertNotIn("<tool_call>", runtime.context.history[-1].content)

    async def test_direct_answer_rejects_textual_tool_call_markup(self) -> None:
        runtime = AgentRuntime(
            context=context_without_disk(),
            client=FakeClient(
                turns=[
                    [
                        "<tool_call>",
                        "<tool_name>web_fetch</tool_name></tool_call>",
                    ]
                ]
            ),
            skills=[],
            tools=[],
            task_store=FakeTaskStore(),
        )
        task = AgentTask(goal="weather")

        events = await collect_events(runtime, task)

        deltas = [event.text for event in events if isinstance(event, TextDelta)]
        self.assertEqual(deltas, ["未能生成有效的最终回答，请重试。"])
        self.assertNotIn("<tool_call", task.result or "")
        self.assertNotIn("<tool_call", runtime.context.history[-1].content)

    async def test_planned_answer_rejects_unclosed_textual_tool_call(self) -> None:
        runtime = AgentRuntime(
            context=context_without_disk(),
            client=FakeClient(
                turns=[
                    ["inspected"],
                    ["final report"],
                    ["<tool_", "call><tool_name>web_fetch"],
                ]
            ),
            planner=FakePlanner(two_step_plan()),
            skills=[],
            tools=[],
            task_store=FakeTaskStore(),
        )
        task = AgentTask(goal="analyze")

        events = await collect_events(runtime, task)

        deltas = [event.text for event in events if isinstance(event, TextDelta)]
        self.assertEqual(deltas, ["final report"])
        self.assertEqual(task.result, "final report")
        self.assertNotIn("<tool_call", runtime.context.history[-1].content)

    async def test_verifier_rejection_retries_once_with_feedback(self) -> None:
        verifier = FakeVerifier(
            [
                StepVerification(False, "missing temperature", True),
                StepVerification(
                    True,
                    "criteria satisfied",
                    False,
                    evidence=("temperature 33C",),
                ),
            ]
        )
        store = FakeTaskStore()
        client = FakeClient(turns=[["humidity only"], ["temperature 33C"]])
        runtime = AgentRuntime(
            context=context_without_disk(),
            client=client,
            verifier=verifier,
            skills=[],
            tools=[],
            task_store=store,
        )
        task = AgentTask(goal="weather")

        events = await collect_events(runtime, task)

        self.assertEqual(task.result, "temperature 33C")
        self.assertEqual(store.snapshots[-1]["attempt_counts"]["direct"], 2)
        self.assertEqual(
            len([event for event in events if isinstance(event, StepRetrying)]),
            1,
        )
        self.assertEqual(
            len(
                [
                    event
                    for event in events
                    if isinstance(event, StepVerificationCompleted)
                ]
            ),
            2,
        )
        completed = next(event for event in events if isinstance(event, StepCompleted))
        self.assertEqual(completed.result.evidence, ("temperature 33C",))
        retry_system = [
            message["content"]
            for message in client.requests[1]
            if message["role"] == "system"
        ][-1]
        self.assertIn("missing temperature", retry_system)

    async def test_non_retryable_verification_rejection_fails_step(self) -> None:
        runtime = AgentRuntime(
            context=context_without_disk(),
            client=FakeClient(turns=[["source unavailable"]]),
            verifier=FakeVerifier(
                [StepVerification(False, "source is blocked", False)]
            ),
            skills=[],
            tools=[],
            task_store=FakeTaskStore(),
        )
        task = AgentTask(goal="weather")
        events: list[object] = []

        with self.assertRaisesRegex(RuntimeError, "source is blocked"):
            async for event in runtime.run(task):
                events.append(event)

        self.assertEqual(task.status, TaskStatus.FAILED)
        self.assertEqual(
            len([event for event in events if isinstance(event, StepRetrying)]),
            0,
        )
        self.assertEqual(
            len([event for event in events if isinstance(event, StepFailed)]),
            1,
        )

    async def test_verification_failure_replans_once_and_reuses_succeeded_step(
        self,
    ) -> None:
        original = two_step_plan()
        revised = Plan(
            id="plan-2",
            task_id="placeholder",
            version=2,
            source="replan",
            steps=(
                original.steps[0],
                PlanStep(
                    id="s3",
                    description="produce fallback report",
                    depends_on=("s1",),
                    success_criteria="fallback report produced",
                ),
            ),
        )
        replanner = FakeReplanner(revised)
        verifier = FakeVerifier(
            [
                StepVerification(True, "inspected", False),
                StepVerification(False, "primary source incomplete", True),
                StepVerification(False, "primary source still incomplete", True),
                StepVerification(True, "fallback complete", False),
            ]
        )
        client = FakeClient(
            turns=[
                ["inspected"],
                ["bad report"],
                ["still bad"],
                ["fallback report"],
                ["final answer"],
            ]
        )
        store = FakeTaskStore()
        runtime = AgentRuntime(
            context=context_without_disk(),
            client=client,
            planner=FakePlanner(original),
            replanner=replanner,
            verifier=verifier,
            skills=[],
            tools=[],
            task_store=store,
        )
        task = AgentTask(goal="analyze")

        events = await collect_events(runtime, task)

        self.assertEqual(task.status, TaskStatus.SUCCEEDED)
        self.assertEqual(task.result, "final answer")
        self.assertEqual(
            [event.step.id for event in events if isinstance(event, StepStarted)],
            ["s1", "s2", "s2", "s3"],
        )
        replan_events = [event for event in events if isinstance(event, PlanReplanned)]
        self.assertEqual(len(replan_events), 1)
        self.assertEqual(replan_events[0].previous_plan.id, "plan-1")
        self.assertEqual(replan_events[0].plan.id, "plan-2")
        self.assertEqual(len(replanner.calls), 1)
        self.assertEqual(store.snapshots[-1]["plan_id"], "plan-2")

    async def test_replan_can_rewrite_pending_downstream_dependencies(self) -> None:
        original = Plan(
            id="plan-1",
            task_id="placeholder",
            steps=(
                PlanStep(
                    id="s1",
                    description="inspect inputs",
                    success_criteria="inputs inspected",
                ),
                PlanStep(
                    id="s2",
                    description="load primary source",
                    depends_on=("s1",),
                    success_criteria="source loaded",
                ),
                PlanStep(
                    id="s4",
                    description="produce report",
                    depends_on=("s2",),
                    success_criteria="report produced",
                ),
            ),
        )
        revised = Plan(
            id="plan-2",
            task_id="placeholder",
            version=2,
            source="replan",
            steps=(
                original.steps[0],
                PlanStep(
                    id="s3",
                    description="load fallback source",
                    depends_on=("s1",),
                    success_criteria="source loaded",
                ),
                PlanStep(
                    id="s4",
                    description="produce report",
                    depends_on=("s3",),
                    success_criteria="report produced",
                ),
            ),
        )
        runtime = AgentRuntime(
            context=context_without_disk(),
            client=FakeClient(
                turns=[
                    ["inspected"],
                    ["bad source"],
                    ["still bad"],
                    ["fallback loaded"],
                    ["report"],
                    ["final answer"],
                ]
            ),
            planner=FakePlanner(original),
            replanner=FakeReplanner(revised),
            verifier=FakeVerifier(
                [
                    StepVerification(True, "inspected", False),
                    StepVerification(False, "primary unavailable", True),
                    StepVerification(False, "primary still unavailable", True),
                    StepVerification(True, "fallback loaded", False),
                    StepVerification(True, "report produced", False),
                ]
            ),
            skills=[],
            tools=[],
            task_store=FakeTaskStore(),
        )

        events = await collect_events(runtime, AgentTask(goal="analyze"))

        self.assertEqual(
            [event.step.id for event in events if isinstance(event, StepStarted)],
            ["s1", "s2", "s2", "s3", "s4"],
        )
        self.assertEqual(
            len([event for event in events if isinstance(event, PlanReplanned)]),
            1,
        )

    async def test_runtime_never_replans_more_than_once(self) -> None:
        replanner = FakeReplanner(
            Plan(
                id="plan-2",
                task_id="placeholder",
                version=2,
                source="replan",
                steps=(
                    PlanStep(
                        id="fallback",
                        description="fallback",
                        success_criteria="fallback succeeds",
                    ),
                ),
            )
        )
        verifier = FakeVerifier(
            [
                StepVerification(False, "first incomplete", True),
                StepVerification(False, "first still incomplete", True),
                StepVerification(False, "fallback incomplete", True),
                StepVerification(False, "fallback still incomplete", True),
            ]
        )
        runtime = AgentRuntime(
            context=context_without_disk(),
            client=FakeClient(turns=[["bad 1"], ["bad 2"], ["bad 3"], ["bad 4"]]),
            replanner=replanner,
            verifier=verifier,
            skills=[],
            tools=[],
            task_store=FakeTaskStore(),
        )

        with self.assertRaisesRegex(RuntimeError, "fallback still incomplete"):
            await collect_events(runtime, AgentTask(goal="work"))

        self.assertEqual(len(replanner.calls), 1)

    async def test_runtime_rejects_revised_plan_without_replan_source(self) -> None:
        replanner = FakeReplanner(
            Plan(
                id="plan-2",
                task_id="placeholder",
                version=2,
                source="llm",
                steps=(
                    PlanStep(
                        id="fallback",
                        description="fallback",
                        success_criteria="fallback succeeds",
                    ),
                ),
            )
        )
        verifier = FakeVerifier(
            [
                StepVerification(False, "incomplete", True),
                StepVerification(False, "still incomplete", True),
                StepVerification(True, "fallback complete", False),
            ]
        )
        runtime = AgentRuntime(
            context=context_without_disk(),
            client=FakeClient(turns=[["bad 1"], ["bad 2"], ["fallback"]]),
            replanner=replanner,
            verifier=verifier,
            skills=[],
            tools=[],
            task_store=FakeTaskStore(),
        )
        events: list[object] = []

        with self.assertRaisesRegex(RuntimeError, "still incomplete"):
            async for event in runtime.run(AgentTask(goal="work")):
                events.append(event)

        self.assertEqual(len(replanner.calls), 1)
        self.assertFalse(any(isinstance(event, PlanReplanned) for event in events))

    async def test_runtime_rejects_replan_that_resets_failed_step_attempts(
        self,
    ) -> None:
        original = Plan(
            id="plan-1",
            task_id="placeholder",
            steps=(
                PlanStep(
                    id="s1",
                    description="load primary source",
                    success_criteria="source loaded",
                ),
            ),
        )
        replanner = FakeReplanner(
            Plan(
                id="plan-2",
                task_id="placeholder",
                version=2,
                source="replan",
                steps=original.steps,
            )
        )
        runtime = AgentRuntime(
            context=context_without_disk(),
            client=FakeClient(turns=[["bad 1"], ["bad 2"], ["bad 3"], ["final"]]),
            planner=FakePlanner(original),
            replanner=replanner,
            verifier=FakeVerifier(
                [
                    StepVerification(False, "incomplete", True),
                    StepVerification(False, "still incomplete", True),
                    StepVerification(True, "unexpected third attempt", False),
                ]
            ),
            skills=[],
            tools=[],
            task_store=FakeTaskStore(),
        )
        events: list[object] = []

        with self.assertRaisesRegex(RuntimeError, "still incomplete"):
            async for event in runtime.run(AgentTask(goal="work")):
                events.append(event)

        starts = [event for event in events if isinstance(event, StepStarted)]
        self.assertEqual(len(starts), 2)
        self.assertFalse(any(isinstance(event, PlanReplanned) for event in events))

    async def test_runtime_rejects_replan_that_reuses_plan_id(self) -> None:
        original = Plan(
            id="plan-1",
            task_id="placeholder",
            steps=(
                PlanStep(
                    id="s1",
                    description="load primary source",
                    success_criteria="source loaded",
                ),
            ),
        )
        replanner = FakeReplanner(
            Plan(
                id="plan-1",
                task_id="placeholder",
                version=2,
                source="replan",
                steps=(
                    PlanStep(
                        id="fallback",
                        description="load fallback source",
                        success_criteria="source loaded",
                    ),
                ),
            )
        )
        runtime = AgentRuntime(
            context=context_without_disk(),
            client=FakeClient(turns=[["bad 1"], ["bad 2"], ["fallback"], ["final"]]),
            planner=FakePlanner(original),
            replanner=replanner,
            verifier=FakeVerifier(
                [
                    StepVerification(False, "incomplete", True),
                    StepVerification(False, "still incomplete", True),
                    StepVerification(True, "fallback complete", False),
                ]
            ),
            skills=[],
            tools=[],
            task_store=FakeTaskStore(),
        )

        with self.assertRaisesRegex(RuntimeError, "still incomplete"):
            await collect_events(runtime, AgentTask(goal="work"))

    async def test_step_tool_calls_emit_scoped_events_and_session_history(
        self,
    ) -> None:
        call = ToolCall(call_id="c1", name="read_file", arguments="{}")
        client = FakeClient(turns=[[call], ["done"]])
        context = context_without_disk()
        store = FakeTaskStore()
        runtime = AgentRuntime(
            context=context,
            client=client,
            skills=[],
            tools=[],
            tool_executor=lambda *_: "file contents",
            task_store=store,
        )

        events = await collect_events(runtime, AgentTask(goal="work"))

        tool_events = [
            event for event in events if isinstance(event, StepToolCompleted)
        ]
        self.assertEqual(len(tool_events), 1)
        self.assertEqual(tool_events[0].step.id, "direct")
        self.assertEqual(tool_events[0].tool_call.result, "file contents")
        self.assertEqual(
            [message.role for message in context.history],
            ["user", "assistant", "tool", "assistant"],
        )
        self.assertEqual(context.history[1].tool_calls, [call.to_api_dict()])
        self.assertEqual(context.history[2].role, "tool")
        self.assertEqual(context.history[2].tool_call_id, "c1")
        self.assertEqual(context.history[2].content, "file contents")
        self.assertEqual(context.history[3].content, "done")
        self.assertTrue(
            any(
                snapshot["transcripts"]["direct"]
                and snapshot["transcripts"]["direct"][-1].get("tool_call_id") == "c1"
                for snapshot in store.snapshots
            )
        )

    async def test_multiple_tool_calls_are_recorded_as_one_history_round(
        self,
    ) -> None:
        first = ToolCall(call_id="c1", name="shell", arguments="{}")
        second = ToolCall(call_id="c2", name="shell", arguments="{}")
        context = context_without_disk()
        client = FakeClient(turns=[[first, second], ["done"], ["follow-up done"]])
        runtime = AgentRuntime(
            context=context,
            client=client,
            skills=[],
            tools=[],
            tool_executor=lambda *_: "ok",
            task_store=FakeTaskStore(),
        )

        try:
            events = await collect_events(runtime, AgentTask(goal="multi"))
        except TypeError as exc:
            self.fail(f"multiple tool calls must not corrupt history: {exc}")

        tool_events = [
            event for event in events if isinstance(event, StepToolCompleted)
        ]
        self.assertEqual(len(tool_events), 2)
        self.assertEqual(
            [message.role for message in context.history],
            ["user", "assistant", "tool", "tool", "assistant"],
        )
        self.assertEqual(
            context.history[1].tool_calls,
            [first.to_api_dict(), second.to_api_dict()],
        )
        self.assertEqual(
            [context.history[2].tool_call_id, context.history[3].tool_call_id],
            ["c1", "c2"],
        )

        await collect_events(runtime, AgentTask(goal="what happened?"))

        replayed_history = [
            message for message in client.requests[2] if message["role"] != "system"
        ]
        self.assertEqual(
            [message["role"] for message in replayed_history],
            ["user", "assistant", "tool", "tool", "assistant", "user"],
        )
        self.assertEqual(replayed_history[1]["tool_calls"][0]["id"], "c1")
        self.assertEqual(replayed_history[2]["tool_call_id"], "c1")
        self.assertEqual(replayed_history[3]["tool_call_id"], "c2")

    async def test_cancelled_multi_tool_round_does_not_record_partial_history(
        self,
    ) -> None:
        first = ToolCall(call_id="c1", name="fast_tool", arguments="{}")
        second = ToolCall(call_id="c2", name="slow_tool", arguments="{}")
        second_started = asyncio.Event()

        async def tool_executor(name: str, arguments: dict) -> str:
            del arguments
            if name == "fast_tool":
                return "first result"
            second_started.set()
            await asyncio.Event().wait()
            return "unreachable"

        context = context_without_disk()
        runtime = AgentRuntime(
            context=context,
            client=FakeClient(turns=[[first, second]]),
            skills=[],
            tools=[],
            tool_executor=tool_executor,
            task_store=FakeTaskStore(),
        )

        execution = asyncio.create_task(
            collect_events(runtime, AgentTask(goal="cancel multi"))
        )
        await second_started.wait()
        execution.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await execution

        self.assertEqual(
            [message.role for message in context.history],
            ["user"],
        )

    async def test_runtime_compacts_before_step_execution(self) -> None:
        context = context_without_disk()
        for index in range(WINDOW + 1):
            context.record_user(str(index))
        client = FakeClient(turns=[["done"]], summary="earlier turns")
        runtime = AgentRuntime(
            context=context,
            client=client,
            skills=[],
            tools=[],
            task_store=FakeTaskStore(),
        )

        await collect_events(runtime, AgentTask(goal="new task"))

        self.assertEqual(context.summary, "earlier turns")
        self.assertEqual(context.summary_upto, 2)
        request_contents = [item["content"] for item in client.requests[0]]
        self.assertNotIn("0", request_contents)
        self.assertNotIn("1", request_contents)
        self.assertTrue(any("earlier turns" in item for item in request_contents))

    async def test_failed_step_marks_task_failed_and_emits_step_failure(self) -> None:
        runtime = AgentRuntime(
            context=context_without_disk(),
            client=FakeClient(turns=[[]]),
            skills=[],
            tools=[],
            task_store=FakeTaskStore(),
        )
        task = AgentTask(goal="work")
        captured: list[object] = []

        with self.assertRaisesRegex(RuntimeError, "步骤未产出结果"):
            async for event in runtime.run(task):
                captured.append(event)

        self.assertEqual(task.status, TaskStatus.FAILED)
        self.assertEqual(
            [event.step.id for event in captured if isinstance(event, StepFailed)],
            ["direct"],
        )
        self.assertEqual(
            [message.role for message in runtime.context.history], ["user"]
        )

    async def test_runtime_limits_tool_rounds_inside_step(self) -> None:
        first = ToolCall(call_id="c1", name="shell", arguments="{}")
        second = ToolCall(call_id="c2", name="shell", arguments="{}")
        runtime = AgentRuntime(
            context=context_without_disk(),
            client=FakeClient(turns=[[first], [second]]),
            skills=[],
            tools=[],
            tool_executor=lambda *_: "ok",
            max_tool_rounds=1,
            task_store=FakeTaskStore(),
        )
        task = AgentTask(goal="loop")

        with self.assertRaisesRegex(RuntimeError, "工具调用轮数超过限制"):
            await collect_events(runtime, task)

        self.assertEqual(task.status, TaskStatus.FAILED)
        self.assertEqual(
            [message.role for message in runtime.context.history],
            ["user", "assistant", "tool", "assistant", "tool"],
        )

    async def test_tool_round_limit_records_multiple_results_once(self) -> None:
        first = ToolCall(call_id="c1", name="shell", arguments="{}")
        second = ToolCall(call_id="c2", name="shell", arguments="{}")
        context = context_without_disk()
        runtime = AgentRuntime(
            context=context,
            client=FakeClient(turns=[[first, second]]),
            skills=[],
            tools=[],
            tool_executor=lambda *_: "unreachable",
            max_tool_rounds=0,
            task_store=FakeTaskStore(),
        )

        with self.assertRaisesRegex(StepExecutionError, "工具调用轮数超过限制"):
            await collect_events(runtime, AgentTask(goal="limit multi"))

        self.assertEqual(
            [message.role for message in context.history],
            ["user", "assistant", "tool", "tool"],
        )
        self.assertEqual(
            [context.history[2].tool_call_id, context.history[3].tool_call_id],
            ["c1", "c2"],
        )

    async def test_cancelling_task_cancels_running_step_snapshot(self) -> None:
        client = BlockingClient()
        store = FakeTaskStore()
        runtime = AgentRuntime(
            context=context_without_disk(),
            client=client,
            skills=[],
            tools=[],
            task_store=store,
        )
        task = AgentTask(goal="wait")

        async def drain() -> None:
            async for _ in runtime.run(task):
                pass

        execution = asyncio.create_task(drain())
        await client.started.wait()
        execution.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await execution

        self.assertEqual(task.status, TaskStatus.CANCELLED)
        self.assertEqual(
            store.snapshots[-1]["step_statuses"]["direct"],
            StepStatus.CANCELLED,
        )

    async def test_closing_event_stream_cancels_verifying_step_snapshot(self) -> None:
        task = AgentTask(goal="verify")
        store = FakeTaskStore()
        runtime = AgentRuntime(
            context=context_without_disk(),
            client=FakeClient(turns=[["candidate"]]),
            verifier=FakeVerifier(
                [StepVerification(True, "criteria satisfied", False)]
            ),
            skills=[],
            tools=[],
            task_store=store,
        )
        stream = runtime.run(task)

        async for event in stream:
            if isinstance(event, StepVerificationCompleted):
                break
        await stream.aclose()

        self.assertEqual(task.status, TaskStatus.CANCELLED)
        self.assertEqual(
            store.snapshots[-1]["step_statuses"]["direct"],
            StepStatus.CANCELLED,
        )

    async def test_closing_event_stream_after_task_started_cancels_task(self) -> None:
        task = AgentTask(goal="start")
        store = FakeTaskStore()
        runtime = AgentRuntime(
            context=context_without_disk(),
            client=FakeClient(turns=[]),
            skills=[],
            tools=[],
            task_store=store,
        )
        stream = runtime.run(task)

        event = await anext(stream)
        self.assertIsInstance(event, TaskStarted)
        await stream.aclose()

        self.assertEqual(task.status, TaskStatus.CANCELLED)
        self.assertEqual(store.snapshots[-1]["task_status"], TaskStatus.CANCELLED)
        self.assertIsNone(store.snapshots[-1]["plan_id"])

    async def test_planner_failure_persists_failed_task_without_plan(self) -> None:
        task = AgentTask(goal="start")
        store = FakeTaskStore()
        runtime = AgentRuntime(
            context=context_without_disk(),
            client=FakeClient(turns=[]),
            planner=FailingPlanner(),
            skills=[],
            tools=[],
            task_store=store,
        )

        with self.assertRaisesRegex(RuntimeError, "planner unavailable"):
            await collect_events(runtime, task)

        self.assertEqual(task.status, TaskStatus.FAILED)
        self.assertEqual(store.snapshots[-1]["task_status"], TaskStatus.FAILED)
        self.assertIsNone(store.snapshots[-1]["plan_id"])

    async def test_cancelling_verifier_call_cancels_verifying_step_snapshot(
        self,
    ) -> None:
        task = AgentTask(goal="verify")
        store = FakeTaskStore()
        verifier = BlockingVerifier()
        runtime = AgentRuntime(
            context=context_without_disk(),
            client=FakeClient(turns=[["candidate"]]),
            verifier=verifier,
            skills=[],
            tools=[],
            task_store=store,
        )

        async def drain() -> None:
            async for _ in runtime.run(task):
                pass

        execution = asyncio.create_task(drain())
        await verifier.started.wait()
        execution.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await execution

        self.assertEqual(task.status, TaskStatus.CANCELLED)
        self.assertEqual(
            store.snapshots[-1]["step_statuses"]["direct"],
            StepStatus.CANCELLED,
        )

    async def test_cancelling_blocked_tool_preserves_started_call_transcript(
        self,
    ) -> None:
        call = ToolCall(call_id="c1", name="slow_tool", arguments="{}")
        client = FakeClient(turns=[[call]])
        store = FakeTaskStore()
        tool_started = asyncio.Event()

        async def blocking_tool(name: str, arguments: dict) -> str:
            del name, arguments
            tool_started.set()
            await asyncio.Event().wait()
            return "unreachable"

        runtime = AgentRuntime(
            context=context_without_disk(),
            client=client,
            skills=[],
            tools=[],
            tool_executor=blocking_tool,
            task_store=store,
        )

        async def drain() -> None:
            async for _ in runtime.run(AgentTask(goal="wait for tool")):
                pass

        execution = asyncio.create_task(drain())
        await tool_started.wait()
        execution.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await execution

        final_transcript = store.snapshots[-1]["transcripts"]["direct"]
        self.assertEqual(final_transcript[-1]["tool_calls"][0]["id"], "c1")

    async def test_snapshot_failure_preserves_error_and_emits_task_failed(self) -> None:
        runtime = AgentRuntime(
            context=context_without_disk(),
            client=FakeClient(turns=[]),
            skills=[],
            tools=[],
            task_store=FailingTaskStore(),
        )
        task = AgentTask(goal="work")
        events: list[object] = []

        with self.assertRaisesRegex(OSError, "disk full"):
            async for event in runtime.run(task):
                events.append(event)

        self.assertEqual(task.status, TaskStatus.FAILED)
        self.assertTrue(any(isinstance(event, TaskFailed) for event in events))


if __name__ == "__main__":
    unittest.main()
