from __future__ import annotations

import ast
import json
import re
import tempfile
import unittest
from pathlib import Path

from sparkos.infrastructure.catalog.models import DatasetRegistrationRequest, DataSourceInspectRequest
from sparkos.infrastructure.catalog.spark_backend import (
    CatalogDiscoveryError,
    SparkCatalogBackend,
)
from sparkos.infrastructure.spark.models import SparkJobRequest, SparkJobResult


def _result(status: str = "succeeded") -> SparkJobResult:
    return SparkJobResult(
        job_id="job-1",
        status=status,  # type: ignore[arg-type]
        application_id="app-1-1",
        exit_code=0 if status == "succeeded" else 1,
        duration_seconds=0.1,
        log_path="artifacts/jobs/job-1.log",
        output="done" if status == "succeeded" else "failed",
    )


class FakeRunner:
    def __init__(self, repo_root: Path, *, write_artifact: bool = True, status: str = "succeeded") -> None:
        self.repo_root = repo_root
        self.write_artifact = write_artifact
        self.status = status
        self.request: SparkJobRequest | None = None

    async def run(self, request: SparkJobRequest) -> SparkJobResult:
        self.request = request
        if self.write_artifact:
            match = re.search(r"output_path = (.+)", request.code)
            assert match is not None
            container_path = Path(ast.literal_eval(match.group(1)))
            host_path = self.repo_root / container_path.relative_to("/opt/sparkos")
            host_path.parent.mkdir(parents=True, exist_ok=True)
            host_path.write_text(
                json.dumps(
                    {
                        "generated_at": "2026-08-18T10:00:00+00:00",
                        "databases": [
                            {
                                "name": "sparkmind_demo",
                                "tables": [
                                    {
                                        "database": "sparkmind_demo",
                                        "name": "fact_order",
                                        "table_type": "MANAGED",
                                        "provider": "parquet",
                                        "location": "file:/warehouse/fact_order",
                                        "columns": [
                                            {
                                                "name": "order_id",
                                                "data_type": "string",
                                                "nullable": True,
                                                "is_partition": False,
                                                "description": "",
                                            }
                                        ],
                                    }
                                ],
                            }
                        ],
                    }
                ),
                encoding="utf-8",
            )
        return _result(self.status)


class CatalogBackendTests(unittest.IsolatedAsyncioTestCase):
    async def test_discover_runs_hive_metadata_job_without_count(self) -> None:
        tmp_path = Path(self.enterContext(tempfile.TemporaryDirectory()))
        runner = FakeRunner(tmp_path)
        backend = SparkCatalogBackend(repo_root=tmp_path, runner=runner)  # type: ignore[arg-type]

        snapshot = await backend.discover()

        assert snapshot.databases[0].tables[0].qualified_name == "sparkmind_demo.fact_order"
        assert runner.request is not None
        assert "spark.catalog.listDatabases()" in runner.request.code
        assert ".count(" not in runner.request.code.lower()
        assert runner.request.job_type == "pyspark"
        assert "from datetime import UTC" not in runner.request.code
        assert "timezone.utc" in runner.request.code

    async def test_discover_rejects_failed_spark_job(self) -> None:
        tmp_path = Path(self.enterContext(tempfile.TemporaryDirectory()))
        backend = SparkCatalogBackend(repo_root=tmp_path, runner=FakeRunner(tmp_path, status="failed"))  # type: ignore[arg-type]

        with self.assertRaisesRegex(CatalogDiscoveryError, "failed"):
            await backend.discover()

    async def test_discover_requires_artifact(self) -> None:
        tmp_path = Path(self.enterContext(tempfile.TemporaryDirectory()))
        backend = SparkCatalogBackend(repo_root=tmp_path, runner=FakeRunner(tmp_path, write_artifact=False))  # type: ignore[arg-type]

        with self.assertRaisesRegex(CatalogDiscoveryError, "产物"):
            await backend.discover()

    async def test_inspect_csv_returns_bounded_schema_and_samples(self) -> None:
        tmp_path = Path(self.enterContext(tempfile.TemporaryDirectory()))
        source = tmp_path / "data/sales.csv"
        source.parent.mkdir(parents=True)
        source.write_text("order_id,amount\nO1,12.5\nO2,3.0\n", encoding="utf-8")
        runner = OperationRunner(
            tmp_path,
            {
                "status": "succeeded",
                "format": "csv",
                "sample_rows": [{"order_id": "O1", "amount": 12.5}],
            },
        )
        backend = SparkCatalogBackend(repo_root=tmp_path, runner=runner)  # type: ignore[arg-type]

        result = await backend.inspect_source(
            DataSourceInspectRequest(
                repo_root=tmp_path,
                path=source,
                data_format="csv",
                options={"header": "true", "inferSchema": "true"},
                sample_rows=2,
            )
        )

        self.assertEqual(result["format"], "csv")
        self.assertEqual(result["sample_rows"], [{"order_id": "O1", "amount": 12.5}])
        assert runner.request is not None
        self.assertIn("df.schema.json()", runner.request.code)
        self.assertNotIn("df.count()", runner.request.code)

    async def test_register_dataset_writes_managed_parquet(self) -> None:
        tmp_path = Path(self.enterContext(tempfile.TemporaryDirectory()))
        source = tmp_path / "data/sales.parquet"
        source.parent.mkdir(parents=True)
        source.touch()
        runner = OperationRunner(
            tmp_path,
            {
                "status": "succeeded",
                "qualified_name": "analytics.sales",
                "storage_format": "parquet",
                "partition_columns": ["dt"],
            },
        )
        backend = SparkCatalogBackend(repo_root=tmp_path, runner=runner)  # type: ignore[arg-type]

        result = await backend.register_dataset(
            DatasetRegistrationRequest(
                repo_root=tmp_path,
                path=source,
                data_format="parquet",
                database="analytics",
                table="sales",
                partition_columns=("dt",),
            )
        )

        self.assertEqual(result["qualified_name"], "analytics.sales")
        assert runner.request is not None
        self.assertIn("saveAsTable", runner.request.code)
        self.assertIn("partitionBy", runner.request.code)


class OperationRunner:
    def __init__(self, repo_root: Path, payload: dict) -> None:
        self.repo_root = repo_root
        self.payload = payload
        self.request: SparkJobRequest | None = None

    async def run(self, request: SparkJobRequest) -> SparkJobResult:
        self.request = request
        match = re.search(r"output_path = (.+)", request.code)
        assert match is not None
        container_path = Path(ast.literal_eval(match.group(1)))
        host_path = self.repo_root / container_path.relative_to("/opt/sparkos")
        host_path.parent.mkdir(parents=True, exist_ok=True)
        host_path.write_text(json.dumps(self.payload), encoding="utf-8")
        return _result(self.payload.get("status", "succeeded"))
