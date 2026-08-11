"""Tests for Agent tool registry definitions and dispatch."""

from __future__ import annotations

import inspect
import json
import unittest
from unittest.mock import AsyncMock, patch

from sparkos.agent.tools.registry import TOOL_DEFINITIONS, execute_tool
from sparkos.infrastructure.advisor.models import AdvisorResult
from sparkos.infrastructure.code_sandbox.models import CodeRunResult


class AgentToolRegistryTests(unittest.IsolatedAsyncioTestCase):
    def test_registry_exposes_run_code_and_not_shell(self) -> None:
        functions = {item["function"]["name"]: item["function"] for item in TOOL_DEFINITIONS}
        self.assertIn("run_code", functions)
        self.assertNotIn("shell", functions)
        parameters = functions["run_code"]["parameters"]
        self.assertFalse(parameters["additionalProperties"])
        self.assertEqual(parameters["properties"]["language"]["enum"], ["python", "bash"])
        self.assertEqual(parameters["required"], ["language", "code"])

    async def test_run_code_dispatches_validated_request(self) -> None:
        expected = CodeRunResult(
            run_id="run-1",
            status="succeeded",
            exit_code=0,
            duration_seconds=0.1,
            log_path="artifacts/code-runs/run-1/output.log",
            output="ok\n",
            output_truncated=False,
        )
        with patch(
            "sparkos.agent.tools.registry._CODE_RUNNER.run",
            new_callable=AsyncMock,
            return_value=expected,
        ) as run:
            pending = execute_tool(
                "run_code",
                {"language": "python", "code": "print('ok')"},
            )
            self.assertTrue(inspect.isawaitable(pending))
            result = await pending

        self.assertEqual(run.call_args.args[0].language, "python")
        self.assertEqual(json.loads(result)["output"], "ok\n")

    def test_registry_exposes_advisor_contract(self) -> None:
        functions = {item["function"]["name"]: item["function"] for item in TOOL_DEFINITIONS}
        advisor = functions["ask_advisor"]
        self.assertFalse(advisor["parameters"]["additionalProperties"])
        self.assertEqual(
            advisor["parameters"]["required"],
            ["question", "context", "attempts"],
        )

    async def test_advisor_dispatch_returns_json(self) -> None:
        expected = AdvisorResult(
            status="succeeded",
            model="advisor",
            answer="Use full jitter.",
            duration_seconds=0.2,
        )
        fake = AsyncMock()
        fake.ask.return_value = expected
        with patch(
            "sparkos.agent.tools.registry._get_advisor",
            return_value=fake,
        ):
            result = await execute_tool(
                "ask_advisor",
                {
                    "question": "How should retries change?",
                    "context": "Intermittent 503 responses.",
                    "attempts": "Fixed delay.",
                },
            )

        self.assertEqual(fake.ask.call_args.args[0].question, "How should retries change?")
        self.assertEqual(json.loads(result)["answer"], "Use full jitter.")
