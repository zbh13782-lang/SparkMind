from __future__ import annotations

import asyncio
import unittest
from unittest.mock import AsyncMock, patch

from config.config import CatalogConfig
from config.health import CheckResult, check_spark_hive
from main import run_preflight


def _catalog_config() -> CatalogConfig:
    return CatalogConfig(
        enabled=True,
        default_database="sparkmind_demo",
        cache_path="artifacts/catalog/catalog.json",
        cache_ttl_seconds=300,
        semantic_path="config/semantic_catalog.yaml",
        max_tables_per_response=50,
        max_columns_per_response=200,
    )


class SparkHiveHealthTests(unittest.IsolatedAsyncioTestCase):
    async def test_check_spark_hive_refreshes_catalog_and_reports_tables(self) -> None:
        service = AsyncMock()
        service.get_catalog.return_value = {
            "status": "succeeded",
            "database": "sparkmind_demo",
            "tables": [{"name": "fact_order"}, {"name": "fact_event"}],
        }

        with patch("config.health.get_catalog_config", return_value=_catalog_config()):
            result = await check_spark_hive(service)

        self.assertEqual(result, CheckResult(ok=True, detail="Spark/Hive 正常 [sparkmind_demo]，发现 2 张表"))
        query = service.get_catalog.await_args.args[0]
        self.assertEqual(query.database, "sparkmind_demo")
        self.assertTrue(query.refresh)

    async def test_check_spark_hive_marks_empty_database_as_degraded(self) -> None:
        service = AsyncMock()
        service.get_catalog.return_value = {
            "status": "succeeded",
            "database": "sparkmind_demo",
            "tables": [],
        }

        with patch("config.health.get_catalog_config", return_value=_catalog_config()):
            result = await check_spark_hive(service)

        self.assertFalse(result.ok)
        self.assertTrue(result.degraded)
        self.assertIn("暂无表", result.detail)

    async def test_check_spark_hive_reports_catalog_failure_without_raising(self) -> None:
        service = AsyncMock()
        service.get_catalog.return_value = {
            "status": "unavailable",
            "default_database": "sparkmind_demo",
            "tables": [],
        }

        with patch("config.health.get_catalog_config", return_value=_catalog_config()):
            result = await check_spark_hive(service)

        self.assertFalse(result.ok)
        self.assertTrue(result.degraded)
        self.assertIn("unavailable", result.detail)

    async def test_check_spark_hive_times_out_without_blocking_startup(self) -> None:
        service = AsyncMock()

        async def hang(*args: object, **kwargs: object) -> dict:
            await asyncio.sleep(1)
            return {}

        service.get_catalog.side_effect = hang
        with (
            patch("config.health.get_catalog_config", return_value=_catalog_config()),
            patch("config.health._SPARK_HIVE_CHECK_TIMEOUT_SECONDS", 0.01),
        ):
            result = await check_spark_hive(service)

        self.assertFalse(result.ok)
        self.assertTrue(result.degraded)
        self.assertIn("超时", result.detail)


class PreflightTests(unittest.IsolatedAsyncioTestCase):
    async def test_preflight_checks_spark_hive_after_docker(self) -> None:
        with (
            patch("config.health.check_llm", new=AsyncMock(return_value=CheckResult(True, "ok"))),
            patch("config.health.check_docker", new=AsyncMock(return_value=CheckResult(True, "docker"))),
            patch(
                "config.health.check_spark_hive", new=AsyncMock(return_value=CheckResult(True, "spark"))
            ) as spark_check,
            patch(
                "config.health._check_advisor_with_fallback", new=AsyncMock(return_value=CheckResult(True, "advisor"))
            ),
            patch("config.config.get_chat_config", return_value=object()),
        ):
            result = await run_preflight()

        self.assertTrue(result.passed)
        self.assertEqual([row[0] for row in result.results], ["LLM", "Docker", "Spark/Hive", "Advisor"])
        spark_check.assert_awaited_once_with()

    async def test_preflight_skips_spark_hive_when_docker_is_down(self) -> None:
        with (
            patch("config.health.check_llm", new=AsyncMock(return_value=CheckResult(True, "ok"))),
            patch("config.health.check_docker", new=AsyncMock(return_value=CheckResult(False, "down", True))),
            patch("config.health.check_spark_hive", new=AsyncMock()) as spark_check,
            patch(
                "config.health._check_advisor_with_fallback", new=AsyncMock(return_value=CheckResult(True, "advisor"))
            ),
            patch("config.config.get_chat_config", return_value=object()),
        ):
            result = await run_preflight()

        self.assertTrue(result.passed)
        self.assertEqual(result.results[2][0], "Spark/Hive")
        self.assertFalse(result.results[2][1])
        self.assertTrue(result.results[2][3])
        spark_check.assert_not_awaited()


class PreflightProgressTests(unittest.IsolatedAsyncioTestCase):
    async def test_preflight_reports_each_real_stage_in_order(self) -> None:
        progress: list[tuple[str, str]] = []
        with (
            patch("config.health.check_llm", new=AsyncMock(return_value=CheckResult(True, "ok"))),
            patch("config.health.check_docker", new=AsyncMock(return_value=CheckResult(True, "docker"))),
            patch("config.health.check_spark_hive", new=AsyncMock(return_value=CheckResult(True, "spark"))),
            patch("config.health._check_advisor_with_fallback", new=AsyncMock(return_value=CheckResult(True, "advisor"))),
            patch("config.config.get_chat_config", return_value=object()),
        ):
            result = await run_preflight(lambda stage, detail: progress.append((stage, detail)))
        self.assertTrue(result.passed)
        self.assertEqual([stage for stage, _ in progress], ["llm", "docker", "spark-hive", "advisor"])
        self.assertTrue(all(detail for _, detail in progress))
