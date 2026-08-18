from __future__ import annotations

import asyncio
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from sparkos.infrastructure.spark.client import SparkJobRunner, SparkRunnerConfig
from sparkos.infrastructure.spark.models import SparkJobRequest, SparkJobResult

# ── model tests ──────────────────────────────────────────────────────────────


class TestSparkJobModel(unittest.TestCase):
    def test_request_rejects_invalid_resource_values(self) -> None:
        with pytest.raises(ValueError, match="executor_memory"):
            SparkJobRequest(
                job_name="bad-memory",
                job_type="pyspark",
                code="print('x')",
                executor_memory="lots",
            )

        with pytest.raises(ValueError, match="executor_cores"):
            SparkJobRequest(
                job_name="bad-cores",
                job_type="pyspark",
                code="print('x')",
                executor_cores=0,
            )

    def test_result_serializes_as_stable_json(self) -> None:
        result = SparkJobResult(
            job_id="job-1",
            status="succeeded",
            application_id="app-1-1",
            exit_code=0,
            duration_seconds=1.25,
            log_path="artifacts/spark-jobs/job-1/spark.log",
            output="done",
        )

        assert json.loads(result.to_json()) == {
            "job_id": "job-1",
            "status": "succeeded",
            "application_id": "app-1-1",
            "exit_code": 0,
            "duration_seconds": 1.25,
            "log_path": "artifacts/spark-jobs/job-1/spark.log",
            "output": "done",
        }


# ── runner tests ─────────────────────────────────────────────────────────────


class FakeProcess:
    def __init__(self, returncode: int | None = 0) -> None:
        self.returncode = returncode

    async def wait(self) -> int:
        assert self.returncode is not None
        return self.returncode

    def terminate(self) -> None:
        self.returncode = -15

    def kill(self) -> None:
        self.returncode = -9


class TestSparkJobRunner(unittest.IsolatedAsyncioTestCase):
    async def test_pyspark_job_uses_one_off_client_without_shell(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            repo_root = Path(directory)
            compose_file = repo_root / "docker-compose.yml"
            compose_file.write_text("services: {}", encoding="utf-8")
            runner = SparkJobRunner(SparkRunnerConfig(repo_root=repo_root, compose_file=compose_file))
            process = FakeProcess()

            with patch(
                "sparkos.infrastructure.spark.client.asyncio.create_subprocess_exec",
                new=AsyncMock(return_value=process),
            ) as create_process:
                result = await runner.run(
                    SparkJobRequest(
                        job_name="hello",
                        job_type="pyspark",
                        code="print('hello')",
                    )
                )

            command = create_process.await_args.args
            assert command[:4] == ("docker", "compose", "-f", str(compose_file))
            assert "run" in command
            assert "--rm" in command
            assert "-T" in command
            assert "spark-client" in command
            assert "spark://spark-master:7077" in command
            assert "spark.driver.bindAddress=0.0.0.0" in command
            assert any(a.startswith("spark.driver.host=sparkmind-job-") for a in command)
            assert "sh" not in command
            assert "-c" not in command
            assert result.status == "succeeded"

    async def test_sql_job_generates_bounded_result_wrapper(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            repo_root = Path(directory)
            compose_file = repo_root / "docker-compose.yml"
            compose_file.write_text("services: {}", encoding="utf-8")
            runner = SparkJobRunner(SparkRunnerConfig(repo_root=repo_root, compose_file=compose_file))

            with patch(
                "sparkos.infrastructure.spark.client.asyncio.create_subprocess_exec",
                new=AsyncMock(return_value=FakeProcess()),
            ):
                result = await runner.run(
                    SparkJobRequest(
                        job_name="sql",
                        job_type="spark_sql",
                        code="select 1 as value",
                    )
                )

            job_dir = repo_root / "artifacts" / "spark-jobs" / result.job_id
            assert (job_dir / "query.sql").read_text(encoding="utf-8") == "select 1 as value"
            wrapper = (job_dir / "job.py").read_text(encoding="utf-8")
            assert "result.show(n=200, truncate=False)" in wrapper
            assert ".enableHiveSupport()" in wrapper

    async def test_job_uses_repo_persisted_hive_catalog(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            repo_root = Path(directory)
            compose_file = repo_root / "docker-compose.yml"
            compose_file.write_text("services: {}", encoding="utf-8")
            runner = SparkJobRunner(SparkRunnerConfig(repo_root=repo_root, compose_file=compose_file))

            with patch(
                "sparkos.infrastructure.spark.client.asyncio.create_subprocess_exec",
                new=AsyncMock(return_value=FakeProcess()),
            ) as create_process:
                await runner.run(
                    SparkJobRequest(
                        job_name="hive-catalog",
                        job_type="spark_sql",
                        code="show tables in sparkmind_demo",
                    )
                )

            command = create_process.await_args.args
            assert "spark.sql.catalogImplementation=hive" in command
            assert "spark.sql.warehouse.dir=file:/opt/sparkos/data/hive/warehouse" in command
            assert any(
                arg.startswith("spark.hadoop.javax.jdo.option.ConnectionURL=jdbc:derby:")
                and "/opt/sparkos/data/hive/metastore_db" in arg
                for arg in command
            )

    async def test_result_keeps_application_id_but_only_returns_log_tail(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            repo_root = Path(directory)
            compose_file = repo_root / "docker-compose.yml"
            compose_file.write_text("services: {}", encoding="utf-8")
            runner = SparkJobRunner(SparkRunnerConfig(repo_root=repo_root, compose_file=compose_file))

            async def launch(*args: object, **kwargs: object) -> FakeProcess:
                log_fd = kwargs["stdout"]
                os.write(log_fd, b"Connected with app ID app-1722920000000-0001\n")
                os.write(log_fd, b"x" * 25_000)
                os.write(log_fd, b"\nfinal-result\n")
                os.fsync(log_fd)
                return FakeProcess()

            with patch(
                "sparkos.infrastructure.spark.client.asyncio.create_subprocess_exec",
                new=AsyncMock(side_effect=launch),
            ):
                result = await runner.run(
                    SparkJobRequest(
                        job_name="bounded-log",
                        job_type="pyspark",
                        code="print('result')",
                    )
                )

            assert result.application_id == "app-1722920000000-0001"
            assert len(result.output.encode("utf-8")) <= 20_000
            assert "final-result" in result.output

    async def test_timeout_stops_the_one_off_container(self) -> None:
        class HangingProcess(FakeProcess):
            def __init__(self) -> None:
                super().__init__(None)
                self.finished = asyncio.Event()

            async def wait(self) -> int:
                await self.finished.wait()
                assert self.returncode is not None
                return self.returncode

            def terminate(self) -> None:
                super().terminate()
                self.finished.set()

        with tempfile.TemporaryDirectory() as directory:
            repo_root = Path(directory)
            compose_file = repo_root / "docker-compose.yml"
            compose_file.write_text("services: {}", encoding="utf-8")
            runner = SparkJobRunner(SparkRunnerConfig(repo_root=repo_root, compose_file=compose_file))
            processes = [HangingProcess(), FakeProcess(), FakeProcess()]

            with patch(
                "sparkos.infrastructure.spark.client.asyncio.create_subprocess_exec",
                new=AsyncMock(side_effect=processes),
            ) as create_process:
                result = await runner.run(
                    SparkJobRequest(
                        job_name="timeout",
                        job_type="pyspark",
                        code="print('slow')",
                        timeout_seconds=1,
                    )
                )

            cleanup_commands = [call.args for call in create_process.await_args_list[1:]]
            assert any(command[:2] == ("docker", "stop") for command in cleanup_commands)
            assert any(command[:3] == ("docker", "rm", "-f") for command in cleanup_commands)
            assert result.status == "timed_out"
