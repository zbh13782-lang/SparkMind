"""Run bounded Spark jobs in one-off Docker Compose client containers."""

from __future__ import annotations

import asyncio
import os
import re
import time
import uuid
from dataclasses import dataclass
from pathlib import Path

from sparkos.infrastructure.spark.models import SparkJobRequest, SparkJobResult

_APPLICATION_ID = re.compile(r"\b(?:app-[0-9]+-[0-9]+|application_[0-9]+_[0-9]+)\b")
_MAX_MODEL_LOG_BYTES = 20_000


@dataclass(frozen=True)
class SparkRunnerConfig:
    repo_root: Path
    compose_file: Path
    service: str = "spark-client"
    master_url: str = "spark://spark-master:7077"
    container_repo_root: str = "/opt/sparkos"
    spark_submit_bin: str = "/opt/spark/bin/spark-submit"

    @classmethod
    def from_env(cls) -> SparkRunnerConfig:
        repo_root = Path(
            os.environ.get(
                "SPARKOS_REPO_ROOT",
                Path(__file__).resolve().parents[3],
            )
        ).resolve()
        return cls(
            repo_root=repo_root,
            compose_file=repo_root / "docker-compose.yml",
            service=os.environ.get("SPARK_CLIENT_SERVICE", "spark-client"),
            master_url=os.environ.get(
                "SPARK_MASTER_URL",
                "spark://spark-master:7077",
            ),
        )


class SparkJobRunner:
    def __init__(self, config: SparkRunnerConfig | None = None) -> None:
        self.config = config or SparkRunnerConfig.from_env()

    async def run(self, request: SparkJobRequest) -> SparkJobResult:
        job_id = uuid.uuid4().hex
        container_name = f"sparkmind-job-{job_id[:12]}"
        job_dir = self.config.repo_root / "artifacts" / "spark-jobs" / job_id
        job_dir.mkdir(parents=True, exist_ok=False)
        script_path = self._write_job(job_dir, request)
        log_path = job_dir / "spark.log"
        command = self._build_command(
            request=request,
            script_path=script_path,
            container_name=container_name,
        )
        started = time.monotonic()
        exit_code: int | None = None
        status = "failed"

        env = os.environ.copy()
        env["SPARKOS_REPO_ROOT"] = str(self.config.repo_root)
        try:
            with log_path.open("wb") as log_file:
                process = await asyncio.create_subprocess_exec(
                    *command,
                    cwd=self.config.repo_root,
                    env=env,
                    stdout=log_file,
                    stderr=asyncio.subprocess.STDOUT,
                )
                try:
                    async with asyncio.timeout(request.timeout_seconds):
                        exit_code = await process.wait()
                    status = "succeeded" if exit_code == 0 else "failed"
                except TimeoutError:
                    await self._stop_container(container_name)
                    exit_code = await self._finish_process(process)
                    status = "timed_out"
                except asyncio.CancelledError:
                    await self._stop_container(container_name)
                    await self._finish_process(process)
                    raise
        except FileNotFoundError as exc:
            log_path.write_text(f"Docker 命令启动失败: {exc}\n", encoding="utf-8")
        except OSError as exc:
            log_path.write_text(f"Spark 作业启动失败: {exc}\n", encoding="utf-8")

        output = self._read_log_tail(log_path)
        return SparkJobResult(
            job_id=job_id,
            status=status,
            application_id=self._find_application_id(log_path),
            exit_code=exit_code,
            duration_seconds=round(time.monotonic() - started, 3),
            log_path=str(log_path.relative_to(self.config.repo_root)),
            output=output,
        )

    def _write_job(self, job_dir: Path, request: SparkJobRequest) -> Path:
        script_path = job_dir / "job.py"
        if request.job_type == "pyspark":
            script_path.write_text(request.code, encoding="utf-8")
            return script_path

        sql_path = job_dir / "query.sql"
        sql_path.write_text(request.code, encoding="utf-8")
        container_sql_path = self._container_path(sql_path)
        script_path.write_text(
            "\n".join(
                [
                    "from pathlib import Path",
                    "from pyspark.sql import SparkSession",
                    "",
                    f"spark = SparkSession.builder.appName({request.job_name!r}).getOrCreate()",
                    "try:",
                    f"    query = Path({container_sql_path!r}).read_text(encoding='utf-8').strip().rstrip(';')",
                    "    result = spark.sql(query)",
                    "    result.show(n=200, truncate=False)",
                    "finally:",
                    "    spark.stop()",
                    "",
                ]
            ),
            encoding="utf-8",
        )
        return script_path

    def _build_command(
        self,
        *,
        request: SparkJobRequest,
        script_path: Path,
        container_name: str,
    ) -> list[str]:
        max_cores = request.executor_cores * request.num_executors
        return [
            "docker",
            "compose",
            "-f",
            str(self.config.compose_file),
            "run",
            "--rm",
            "-T",
            "--name",
            container_name,
            self.config.service,
            self.config.spark_submit_bin,
            "--master",
            self.config.master_url,
            "--deploy-mode",
            "client",
            "--name",
            request.job_name,
            "--driver-memory",
            request.driver_memory,
            "--executor-memory",
            request.executor_memory,
            "--executor-cores",
            str(request.executor_cores),
            "--conf",
            f"spark.cores.max={max_cores}",
            "--conf",
            f"spark.driver.host={container_name}",
            "--conf",
            "spark.driver.bindAddress=0.0.0.0",
            self._container_path(script_path),
        ]

    async def _stop_container(self, container_name: str) -> None:
        for command in (
            ("docker", "stop", "--time", "10", container_name),
            ("docker", "rm", "-f", container_name),
        ):
            process = await asyncio.create_subprocess_exec(
                *command,
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL,
            )
            await process.wait()

    @staticmethod
    async def _finish_process(process: asyncio.subprocess.Process) -> int | None:
        if process.returncode is None:
            process.terminate()
        try:
            async with asyncio.timeout(5):
                return await process.wait()
        except TimeoutError:
            process.kill()
            return await process.wait()

    def _container_path(self, host_path: Path) -> str:
        relative_path = host_path.relative_to(self.config.repo_root).as_posix()
        return f"{self.config.container_repo_root}/{relative_path}"

    @staticmethod
    def _read_log_tail(path: Path) -> str:
        if not path.exists():
            return ""
        with path.open("rb") as file:
            file.seek(0, os.SEEK_END)
            size = file.tell()
            file.seek(max(0, size - _MAX_MODEL_LOG_BYTES))
            return file.read().decode("utf-8", errors="replace")

    @staticmethod
    def _find_application_id(path: Path) -> str:
        if not path.exists():
            return ""
        with path.open(encoding="utf-8", errors="replace") as file:
            for line in file:
                match = _APPLICATION_ID.search(line)
                if match:
                    return match.group(0)
        return ""
