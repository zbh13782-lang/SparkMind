from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path

import pytest

from sparkos.infrastructure.catalog.models import DatasetRegistrationRequest, DataSourceInspectRequest
from sparkos.infrastructure.catalog.spark_backend import SparkCatalogBackend
from sparkos.infrastructure.spark.client import SparkJobRunner
from sparkos.infrastructure.spark.models import SparkJobRequest

pytestmark = pytest.mark.integration


def _require_integration() -> None:
    if os.environ.get("SPARKMIND_RUN_INTEGRATION") != "1":
        pytest.skip("set SPARKMIND_RUN_INTEGRATION=1 to run Docker Spark integration tests")


class CatalogDockerIntegrationTests(unittest.IsolatedAsyncioTestCase):
    async def test_discover_and_register_csv(self) -> None:
        _require_integration()
        repo_root = Path(__file__).resolve().parents[1]
        runner = SparkJobRunner()
        backend = SparkCatalogBackend(repo_root=repo_root, runner=runner)
        snapshot = await backend.discover()
        tables = {table.qualified_name: table for database in snapshot.databases for table in database.tables}
        self.assertIn("sparkmind_demo.fact_order", tables)
        self.assertIn(
            "dt", [column.name for column in tables["sparkmind_demo.fact_order"].columns if column.is_partition]
        )

        with tempfile.TemporaryDirectory(dir=repo_root / "data") as directory:
            source = Path(directory) / "sales.csv"
            source.write_text("dt,channel,amount\n2026-01-01,app,10\n2026-01-01,web,7\n", encoding="utf-8")
            inspection = await backend.inspect_source(
                DataSourceInspectRequest(
                    repo_root=repo_root,
                    path=source,
                    data_format="csv",
                    options={"header": "true", "inferSchema": "true"},
                    sample_rows=2,
                )
            )
            self.assertEqual(inspection["format"], "csv")
            table_name = "sales_integration_fixture"
            try:
                result = await backend.register_dataset(
                    DatasetRegistrationRequest(
                        repo_root=repo_root,
                        path=source,
                        data_format="csv",
                        database="sparkmind_demo",
                        table=table_name,
                        options={"header": "true", "inferSchema": "true"},
                        partition_columns=("dt",),
                    )
                )
                self.assertEqual(result["qualified_name"], f"sparkmind_demo.{table_name}")
                query = await runner.run(
                    SparkJobRequest(
                        job_name="catalog-integration-query",
                        job_type="spark_sql",
                        code=f"SELECT SUM(amount) AS total FROM sparkmind_demo.{table_name}",
                    )
                )
                self.assertEqual(query.status, "succeeded")
                self.assertIn("17", query.output)
            finally:
                await runner.run(
                    SparkJobRequest(
                        job_name="catalog-integration-cleanup",
                        job_type="spark_sql",
                        code=f"DROP TABLE IF EXISTS sparkmind_demo.{table_name}",
                    )
                )
