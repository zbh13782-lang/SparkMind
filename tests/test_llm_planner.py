from __future__ import annotations

import json
import unittest

from sparkos.agent.llm_planner import LLMPlanner
from sparkos.agent.planner import (
    ClarificationRequest,
    Plan,
    PlanningContext,
    PlanStep,
    SkillCapability,
)
from sparkos.agent.scheduler import create_step_runs
from sparkos.agent.step import StepResult
from sparkos.agent.task import AgentTask
from sparkos.infrastructure.llm.models import ChatMessage


class FakePlanningModel:
    def __init__(self, response: str) -> None:
        self.response = response
        self.requests: list[list[dict]] = []

    async def chat_once(self, messages: list[dict]) -> str:
        self.requests.append(messages)
        return self.response


def planning_context() -> PlanningContext:
    return PlanningContext(
        session_id="session-1",
        summary="Earlier the user selected sales.csv.",
        recent_messages=(ChatMessage(role="user", content="analyze it"),),
        skills=(
            SkillCapability(
                name="spark-sql",
                description="Generate and optimize Spark SQL",
            ),
        ),
        tool_names=("read_file", "shell"),
    )


class LLMPlannerTests(unittest.IsolatedAsyncioTestCase):
    async def test_complex_task_returns_dependency_aware_plan(self) -> None:
        model = FakePlanningModel(
            json.dumps(
                {
                    "should_plan": True,
                    "steps": [
                        {
                            "id": "s1",
                            "description": "Inspect the input data",
                            "depends_on": [],
                            "success_criteria": "Input schema is known",
                        },
                        {
                            "id": "s2",
                            "description": "Write the findings",
                            "depends_on": ["s1"],
                            "success_criteria": "Findings are summarized",
                        },
                    ],
                }
            )
        )
        task = AgentTask(goal="Analyze the data and produce a report")

        plan = await LLMPlanner(model).create_plan(task, planning_context())

        self.assertIsNotNone(plan)
        assert plan is not None
        self.assertEqual(plan.task_id, task.id)
        self.assertEqual([step.id for step in plan.steps], ["s1", "s2"])
        self.assertEqual(plan.steps[1].depends_on, ("s1",))
        self.assertEqual(plan.steps[1].success_criteria, "Findings are summarized")

    async def test_simple_task_returns_none(self) -> None:
        model = FakePlanningModel('{"should_plan": false, "steps": []}')

        plan = await LLMPlanner(model).create_plan(
            AgentTask(goal="Say hello"), planning_context()
        )

        self.assertIsNone(plan)

    async def test_unclear_task_returns_clarification_request(self) -> None:
        model = FakePlanningModel(
            json.dumps(
                {
                    "should_plan": False,
                    "clarification_question": "  请提供要分析的文件路径。  ",
                    "steps": [],
                }
            )
        )

        decision = await LLMPlanner(model).create_plan(
            AgentTask(goal="帮我分析一下"), planning_context()
        )

        self.assertEqual(
            decision,
            ClarificationRequest(question="请提供要分析的文件路径。"),
        )

    async def test_blank_clarification_falls_back_to_direct_execution(self) -> None:
        model = FakePlanningModel(
            '{"should_plan":false,"clarification_question":"  ","steps":[]}'
        )

        decision = await LLMPlanner(model).create_plan(
            AgentTask(goal="帮我处理"), planning_context()
        )

        self.assertIsNone(decision)

    async def test_markdown_fenced_json_is_accepted(self) -> None:
        model = FakePlanningModel(
            """```json
{"should_plan": true, "steps": [{"id": "s1", "description": "Inspect", "depends_on": []}]}
```"""
        )

        plan = await LLMPlanner(model).create_plan(
            AgentTask(goal="Inspect the project"), planning_context()
        )

        self.assertIsNotNone(plan)
        assert plan is not None
        self.assertEqual(plan.steps[0].description, "Inspect")

    async def test_malformed_json_falls_back_to_direct_execution(self) -> None:
        model = FakePlanningModel("not json")

        plan = await LLMPlanner(model).create_plan(
            AgentTask(goal="Do work"), planning_context()
        )

        self.assertIsNone(plan)

    async def test_unserializable_task_input_falls_back_to_direct_execution(
        self,
    ) -> None:
        model = FakePlanningModel('{"should_plan": false, "steps": []}')
        task = AgentTask(goal="Do work", input={"value": object()})

        plan = await LLMPlanner(model).create_plan(task, planning_context())

        self.assertIsNone(plan)
        self.assertEqual(model.requests, [])

    async def test_unknown_dependency_rejects_plan(self) -> None:
        model = FakePlanningModel(
            '{"should_plan":true,"steps":['
            '{"id":"s1","description":"Inspect","depends_on":["missing"]}]}'
        )

        plan = await LLMPlanner(model).create_plan(
            AgentTask(goal="Do work"), planning_context()
        )

        self.assertIsNone(plan)

    async def test_dependency_cycle_rejects_plan(self) -> None:
        model = FakePlanningModel(
            '{"should_plan":true,"steps":['
            '{"id":"s1","description":"One","depends_on":["s2"]},'
            '{"id":"s2","description":"Two","depends_on":["s1"]}]}'
        )

        plan = await LLMPlanner(model).create_plan(
            AgentTask(goal="Do work"), planning_context()
        )

        self.assertIsNone(plan)

    async def test_duplicate_step_id_rejects_plan(self) -> None:
        model = FakePlanningModel(
            '{"should_plan":true,"steps":['
            '{"id":"s1","description":"One","depends_on":[]},'
            '{"id":"s1","description":"Two","depends_on":[]}]}'
        )

        plan = await LLMPlanner(model).create_plan(
            AgentTask(goal="Do work"), planning_context()
        )

        self.assertIsNone(plan)

    async def test_self_dependency_rejects_plan(self) -> None:
        model = FakePlanningModel(
            '{"should_plan":true,"steps":['
            '{"id":"s1","description":"One","depends_on":["s1"]}]}'
        )

        plan = await LLMPlanner(model).create_plan(
            AgentTask(goal="Do work"), planning_context()
        )

        self.assertIsNone(plan)

    async def test_plan_over_configured_step_limit_is_rejected(self) -> None:
        steps = [
            {"id": f"s{index}", "description": str(index), "depends_on": []}
            for index in range(3)
        ]
        model = FakePlanningModel(json.dumps({"should_plan": True, "steps": steps}))

        plan = await LLMPlanner(model, max_steps=2).create_plan(
            AgentTask(goal="Do work"), planning_context()
        )

        self.assertIsNone(plan)

    async def test_prompt_uses_configured_step_limit(self) -> None:
        model = FakePlanningModel('{"should_plan": false, "steps": []}')

        await LLMPlanner(model, max_steps=2).create_plan(
            AgentTask(goal="Do work"), planning_context()
        )

        self.assertIn("最多 2 步", model.requests[0][0]["content"])

    async def test_empty_step_description_rejects_plan(self) -> None:
        model = FakePlanningModel(
            '{"should_plan":true,"steps":['
            '{"id":"s1","description":"  ","depends_on":[]}]}'
        )

        plan = await LLMPlanner(model).create_plan(
            AgentTask(goal="Do work"), planning_context()
        )

        self.assertIsNone(plan)

    async def test_planning_request_contains_task_context_and_capabilities(
        self,
    ) -> None:
        model = FakePlanningModel('{"should_plan": false, "steps": []}')
        task = AgentTask(goal="Analyze sales", input={"format": "csv"})

        await LLMPlanner(model).create_plan(task, planning_context())

        self.assertEqual(len(model.requests), 1)
        payload = json.loads(model.requests[0][1]["content"])
        self.assertEqual(payload["goal"], "Analyze sales")
        self.assertEqual(payload["input"], {"format": "csv"})
        self.assertIn("sales.csv", payload["summary"])
        self.assertEqual(
            payload["skills"],
            [
                {
                    "name": "spark-sql",
                    "description": "Generate and optimize Spark SQL",
                }
            ],
        )
        self.assertEqual(payload["tools"], ["read_file", "shell"])
        self.assertEqual(payload["recent_messages"][0]["content"], "analyze it")

    async def test_revise_plan_increments_version_and_includes_failure_state(
        self,
    ) -> None:
        model = FakePlanningModel(
            json.dumps(
                {
                    "should_plan": True,
                    "steps": [
                        {
                            "id": "s1",
                            "description": "Inspect input",
                            "depends_on": [],
                            "success_criteria": "Input inspected",
                        },
                        {
                            "id": "s3",
                            "description": "Use fallback source",
                            "depends_on": ["s1"],
                            "success_criteria": "Fallback report complete",
                        },
                    ],
                }
            )
        )
        task = AgentTask(goal="Analyze")
        failed_step = PlanStep(
            id="s2",
            description="Use primary source",
            depends_on=("s1",),
            success_criteria="Report complete",
        )
        current_plan = Plan(
            id="plan-1",
            task_id=task.id,
            version=1,
            steps=(
                PlanStep(
                    id="s1",
                    description="Inspect input",
                    success_criteria="Input inspected",
                ),
                failed_step,
            ),
        )
        runs = create_step_runs(current_plan)
        runs["s1"].start()
        runs["s1"].succeed(StepResult(True, "schema loaded"))
        runs["s2"].start()
        runs["s2"].fail("primary source incomplete")
        runs["s2"].start()
        runs["s2"].fail("primary source blocked")

        revised = await LLMPlanner(model).revise_plan(
            task=task,
            context=planning_context(),
            current_plan=current_plan,
            step_runs=runs,
            failed_step=failed_step,
            reason="primary source blocked",
        )

        self.assertIsNotNone(revised)
        assert revised is not None
        self.assertEqual(revised.version, 2)
        self.assertEqual(revised.source, "replan")
        self.assertEqual([step.id for step in revised.steps], ["s1", "s3"])
        payload = json.loads(model.requests[0][1]["content"])
        self.assertEqual(payload["failed_step"]["id"], "s2")
        self.assertEqual(payload["failure_reason"], "primary source blocked")
        self.assertEqual(
            payload["step_runs"]["s1"]["result"]["output"],
            "schema loaded",
        )
        self.assertEqual(
            payload["step_runs"]["s2"]["error"],
            "primary source blocked",
        )


if __name__ == "__main__":
    unittest.main()
