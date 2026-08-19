from __future__ import annotations

import json
import unittest

from sparkos.agent.llm_planner import LLMPlanner
from sparkos.agent.planner import PlanningContext, SkillCapability
from sparkos.agent.task import AgentTask
from sparkos.infrastructure.llm.models import ChatMessage


class FakePlanningModel:
    def __init__(self) -> None:
        self.requests: list[list[dict]] = []

    async def chat_once(self, messages: list[dict], *, json_object: bool = False) -> str:
        del json_object
        self.requests.append(messages)
        return '{"should_plan": false, "clarification_question": null, "steps": []}'


class CatalogPlanningTests(unittest.IsolatedAsyncioTestCase):
    async def test_planner_receives_cached_catalog_summary(self) -> None:
        model = FakePlanningModel()
        context = PlanningContext(
            session_id="session-1",
            summary="",
            recent_messages=(ChatMessage(role="user", content="统计每天 GMV"),),
            skills=(SkillCapability(name="spark-sql", description="Spark SQL"),),
            tool_names=("get_data_catalog", "run_spark_job"),
            catalog_summary={
                "default_database": "sparkmind_demo",
                "tables": ["fact_order", "fact_event"],
            },
        )

        await LLMPlanner(model).create_plan(AgentTask(goal="统计每天 GMV"), context)

        payload = json.loads(model.requests[0][1]["content"])
        self.assertEqual(payload["catalog_summary"]["default_database"], "sparkmind_demo")
        self.assertIn("fact_order", payload["catalog_summary"]["tables"])
