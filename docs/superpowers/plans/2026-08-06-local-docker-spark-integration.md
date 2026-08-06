# Local Docker Spark Integration Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let the local SparkMind Agent execute one Spark SQL or PySpark job against the local Docker Compose Spark cluster through a single `run_spark_job` tool and receive a bounded, structured result.

**Architecture:** The Agent remains a host Python process. Each tool call writes an isolated job directory under `artifacts/spark-jobs/`, launches a one-off `spark-client` container with `docker compose run --rm`, and runs the Driver there against `spark://spark-master:7077`. The tool waits for completion, streams container output to a local log, returns only the log tail to the model, and stops the one-off container on timeout or task cancellation.

**Tech Stack:** Python 3.14, asyncio subprocesses, Docker Compose, Apache Spark 3.5.3, OpenAI function tools, unittest/pytest, Ruff.

## Global Constraints

- Keep the Agent on the host and Spark inside the existing local Docker Compose project.
- Expose exactly one Agent-facing Spark tool in this phase: `run_spark_job`.
- Do not add an HTTP service, Spark Connect, Livy, Hive integration, or a host-side PySpark dependency.
- Continue from the current worktree edits in `docker-compose.yml` and `sparkos/agent/tools/registry.py`; do not restore the deleted Docker-exec implementation from Git.
- Reuse `sparkos/infrastructure/spark/client.py` as the new one-off job runner path, but do not run the Driver in `spark-master`.
- Never construct the Docker or Spark command with `shell=True`; every argument is a separate subprocess argument.
- Keep complete logs under the already-ignored `artifacts/` directory and cap the model-visible output at 20,000 bytes.
- Run Ruff check and format after every Python task.

---

## File Structure

- Create `docker/spark-defaults.conf`: shared Spark event-log and local SQL defaults.
- Modify `docker-compose.yml`: add Master readiness and the one-off `spark-client` service.
- Create `sparkos/infrastructure/spark/__init__.py`: export the Spark runner types.
- Create `sparkos/infrastructure/spark/models.py`: validated job request and structured result.
- Create `sparkos/infrastructure/spark/client.py`: artifact creation, command construction, execution, cleanup, and bounded log reading.
- Modify `sparkos/agent/tools/registry.py`: declare and dispatch the single Spark tool.
- Modify `sparkos/agent/skills/spark-job/SKILL.md`: align the skill with synchronous one-call execution.
- Modify `README.md`: document startup, UI URLs, and the local execution path.
- Create `tests/test_spark_client.py`: runner behavior and command safety.
- Create `tests/test_spark_tools.py`: tool schema and dispatch contract.

---

### Task 1: Make Docker Compose a stable Spark execution target

**Files:**
- Create: `docker/spark-defaults.conf`
- Modify: `docker-compose.yml`

**Interfaces:**
- Consumes: the existing `spark-master`, `spark-worker`, `spark-history`, `spark-network`, and `${SPARKOS_REPO_ROOT:-.}` mount.
- Produces: a Compose service named `spark-client` that can execute `/opt/spark/bin/spark-submit` through `docker compose run --rm -T spark-client`.

- [ ] **Step 1: Create the shared Spark defaults**

Create `docker/spark-defaults.conf` with exactly these initial local-development defaults:

```properties
spark.eventLog.enabled                 true
spark.eventLog.dir                     file:/opt/sparkos/artifacts/spark-events
spark.history.fs.logDirectory          file:/opt/sparkos/artifacts/spark-events
spark.sql.session.timeZone             Asia/Shanghai
spark.sql.shuffle.partitions           4
spark.ui.showConsoleProgress            false
```

- [ ] **Step 2: Add Master readiness and the one-off client service**

Add this health check to `spark-master` after `restart: unless-stopped`:

```yaml
    healthcheck:
      test: ["CMD-SHELL", "bash -c '</dev/tcp/127.0.0.1/7077'"]
      interval: 2s
      timeout: 2s
      retries: 20
      start_period: 5s
```

Change `spark-worker.depends_on` to wait for Master health:

```yaml
    depends_on:
      spark-master:
        condition: service_healthy
```

Add this service before `spark-history`:

```yaml
  spark-client:
    image: apache/spark:3.5.3
    profiles:
      - jobs

    depends_on:
      spark-master:
        condition: service_healthy
      spark-worker:
        condition: service_started

    volumes:
      - ${SPARKOS_REPO_ROOT:-.}:/opt/sparkos
      - ./docker/spark-defaults.conf:/opt/spark/conf/spark-defaults.conf:ro

    networks:
      - spark-network
```

The service intentionally has no fixed `container_name`, published port, restart policy, or idle command. `docker compose run` creates one isolated Driver container per job and removes it after exit.

- [ ] **Step 3: Validate the rendered Compose configuration**

Run:

```bash
docker compose config --quiet
docker compose --profile jobs config --services
```

Expected: both commands exit `0`; the service list contains `spark-master`, `spark-worker`, `spark-client`, and `spark-history`.

- [ ] **Step 4: Start the cluster and verify readiness**

Run:

```bash
mkdir -p artifacts/spark-events artifacts/spark-jobs
docker compose up -d spark-master spark-worker spark-history
docker compose ps
```

Expected: Master becomes healthy, Worker and History Server are running, and no persistent `spark-client` container exists.

- [ ] **Step 5: Commit the Compose baseline**

```bash
git add docker-compose.yml docker/spark-defaults.conf
git commit -m "feat: add local Spark job container"
```

---

### Task 2: Implement the cancellable one-off Spark job runner

**Files:**
- Create: `sparkos/infrastructure/spark/__init__.py`
- Create: `sparkos/infrastructure/spark/models.py`
- Create: `sparkos/infrastructure/spark/client.py`
- Create: `tests/test_spark_client.py`

**Interfaces:**
- Consumes: `docker compose run --rm -T spark-client`, repository bind mount `/opt/sparkos`, and Spark Master URL `spark://spark-master:7077`.
- Produces: `SparkJobRequest`, `SparkJobResult`, `SparkRunnerConfig`, and `SparkJobRunner.run(request) -> SparkJobResult`.

- [ ] **Step 1: Write failing request/result tests**

Create `tests/test_spark_client.py` with the model tests first:

```python
from __future__ import annotations

import json
import unittest

from sparkos.infrastructure.spark.models import SparkJobRequest, SparkJobResult


class SparkJobModelTests(unittest.TestCase):
    def test_request_rejects_invalid_resource_values(self) -> None:
        with self.assertRaisesRegex(ValueError, "executor_memory"):
            SparkJobRequest(
                job_name="bad-memory",
                job_type="pyspark",
                code="print('x')",
                executor_memory="lots",
            )

        with self.assertRaisesRegex(ValueError, "executor_cores"):
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

        self.assertEqual(
            json.loads(result.to_json()),
            {
                "job_id": "job-1",
                "status": "succeeded",
                "application_id": "app-1-1",
                "exit_code": 0,
                "duration_seconds": 1.25,
                "log_path": "artifacts/spark-jobs/job-1/spark.log",
                "output": "done",
            },
        )
```

- [ ] **Step 2: Run the model tests and verify RED**

Run:

```bash
.venv/bin/python -m pytest tests/test_spark_client.py -q
```

Expected: collection fails because `sparkos.infrastructure.spark.models` does not exist.

- [ ] **Step 3: Implement validated models**

Create `sparkos/infrastructure/spark/models.py`:

```python
"""Validated inputs and structured outputs for local Spark jobs."""

from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass
from typing import Literal

SparkJobType = Literal["spark_sql", "pyspark"]
SparkJobStatus = Literal["succeeded", "failed", "timed_out"]

_MEMORY_PATTERN = re.compile(r"^[1-9][0-9]*[kKmMgGtT]$")


@dataclass(frozen=True)
class SparkJobRequest:
    job_name: str
    job_type: SparkJobType
    code: str
    executor_memory: str = "1g"
    executor_cores: int = 1
    num_executors: int = 1
    driver_memory: str = "1g"
    timeout_seconds: int = 600

    def __post_init__(self) -> None:
        if not self.job_name.strip() or len(self.job_name) > 80:
            raise ValueError("job_name 必须为 1 到 80 个字符")
        if self.job_type not in {"spark_sql", "pyspark"}:
            raise ValueError("job_type 必须是 spark_sql 或 pyspark")
        if not self.code.strip() or len(self.code.encode("utf-8")) > 200_000:
            raise ValueError("code 必须为非空文本且不超过 200000 字节")
        for field_name, value in (
            ("executor_memory", self.executor_memory),
            ("driver_memory", self.driver_memory),
        ):
            if not _MEMORY_PATTERN.fullmatch(value):
                raise ValueError(f"{field_name} 必须使用 1g、512m 等格式")
        for field_name, value in (
            ("executor_cores", self.executor_cores),
            ("num_executors", self.num_executors),
        ):
            if not 1 <= value <= 32:
                raise ValueError(f"{field_name} 必须在 1 到 32 之间")
        if not 1 <= self.timeout_seconds <= 3600:
            raise ValueError("timeout_seconds 必须在 1 到 3600 之间")


@dataclass(frozen=True)
class SparkJobResult:
    job_id: str
    status: SparkJobStatus
    application_id: str
    exit_code: int | None
    duration_seconds: float
    log_path: str
    output: str

    def to_json(self) -> str:
        return json.dumps(asdict(self), ensure_ascii=False)
```

Create `sparkos/infrastructure/spark/__init__.py`:

```python
"""Local Docker Spark execution infrastructure."""

from sparkos.infrastructure.spark.models import SparkJobRequest, SparkJobResult

__all__ = ["SparkJobRequest", "SparkJobResult"]
```

- [ ] **Step 4: Run model tests and verify GREEN**

Run:

```bash
.venv/bin/python -m pytest tests/test_spark_client.py -q
```

Expected: `2 passed`.

- [ ] **Step 5: Add failing runner tests**

Append tests that fix the runner contract before implementation:

```python
import asyncio
import tempfile
from pathlib import Path
from unittest.mock import AsyncMock, patch

from sparkos.infrastructure.spark.client import SparkJobRunner, SparkRunnerConfig


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


class SparkJobRunnerTests(unittest.IsolatedAsyncioTestCase):
    async def test_pyspark_job_uses_one_off_client_without_shell(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            repo_root = Path(directory)
            compose_file = repo_root / "docker-compose.yml"
            compose_file.write_text("services: {}", encoding="utf-8")
            runner = SparkJobRunner(
                SparkRunnerConfig(repo_root=repo_root, compose_file=compose_file)
            )
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
            self.assertEqual(
                command[:4], ("docker", "compose", "-f", str(compose_file))
            )
            self.assertIn("run", command)
            self.assertIn("--rm", command)
            self.assertIn("-T", command)
            self.assertIn("spark-client", command)
            self.assertIn("spark://spark-master:7077", command)
            self.assertIn("spark.driver.bindAddress=0.0.0.0", command)
            self.assertTrue(
                any(
                    argument.startswith("spark.driver.host=sparkmind-job-")
                    for argument in command
                )
            )
            self.assertNotIn("sh", command)
            self.assertNotIn("-c", command)
            self.assertEqual(result.status, "succeeded")

    async def test_sql_job_generates_bounded_result_wrapper(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            repo_root = Path(directory)
            compose_file = repo_root / "docker-compose.yml"
            compose_file.write_text("services: {}", encoding="utf-8")
            runner = SparkJobRunner(
                SparkRunnerConfig(repo_root=repo_root, compose_file=compose_file)
            )

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
            self.assertEqual(
                (job_dir / "query.sql").read_text(encoding="utf-8"),
                "select 1 as value",
            )
            wrapper = (job_dir / "job.py").read_text(encoding="utf-8")
            self.assertIn("result.show(n=200, truncate=False)", wrapper)

    async def test_result_keeps_application_id_but_only_returns_log_tail(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            repo_root = Path(directory)
            compose_file = repo_root / "docker-compose.yml"
            compose_file.write_text("services: {}", encoding="utf-8")
            runner = SparkJobRunner(
                SparkRunnerConfig(repo_root=repo_root, compose_file=compose_file)
            )

            async def launch(*args: object, **kwargs: object) -> FakeProcess:
                log_file = kwargs["stdout"]
                log_file.write(b"Connected with app ID app-1722920000000-0001\n")
                log_file.write(b"x" * 25_000)
                log_file.write(b"\nfinal-result\n")
                log_file.flush()
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

            self.assertEqual(result.application_id, "app-1722920000000-0001")
            self.assertLessEqual(len(result.output.encode("utf-8")), 20_000)
            self.assertIn("final-result", result.output)

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
            runner = SparkJobRunner(
                SparkRunnerConfig(repo_root=repo_root, compose_file=compose_file)
            )
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

            cleanup_commands = [
                call.args for call in create_process.await_args_list[1:]
            ]
            self.assertTrue(
                any(command[:2] == ("docker", "stop") for command in cleanup_commands)
            )
            self.assertTrue(
                any(
                    command[:3] == ("docker", "rm", "-f")
                    for command in cleanup_commands
                )
            )
            self.assertEqual(result.status, "timed_out")
```

- [ ] **Step 6: Run runner tests and verify RED**

Run:

```bash
.venv/bin/python -m pytest tests/test_spark_client.py -q
```

Expected: collection fails because `SparkJobRunner` and `SparkRunnerConfig` do not exist.

- [ ] **Step 7: Implement the one-off runner**

Create `sparkos/infrastructure/spark/client.py` with these behaviors:

```python
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
```

Update `sparkos/infrastructure/spark/__init__.py`:

```python
"""Local Docker Spark execution infrastructure."""

from sparkos.infrastructure.spark.client import SparkJobRunner, SparkRunnerConfig
from sparkos.infrastructure.spark.models import SparkJobRequest, SparkJobResult

__all__ = [
    "SparkJobRequest",
    "SparkJobResult",
    "SparkJobRunner",
    "SparkRunnerConfig",
]
```

- [ ] **Step 8: Run runner tests and fix only contract failures**

Run:

```bash
.venv/bin/python -m pytest tests/test_spark_client.py -q
.venv/bin/ruff check sparkos/infrastructure/spark tests/test_spark_client.py
.venv/bin/ruff format sparkos/infrastructure/spark tests/test_spark_client.py
```

Expected: all Spark client tests pass and Ruff exits `0`.

- [ ] **Step 9: Commit the runner**

```bash
git add sparkos/infrastructure/spark tests/test_spark_client.py
git commit -m "feat: run Spark jobs in one-off containers"
```

---

### Task 3: Expose exactly one Spark tool to the Agent

**Files:**
- Modify: `sparkos/agent/tools/registry.py`
- Create: `tests/test_spark_tools.py`

**Interfaces:**
- Consumes: `SparkJobRunner.run(SparkJobRequest) -> SparkJobResult`.
- Produces: OpenAI function tool `run_spark_job` and an awaitable dispatch result handled by the existing `StepExecutor` awaitable branch.

- [ ] **Step 1: Write failing tool schema and dispatch tests**

Create `tests/test_spark_tools.py`:

```python
from __future__ import annotations

import inspect
import json
import unittest
from unittest.mock import AsyncMock, patch

from sparkos.agent.tools.registry import TOOL_DEFINITIONS, execute_tool
from sparkos.infrastructure.spark.models import SparkJobResult


class SparkToolTests(unittest.IsolatedAsyncioTestCase):
    def test_registry_exposes_one_spark_tool(self) -> None:
        spark_tools = [
            tool["function"]
            for tool in TOOL_DEFINITIONS
            if "spark" in tool["function"]["name"]
        ]

        self.assertEqual([tool["name"] for tool in spark_tools], ["run_spark_job"])
        self.assertEqual(
            spark_tools[0]["parameters"]["properties"]["job_type"]["enum"],
            ["spark_sql", "pyspark"],
        )

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
            self.assertTrue(inspect.isawaitable(pending))
            result = await pending

        request = run.await_args.args[0]
        self.assertEqual(request.job_name, "hello")
        self.assertEqual(request.executor_memory, "2g")
        self.assertEqual(json.loads(result)["status"], "succeeded")
```

- [ ] **Step 2: Run tool tests and verify RED**

Run:

```bash
.venv/bin/python -m pytest tests/test_spark_tools.py -q
```

Expected: tests fail because `run_spark_job` is absent.

- [ ] **Step 3: Add the tool schema and dispatch**

In `sparkos/agent/tools/registry.py`:

1. Import `Awaitable`, `SparkJobRequest`, and `SparkJobRunner`.
2. Append this function definition to `TOOL_DEFINITIONS`:

```python
(
    {
        "type": "function",
        "function": {
            "name": "run_spark_job",
            "description": "在本地 Docker Spark 集群同步执行一条 Spark SQL 或一个 PySpark 作业，并返回状态、日志末尾和作业信息。",
            "parameters": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "job_name": {
                        "type": "string",
                        "description": "1 到 80 个字符的作业名称",
                    },
                    "job_type": {
                        "type": "string",
                        "enum": ["spark_sql", "pyspark"],
                    },
                    "code": {
                        "type": "string",
                        "description": "单条 Spark SQL 或完整 PySpark 脚本",
                    },
                    "executor_memory": {"type": "string", "default": "1g"},
                    "executor_cores": {"type": "integer", "default": 1},
                    "num_executors": {"type": "integer", "default": 1},
                    "driver_memory": {"type": "string", "default": "1g"},
                    "timeout_seconds": {
                        "type": "integer",
                        "minimum": 1,
                        "maximum": 3600,
                        "default": 600,
                    },
                },
                "required": ["job_name", "job_type", "code"],
            },
        },
    },
)
```

Add the module-level runner:

```python
_SPARK_RUNNER = SparkJobRunner()
```

Change the executor return type and add dispatch:

```python
def execute_tool(
    name: str,
    arguments: dict[str, Any],
) -> str | Awaitable[str]:
    """根据工具名称和参数执行对应工具。"""
    if name == "read_file":
        return _read_file(arguments["path"])
    if name == "write_file":
        return _write_file(arguments["path"], arguments["content"])
    if name == "shell":
        return _shell(arguments["command"])
    if name == "web_fetch":
        return _web_fetch(arguments["url"])
    if name == "run_spark_job":
        return _run_spark_job(arguments)
    return f"未知工具: {name}"
```

Add the async adapter:

```python
async def _run_spark_job(arguments: dict[str, Any]) -> str:
    request = SparkJobRequest(
        job_name=arguments["job_name"],
        job_type=arguments["job_type"],
        code=arguments["code"],
        executor_memory=arguments.get("executor_memory", "1g"),
        executor_cores=int(arguments.get("executor_cores", 1)),
        num_executors=int(arguments.get("num_executors", 1)),
        driver_memory=arguments.get("driver_memory", "1g"),
        timeout_seconds=int(arguments.get("timeout_seconds", 600)),
    )
    result = await _SPARK_RUNNER.run(request)
    return result.to_json()
```

This works with the existing `StepExecutor`: the synchronous registry call runs in its worker thread, returns a coroutine for Spark only, and lines 336-337 await that coroutine on the event loop.

- [ ] **Step 4: Run focused and runtime tests**

Run:

```bash
.venv/bin/python -m pytest tests/test_spark_tools.py tests/test_step_executor.py tests/test_agent_runtime.py -q
.venv/bin/ruff check sparkos/agent/tools/registry.py tests/test_spark_tools.py
.venv/bin/ruff format sparkos/agent/tools/registry.py tests/test_spark_tools.py
```

Expected: all focused tests pass and Ruff exits `0`.

- [ ] **Step 5: Commit the Agent tool**

```bash
git add sparkos/agent/tools/registry.py tests/test_spark_tools.py
git commit -m "feat: expose local Spark execution tool"
```

---

### Task 4: Align the Spark skill and prove the real path

**Files:**
- Modify: `sparkos/agent/skills/spark-job/SKILL.md`
- Modify: `README.md`

**Interfaces:**
- Consumes: `run_spark_job` and its `SparkJobResult` JSON.
- Produces: instructions that cause the model to call the real tool once and interpret its terminal result.

- [ ] **Step 1: Replace the asynchronous skill contract**

Update `sparkos/agent/skills/spark-job/SKILL.md` so its workflow states:

````markdown
## 执行工具

本 Skill 使用 `run_spark_job` 完成一次同步执行。工具调用返回前，作业已经成功、失败或超时；不要生成状态轮询或日志查询工具名。

输入：

```json
{
  "job_name": "user_active_daily",
  "job_type": "spark_sql",
  "code": "select city_id, count(distinct user_id) from dwd_user_action_di group by city_id",
  "executor_memory": "1g",
  "executor_cores": 1,
  "num_executors": 1,
  "driver_memory": "1g",
  "timeout_seconds": 600
}
```

输出：

```json
{
  "job_id": "7c8f3e6a0d9b4d5eb3c1c8e3276d121a",
  "status": "succeeded | failed | timed_out",
  "application_id": "app-1722920000000-0001",
  "exit_code": 0,
  "duration_seconds": 8.42,
  "log_path": "artifacts/spark-jobs/7c8f3e6a0d9b4d5eb3c1c8e3276d121a/spark.log",
  "output": "Spark 日志末尾或 SQL 结果"
}
```
````

Also change the workflow to: validate input, call `run_spark_job` once, inspect terminal status and output, explain failure or results, and reference `log_path` when deeper diagnosis needs the complete log.

- [ ] **Step 2: Document local startup and execution**

Append to `README.md`:

````markdown
## 本地 Spark

启动 Spark 集群：

```bash
mkdir -p artifacts/spark-events artifacts/spark-jobs
docker compose up -d spark-master spark-worker spark-history
```

运行 Agent：

```bash
.venv/bin/python main.py
```

Agent 的 `run_spark_job` 工具会为每次调用启动临时 `spark-client` 容器。Driver 在该容器运行，Executor 在 `spark-worker` 运行，完成后临时容器自动删除。

- Master UI: http://localhost:8080
- History UI: http://localhost:18080
- 作业日志: `artifacts/spark-jobs/<job_id>/spark.log`
````

- [ ] **Step 3: Run a real PySpark smoke job through the one-off service**

Run:

```bash
docker compose run --rm -T spark-client \
  /opt/spark/bin/spark-submit \
  --master spark://spark-master:7077 \
  /opt/sparkos/tmp_hello-spark.py
```

Expected: exit `0`, output contains rows `(1, a)` and `(2, b)`, and the temporary client container disappears from `docker compose ps -a`.

- [ ] **Step 4: Run the complete verification suite**

Run:

```bash
.venv/bin/python -m pytest tests/ -q
.venv/bin/ruff check sparkos tests
.venv/bin/ruff format --check sparkos tests
.venv/bin/python -m compileall -q sparkos tests
docker compose config --quiet
git diff --check
```

Expected: all tests pass, Ruff reports no errors or formatting changes, compilation and Compose validation exit `0`, and `git diff --check` reports no whitespace errors.

- [ ] **Step 5: Exercise the Agent-facing adapter without the LLM**

Run:

```bash
.venv/bin/python -c 'import asyncio, json; from sparkos.agent.tools.registry import execute_tool; result = execute_tool("run_spark_job", {"job_name": "agent-smoke", "job_type": "spark_sql", "code": "select 1 as value"}); print(asyncio.run(result))'
```

Expected: JSON has `status: "succeeded"`, `exit_code: 0`, a non-empty `application_id`, and output containing the `value` column and row `1`.

- [ ] **Step 6: Commit documentation and skill alignment**

```bash
git add README.md sparkos/agent/skills/spark-job/SKILL.md
git commit -m "docs: describe local Spark execution"
```

---

## Deferred Scope

The following capabilities should be added only after the synchronous local path has real usage evidence:

- Long-lived job submission with separate status/log/cancel tools.
- Multiple concurrent Agent jobs and resource admission control.
- Hive Metastore and catalog discovery.
- Spark History REST metrics ingestion.
- Livy, Spark Connect, Kubernetes, or remote-cluster transports.
- Arbitrary package upload and dependency resolution.

## Self-Review

- Spec coverage: the plan keeps Agent on the host, Spark in local Docker, uses Docker CLI directly, exposes one tool, persists logs, and covers SQL plus PySpark.
- Placeholder scan: commands, schemas, defaults, paths, status values, and validation bounds are concrete.
- Type consistency: `SparkJobRequest`, `SparkJobResult`, `SparkRunnerConfig`, `SparkJobRunner`, and `run_spark_job` use the same names and fields in implementation, tests, skill, and documentation.
- Worktree safety: only the current Spark-related edited paths are extended; `config/config.yaml` and unrelated user changes are not rewritten by this plan.
