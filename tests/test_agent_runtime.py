from __future__ import annotations

import asyncio
import unittest
from unittest.mock import Mock

from sparkos.agent.context import AgentContext
from sparkos.agent.events import (
    TaskCompleted,
)
from sparkos.agent.planner import ClarificationRequest, Plan, PlanStep
from sparkos.agent.runtime import AgentRuntime
from sparkos.agent.task import AgentTask


class FakeClient:
    def __init__(self, turns, summary="compacted"):
        self.turns = list(turns)
        self.summary = summary
        self.requests = []
        self.compaction_requests = []

    async def chat_stream(self, messages, tools=None):
        del tools
        self.requests.append([m.to_api_dict() for m in messages])
        for item in self.turns.pop(0):
            yield item

    async def chat_once(self, messages, *, json_object=False):
        del json_object
        self.compaction_requests.append(messages)
        return self.summary


class FakePlanner:
    def __init__(self, plan):
        self.plan = plan
        self.calls = []

    async def create_plan(self, task, context):
        self.calls.append((task, context))
        return Plan(
            id=self.plan.id,
            task_id=task.id,
            steps=self.plan.steps,
            version=self.plan.version,
            source=self.plan.source,
        )


class FakeClarifyingPlanner:
    def __init__(self, question):
        self.question = question

    async def create_plan(self, task, context):
        return ClarificationRequest(self.question)


class FailingPlanner:
    async def create_plan(self, task, context):
        raise RuntimeError("planner unavailable")


class FakeReplanner:
    def __init__(self, plan):
        self.plan = plan
        self.calls = []

    async def revise_plan(self, task, context, current_plan, step_runs, failed_step, reason):
        self.calls.append({"task": task, "reason": reason})
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
    def __init__(self):
        self.snapshots = []

    def save(self, task, plan, step_runs):
        self.snapshots.append(
            {
                "task_status": task.status,
                "plan_id": plan.id if plan else None,
                "step_statuses": {sid: r.status for sid, r in step_runs.items()},
            }
        )


class FailingTaskStore:
    def save(self, task, plan, step_runs):
        raise OSError("disk full")


class BlockingClient(FakeClient):
    def __init__(self):
        super().__init__(turns=[])
        self.started = asyncio.Event()

    async def chat_stream(self, messages, tools=None):
        self.started.set()
        await asyncio.Event().wait()
        yield "unreachable"


def two_step_plan(task_id="placeholder"):
    return Plan(
        id="plan-1",
        task_id=task_id,
        steps=(
            PlanStep(id="s1", description="inspect", success_criteria="done"),
            PlanStep(
                id="s2",
                description="report",
                depends_on=("s1",),
                success_criteria="done",
            ),
        ),
    )


def context_without_disk():
    ctx = AgentContext()
    ctx.ensure_session = Mock()
    ctx.persist = Mock()
    return ctx


async def collect_events(rt, task):
    return [e async for e in rt.run(task)]


class AgentRuntimeTests(unittest.IsolatedAsyncioTestCase):
    async def test_simple_task(self):
        rt = AgentRuntime(
            context=context_without_disk(),
            client=FakeClient(turns=[["hi"]]),
            skills=[],
            tools=[],
            task_store=FakeTaskStore(),
        )
        task = AgentTask(goal="test")
        events = await collect_events(rt, task)
        self.assertIsInstance(events[-1], TaskCompleted)
        self.assertEqual(task.result, "hi")

    async def test_step_failure_raises(self):
        rt = AgentRuntime(
            context=context_without_disk(),
            client=FakeClient(turns=[[]]),
            skills=[],
            tools=[],
            task_store=FakeTaskStore(),
        )
        task = AgentTask(goal="test")
        with self.assertRaises(RuntimeError):
            await collect_events(rt, task)


if __name__ == "__main__":
    unittest.main()
