from __future__ import annotations

import inspect
import json
import unittest
from unittest.mock import AsyncMock, patch

from sparkos.agent.tools.registry import TOOL_DEFINITIONS, execute_tool
from sparkos.infrastructure.spark.models import SparkJobResult


class TestSparkToolTests(unittest.IsolatedAsyncioTestCase):
    def test_registry_exposes_one_spark_tool(self) -> None:
        spark_tools = [
            item["function"]
            for item in TOOL_DEFINITIONS
            if "spark" in item["function"]["name"]
        ]

        assert [tool["name"] for tool in spark_tools] == ["run_spark_job"]
        assert spark_tools[0]["parameters"]["properties"]["job_type"]["enum"] == [
            "spark_sql",
            "pyspark",
        ]

    async def test_dispatch_builds_request_and_returns_json(self) -> None:
        expected = SparkJobResult(
            job_id="job-1",
            status="succeeded",
            application_id="app-1-1",
            exit_code=0,
            duration_seconds=1.0,
            log_path="artifacts/spark-jobs/job-1/spark.log",
            output="ok",
        )

        with patch(
            "sparkos.agent.tools.registry._SPARK_RUNNER.run",
            new=AsyncMock(return_value=expected),
        ) as run:
            pending = execute_tool(
                "run_spark_job",
                {
                    "job_name": "hello",
                    "job_type": "pyspark",
                    "code": "print('hello')",
                    "executor_memory": "2g",
                },
            )
            assert inspect.isawaitable(pending)
            result = await pending

        request = run.await_args.args[0]
        assert request.job_name == "hello"
        assert request.executor_memory == "2g"
        assert json.loads(result)["status"] == "succeeded"
