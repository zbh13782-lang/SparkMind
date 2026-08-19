from __future__ import annotations

import asyncio
import unittest
from pathlib import Path
from unittest.mock import Mock

from sparkos.agent.context import AgentContext
from sparkos.agent.events import (
    SkillActivated,
    TaskCompleted,
)
from sparkos.agent.planner import ClarificationRequest, Plan, PlanStep
from sparkos.agent.runtime import AgentRuntime
from sparkos.agent.skills.loader import Skill
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


class FakeCatalogService:
    def cached_summary(self):
        return {
            "default_database": "sparkmind_demo",
            "tables": ["fact_order"],
            "stale": False,
        }


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
            client=FakeClient(turns=[["hi"], ["done"], ["summary"]]),
            skills=[],
            tools=[],
            task_store=FakeTaskStore(),
        )
        task = AgentTask(goal="test")
        events = await collect_events(rt, task)
        self.assertIsInstance(events[-1], TaskCompleted)
        self.assertEqual(task.result, "hi")

    async def test_query_and_quality_test_auto_activates_quality_skill(self):
        client = FakeClient(turns=[["quality report"]])
        rt = AgentRuntime(
            context=context_without_disk(),
            client=client,
            skills=[
                Skill(
                    name="data-quality-test",
                    description="query plus quality test",
                    path=Path("sparkos/agent/skills/data-quality-test/SKILL.md"),
                )
            ],
            tools=[],
            task_store=FakeTaskStore(),
            catalog_service=FakeCatalogService(),
        )

        events = await collect_events(rt, AgentTask(goal="查询订单并做质量测试"))

        system_text = "\n".join(
            message["content"]
            for message in client.requests[0]
            if message["role"] == "system"
        )
        self.assertIn("当前步骤激活技能：data-quality-test", system_text)
        activated = [event for event in events if isinstance(event, SkillActivated)]
        self.assertEqual([event.skill.name for event in activated], ["data-quality-test"])

    async def test_plain_query_does_not_auto_activate_quality_skill(self):
        client = FakeClient(turns=[["query result"]])
        rt = AgentRuntime(
            context=context_without_disk(),
            client=client,
            skills=[
                Skill(
                    name="data-quality-test",
                    description="query plus quality test",
                    path=Path("sparkos/agent/skills/data-quality-test/SKILL.md"),
                )
            ],
            tools=[],
            task_store=FakeTaskStore(),
            catalog_service=FakeCatalogService(),
        )

        events = await collect_events(rt, AgentTask(goal="查询订单"))

        system_text = "\n".join(
            message["content"]
            for message in client.requests[0]
            if message["role"] == "system"
        )
        self.assertNotIn("当前激活技能：data-quality-test", system_text)
        self.assertFalse(any(isinstance(event, SkillActivated) for event in events))

    async def test_planner_skills_are_loaded_only_for_their_steps(self):
        client = FakeClient(turns=[["query result"], ["quality result"], ["final"]])
        plan = Plan(
            id="plan-skills",
            task_id="placeholder",
            steps=(
                PlanStep(
                    id="s1",
                    description="查询订单",
                    skills=("spark-sql",),
                    success_criteria="查询完成",
                ),
                PlanStep(
                    id="s2",
                    description="检查查询结果质量",
                    depends_on=("s1",),
                    skills=("data-quality-test",),
                    success_criteria="质量报告完成",
                ),
            ),
        )
        rt = AgentRuntime(
            context=context_without_disk(),
            client=client,
            skills=[
                Skill(
                    name="spark-sql",
                    description="query",
                    path=Path("sparkos/agent/skills/spark-sql/SKILL.md"),
                ),
                Skill(
                    name="data-quality-test",
                    description="quality",
                    path=Path("sparkos/agent/skills/data-quality-test/SKILL.md"),
                ),
            ],
            tools=[],
            task_store=FakeTaskStore(),
            planner=FakePlanner(plan),
            catalog_service=FakeCatalogService(),
        )

        events = await collect_events(rt, AgentTask(goal="查询订单并检查质量"))

        first_system = "\n".join(
            message["content"] for message in client.requests[0] if message["role"] == "system"
        )
        second_system = "\n".join(
            message["content"] for message in client.requests[1] if message["role"] == "system"
        )
        self.assertIn("当前步骤激活技能：spark-sql", first_system)
        self.assertNotIn("当前步骤激活技能：data-quality-test", first_system)
        self.assertIn("当前步骤激活技能：data-quality-test", second_system)
        activated = [
            (event.skill.name, event.step.id if event.step else None, event.source)
            for event in events
            if isinstance(event, SkillActivated)
        ]
        self.assertIn(("spark-sql", "s1", "planner"), activated)
        self.assertIn(("data-quality-test", "s2", "planner"), activated)

    async def test_rule_skill_is_merged_into_planned_quality_step(self):
        client = FakeClient(turns=[["query result"], ["quality result"], ["final"]])
        plan = Plan(
            id="plan-rule-merge",
            task_id="placeholder",
            steps=(
                PlanStep(id="s1", description="查询订单", success_criteria="查询完成"),
                PlanStep(
                    id="s2",
                    description="执行质量检查",
                    depends_on=("s1",),
                    success_criteria="质量报告完成",
                ),
            ),
        )
        rt = AgentRuntime(
            context=context_without_disk(),
            client=client,
            skills=[
                Skill(
                    name="data-quality-test",
                    description="quality",
                    path=Path("sparkos/agent/skills/data-quality-test/SKILL.md"),
                )
            ],
            tools=[],
            task_store=FakeTaskStore(),
            planner=FakePlanner(plan),
            catalog_service=FakeCatalogService(),
        )

        events = await collect_events(rt, AgentTask(goal="查询订单并检查质量"))

        first_system = "\n".join(
            message["content"] for message in client.requests[0] if message["role"] == "system"
        )
        second_system = "\n".join(
            message["content"] for message in client.requests[1] if message["role"] == "system"
        )
        self.assertNotIn("当前步骤激活技能：data-quality-test", first_system)
        self.assertIn("当前步骤激活技能：data-quality-test", second_system)
        rule_events = [
            event
            for event in events
            if isinstance(event, SkillActivated) and event.source == "rule"
        ]
        self.assertEqual([(event.skill.name, event.step.id) for event in rule_events], [("data-quality-test", "s2")])

    async def test_corrupted_final_summary_falls_back_to_verified_step_result(self):
        corrupted = "分析结果 confirmed 真的 SELECT OVERVIEW actress; остаток данные 乱码"
        client = FakeClient(turns=[["查询结果"], ["已核验的质量结果"], [corrupted]])
        plan = Plan(
            id="plan-corrupt-final",
            task_id="placeholder",
            steps=(
                PlanStep(id="s1", description="查询数据", success_criteria="查询完成"),
                PlanStep(
                    id="s2",
                    description="汇总结果",
                    depends_on=("s1",),
                    success_criteria="结果完成",
                ),
            ),
        )
        rt = AgentRuntime(
            context=context_without_disk(),
            client=client,
            skills=[],
            tools=[],
            task_store=FakeTaskStore(),
            planner=FakePlanner(plan),
            catalog_service=FakeCatalogService(),
        )

        events = await collect_events(rt, AgentTask(goal="查询并汇总结果"))

        self.assertEqual(rt.context.history[-1].content, "已核验的质量结果")
        self.assertEqual(task_result := events[-1].task.result, "已核验的质量结果")
        self.assertNotIn("остаток", task_result)

    async def test_later_steps_do_not_receive_raw_tool_history_from_current_task(self):
        client = FakeClient(turns=[["first result"], ["second result"], ["final"]])
        plan = Plan(
            id="plan-clean-step-context",
            task_id="placeholder",
            steps=(
                PlanStep(id="s1", description="查询", success_criteria="done"),
                PlanStep(id="s2", description="分析", depends_on=("s1",), success_criteria="done"),
            ),
        )
        context = context_without_disk()
        context.record_user("历史问题")
        context.record_tool("old-call", "历史工具结果")
        rt = AgentRuntime(
            context=context,
            client=client,
            skills=[],
            tools=[],
            task_store=FakeTaskStore(),
            planner=FakePlanner(plan),
            catalog_service=FakeCatalogService(),
        )

        await collect_events(rt, AgentTask(goal="新任务"))

        second_request = client.requests[1]
        request_text = "\n".join(message.get("content", "") for message in second_request)
        self.assertNotIn("历史工具结果", request_text)
        self.assertIn("first result", request_text)

    async def test_planner_context_contains_catalog_summary(self):
        planner = FakePlanner(two_step_plan())
        rt = AgentRuntime(
            context=context_without_disk(),
            client=FakeClient(turns=[["hi"], ["done"], ["summary"]]),
            skills=[],
            tools=[],
            task_store=FakeTaskStore(),
            planner=planner,
            catalog_service=FakeCatalogService(),
        )
        task = AgentTask(goal="统计每天 GMV")

        await collect_events(rt, task)

        self.assertEqual(planner.calls[0][1].catalog_summary["default_database"], "sparkmind_demo")

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
