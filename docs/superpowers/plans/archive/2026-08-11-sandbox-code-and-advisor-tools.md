# Sandbox Code And Advisor Tools Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a `run_code` tool that executes Python or Bash in a locked-down one-off Docker container, and an `ask_advisor` tool that asks a separately configured higher-capability model for bounded advice when the primary Agent is stuck.

**Architecture:** Keep the Agent process and tool loop on the host. `run_code` writes an auditable run directory, invokes a prebuilt local Docker image with no network or host-workspace access, and returns a bounded JSON result. `ask_advisor` calls a separate OpenAI-compatible client with no tools, a strict input budget, a timeout, and one call per plan step; its answer is advisory data that the primary Agent must verify before using.

**Tech Stack:** Python 3.14, asyncio subprocesses, Docker, OpenAI-compatible Chat Completions, YAML configuration, unittest/pytest, Ruff.

## Global Constraints

- Preserve the current uncommitted Spark work and its `run_spark_job` contract.
- Expose exactly two new Agent-facing tools: `run_code` and `ask_advisor`.
- Remove the Agent-facing `shell` tool after `run_code` is covered by tests; otherwise arbitrary commands still execute on the host and the sandbox boundary is misleading.
- Support exactly `python` and `bash` in the first `run_code` release.
- Do not mount the repository root, project source tree, Docker socket, home directory, credentials, or SSH state into code containers; mount only the current `artifacts/code-runs/{run_id}` directory read-only.
- Disable container networking and automatic image pulls.
- Use `asyncio.create_subprocess_exec`; never construct Docker commands with `shell=True`.
- Bound code at 100,000 UTF-8 bytes, stdin at 64,000 UTF-8 bytes, execution time at 30 seconds, model-visible output at 20,000 bytes, memory at 256 MiB, CPUs at 1, and processes at 64.
- Persist source and complete logs under the already ignored `artifacts/code-runs/<run_id>/` directory.
- Configure the advisor through the semantic model alias `advisor`; the OpenAI-compatible gateway owns the mapping from that alias to the higher-capability model.
- Give the advisor no tools and no implicit conversation history. The primary Agent must send only the specific problem, relevant context, and prior attempts.
- Permit at most one `ask_advisor` call per plan step. Existing `max_tool_rounds` remains the outer bound for all tools.
- Tool errors must be returned as structured tool results so the Agent can recover; task cancellation must still propagate as `asyncio.CancelledError` after container cleanup.
- Run focused tests first, then the complete test suite and Ruff.

---

## Public Tool Contracts

`run_code` request:

```json
{
  "language": "python",
  "code": "print(sum(range(10)))",
  "stdin": "",
  "timeout_seconds": 10
}
```

`run_code` result:

```json
{
  "run_id": "4f3a28cbb65f4d44a29d7f92c95c341e",
  "status": "succeeded",
  "exit_code": 0,
  "duration_seconds": 0.123,
  "log_path": "artifacts/code-runs/4f3a28cbb65f4d44a29d7f92c95c341e/output.log",
  "output": "45\n",
  "output_truncated": false
}
```

`ask_advisor` request:

```json
{
  "question": "Which retry strategy fits this failure mode?",
  "context": "The step calls an idempotent API and receives intermittent 503 responses.",
  "attempts": "A fixed 1-second retry was tried three times and still synchronized callers."
}
```

`ask_advisor` result:

```json
{
  "status": "succeeded",
  "model": "advisor",
  "answer": "Use capped exponential backoff with full jitter...",
  "duration_seconds": 1.234
}
```

---

## File Structure

- Create `docker/code-sandbox.Dockerfile`: immutable Python/Bash runtime with an unprivileged user.
- Create `sparkos/infrastructure/code_sandbox/__init__.py`: export sandbox request, result, config, and runner types.
- Create `sparkos/infrastructure/code_sandbox/models.py`: validate `run_code` input and serialize stable results.
- Create `sparkos/infrastructure/code_sandbox/runner.py`: write artifacts, build the Docker argv, execute, timeout, cancel, clean up, and bound output.
- Create `sparkos/infrastructure/advisor/__init__.py`: export advisor request, result, and service types.
- Create `sparkos/infrastructure/advisor/models.py`: validate advisor input and serialize stable results.
- Create `sparkos/infrastructure/advisor/service.py`: issue a no-tools, bounded model request through `chat_once`.
- Modify `config/config.py`: add validated `AdvisorConfig` loading with environment overrides.
- Modify `config/config.yaml`: add the enabled `advisor` alias and runtime call budget.
- Modify `sparkos/agent/tools/registry.py`: declare and dispatch `run_code` and `ask_advisor`, and remove `shell`.
- Modify `sparkos/agent/step_executor.py`: enforce per-step advisor call limits before dispatch.
- Modify `sparkos/agent/runtime.py`: pass the advisor limit into the default `StepExecutor`.
- Modify `sparkos/agent/system_prompt.md`: tell the primary Agent when to consult and how to verify advisor output.
- Modify `README.md`: document sandbox image build, isolation, artifacts, and advisor configuration.
- Create `tests/test_code_sandbox.py`: model, Docker command, output, timeout, cancellation, and cleanup tests.
- Create `tests/test_advisor.py`: input validation, prompt isolation, timeout, empty response, and result tests.
- Create `tests/test_config.py`: advisor defaults, alias, limits, and environment overrides.
- Create `tests/test_agent_tools.py`: schemas and dispatch for both new tools plus removal of `shell`.
- Modify `tests/test_step_executor.py`: advisor budget and post-advice continuation tests.

---

### Task 1: Build the locked-down code sandbox runner

**Files:**
- Create: `docker/code-sandbox.Dockerfile`
- Create: `sparkos/infrastructure/code_sandbox/__init__.py`
- Create: `sparkos/infrastructure/code_sandbox/models.py`
- Create: `sparkos/infrastructure/code_sandbox/runner.py`
- Create: `tests/test_code_sandbox.py`

**Interfaces:**
- Consumes: a local Docker image named `sparkmind-code-sandbox:latest`.
- Produces: `CodeRunRequest`, `CodeRunResult`, `CodeSandboxConfig`, and `CodeSandboxRunner.run(request) -> CodeRunResult`.

- [ ] **Step 1: Write the failing model tests**

Create `tests/test_code_sandbox.py` with these initial tests:

```python
from __future__ import annotations

import json
import unittest

from sparkos.infrastructure.code_sandbox.models import CodeRunRequest, CodeRunResult


class CodeRunModelTests(unittest.TestCase):
    def test_request_accepts_python_and_bash(self) -> None:
        self.assertEqual(CodeRunRequest("python", "print(1)").language, "python")
        self.assertEqual(CodeRunRequest("bash", "printf ok").language, "bash")

    def test_request_rejects_unbounded_or_unknown_input(self) -> None:
        with self.assertRaisesRegex(ValueError, "language"):
            CodeRunRequest("ruby", "puts 1")  # type: ignore[arg-type]
        with self.assertRaisesRegex(ValueError, "code"):
            CodeRunRequest("python", "")
        with self.assertRaisesRegex(ValueError, "code"):
            CodeRunRequest("python", "x" * 100_001)
        with self.assertRaisesRegex(ValueError, "stdin"):
            CodeRunRequest("python", "print(1)", stdin="x" * 64_001)
        with self.assertRaisesRegex(ValueError, "timeout_seconds"):
            CodeRunRequest("python", "print(1)", timeout_seconds=31)

    def test_result_serializes_as_stable_json(self) -> None:
        result = CodeRunResult(
            run_id="run-1",
            status="succeeded",
            exit_code=0,
            duration_seconds=0.25,
            log_path="artifacts/code-runs/run-1/output.log",
            output="ok\n",
            output_truncated=False,
        )
        self.assertEqual(json.loads(result.to_json())["status"], "succeeded")
        self.assertEqual(json.loads(result.to_json())["output"], "ok\n")
```

- [ ] **Step 2: Verify the model tests fail**

Run:

```bash
.venv/bin/python -m pytest tests/test_code_sandbox.py -q
```

Expected: collection fails because `sparkos.infrastructure.code_sandbox` does not exist.

- [ ] **Step 3: Implement validated request and result models**

Create `sparkos/infrastructure/code_sandbox/models.py`:

```python
"""Validated requests and structured results for sandboxed code runs."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from typing import Literal

CodeLanguage = Literal["python", "bash"]
CodeRunStatus = Literal["succeeded", "failed", "timed_out"]

_MAX_CODE_BYTES = 100_000
_MAX_STDIN_BYTES = 64_000


@dataclass(frozen=True)
class CodeRunRequest:
    language: CodeLanguage
    code: str
    stdin: str = ""
    timeout_seconds: int = 10

    def __post_init__(self) -> None:
        if self.language not in {"python", "bash"}:
            raise ValueError("language 必须是 python 或 bash")
        code_size = len(self.code.encode("utf-8"))
        if not self.code.strip() or code_size > _MAX_CODE_BYTES:
            raise ValueError("code 必须为非空文本且不超过 100000 字节")
        if len(self.stdin.encode("utf-8")) > _MAX_STDIN_BYTES:
            raise ValueError("stdin 不能超过 64000 字节")
        if not 1 <= self.timeout_seconds <= 30:
            raise ValueError("timeout_seconds 必须在 1 到 30 之间")


@dataclass(frozen=True)
class CodeRunResult:
    run_id: str
    status: CodeRunStatus
    exit_code: int | None
    duration_seconds: float
    log_path: str
    output: str
    output_truncated: bool

    def to_json(self) -> str:
        return json.dumps(asdict(self), ensure_ascii=False)
```

Create `sparkos/infrastructure/code_sandbox/__init__.py`:

```python
"""One-off Docker sandbox for Python and Bash snippets."""

from sparkos.infrastructure.code_sandbox.models import CodeRunRequest, CodeRunResult
from sparkos.infrastructure.code_sandbox.runner import (
    CodeSandboxConfig,
    CodeSandboxRunner,
)

__all__ = [
    "CodeRunRequest",
    "CodeRunResult",
    "CodeSandboxConfig",
    "CodeSandboxRunner",
]
```

- [ ] **Step 4: Verify the model tests pass**

Run:

```bash
.venv/bin/python -m pytest tests/test_code_sandbox.py -q
```

Expected: `3 passed`.

- [ ] **Step 5: Add failing runner tests**

Append a `CodeSandboxRunnerTests(unittest.IsolatedAsyncioTestCase)` class. Patch `asyncio.create_subprocess_exec` with a fake process and assert all of the following:

```python
command = create_process.await_args.args
self.assertEqual(command[:3], ("docker", "run", "--rm"))
self.assertIn("--network", command)
self.assertEqual(command[command.index("--network") + 1], "none")
self.assertIn("--read-only", command)
self.assertIn("--cap-drop", command)
self.assertEqual(command[command.index("--cap-drop") + 1], "ALL")
self.assertIn("no-new-privileges", command)
self.assertIn("--pids-limit", command)
self.assertIn("64", command)
self.assertIn("--memory", command)
self.assertIn("256m", command)
self.assertIn("--cpus", command)
self.assertIn("1.0", command)
self.assertIn("--pull", command)
self.assertEqual(command[command.index("--pull") + 1], "never")
self.assertNotIn("/var/run/docker.sock", " ".join(command))
mount_spec = command[command.index("--mount") + 1]
self.assertEqual(mount_spec, f"type=bind,src={job_dir},dst=/workspace,readonly")
self.assertNotEqual(job_dir, repo_root)
self.assertEqual(command[-3:], ("python", "-I", "/workspace/main.py"))
```

Also add focused tests for:

```python
self.assertEqual(bash_command[-3:], ("/bin/bash", "--noprofile", "/workspace/main.sh"))
self.assertEqual(result.status, "timed_out")
self.assertLessEqual(len(result.output.encode("utf-8")), 20_000)
self.assertTrue(result.output_truncated)
```

The cancellation test must wait until the fake process starts, cancel the `run` task, assert the cleanup command includes `docker rm -f <container_name>`, and assert `CancelledError` reaches the caller.

- [ ] **Step 6: Implement the runner**

Create `sparkos/infrastructure/code_sandbox/runner.py` with this concrete configuration and command policy:

```python
@dataclass(frozen=True)
class CodeSandboxConfig:
    repo_root: Path
    image: str = "sparkmind-code-sandbox:latest"
    memory: str = "256m"
    cpus: str = "1.0"
    pids_limit: int = 64
    container_workspace: str = "/workspace"

    @classmethod
    def from_env(cls) -> CodeSandboxConfig:
        repo_root = Path(
            os.environ.get(
                "SPARKOS_REPO_ROOT",
                Path(__file__).resolve().parents[3],
            )
        ).resolve()
        return cls(
            repo_root=repo_root,
            image=os.environ.get(
                "SPARKMIND_CODE_SANDBOX_IMAGE",
                "sparkmind-code-sandbox:latest",
            ),
        )
```

`CodeSandboxRunner.run` must:

1. Create `artifacts/code-runs/{uuid4.hex}/` with mode `0o755`.
2. Write `main.py` or `main.sh` as UTF-8 with mode `0o644`.
3. Open an in-memory or temporary stdin file and `output.log`; pass both as file descriptors to `create_subprocess_exec` so stdout is never buffered without a bound in host memory.
4. Run the process under `asyncio.timeout(request.timeout_seconds)`.
5. On timeout, execute `docker stop --time 1 {container_name}` and `docker rm -f {container_name}`, then return `status="timed_out"`.
6. On cancellation, perform the same cleanup and re-raise `CancelledError`.
7. On missing Docker or another launch `OSError`, write the error to `output.log` and return `status="failed"`, `exit_code=None`.
8. Read only the last 20,000 bytes for `output`, while setting `output_truncated` from the complete log size.

Build the Docker argv as separate arguments in exactly this security shape:

```python
[
    "docker",
    "run",
    "--rm",
    "--name",
    container_name,
    "--pull",
    "never",
    "--network",
    "none",
    "--read-only",
    "--cap-drop",
    "ALL",
    "--security-opt",
    "no-new-privileges",
    "--pids-limit",
    "64",
    "--memory",
    "256m",
    "--cpus",
    "1.0",
    "--tmpfs",
    "/tmp:rw,nosuid,nodev,size=64m",
    "--mount",
    f"type=bind,src={job_dir},dst=/workspace,readonly",
    "--workdir",
    "/workspace",
    config.image,
    *language_command,
]
```

Use `("python", "-I", "/workspace/main.py")` for Python and `("/bin/bash", "--noprofile", "/workspace/main.sh")` for Bash. Do not pass code through command-line arguments.

- [ ] **Step 7: Add the immutable runtime image**

Create `docker/code-sandbox.Dockerfile`:

```dockerfile
FROM python:3.14-slim

RUN useradd --uid 65532 --create-home --shell /usr/sbin/nologin sandbox
USER 65532:65532
WORKDIR /workspace

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1
```

Build and smoke test it:

```bash
docker build --pull -f docker/code-sandbox.Dockerfile -t sparkmind-code-sandbox:latest docker
docker run --rm --network none --read-only sparkmind-code-sandbox:latest python -I -c 'print(6 * 7)'
```

Expected: build exits `0`; smoke test prints `42`.

- [ ] **Step 8: Run focused verification and commit**

```bash
.venv/bin/python -m pytest tests/test_code_sandbox.py -q
.venv/bin/python -m ruff check sparkos/infrastructure/code_sandbox tests/test_code_sandbox.py
.venv/bin/python -m ruff format --check sparkos/infrastructure/code_sandbox tests/test_code_sandbox.py
git add docker/code-sandbox.Dockerfile sparkos/infrastructure/code_sandbox tests/test_code_sandbox.py
git commit -m "feat: add isolated code sandbox runner"
```

Expected: focused tests and Ruff pass.

---

### Task 2: Expose `run_code` and retire host shell execution

**Files:**
- Modify: `sparkos/agent/tools/registry.py`
- Create: `tests/test_agent_tools.py`
- Preserve: `tests/test_spark_tools.py`

**Interfaces:**
- Consumes: `CodeSandboxRunner.run(CodeRunRequest)` from Task 1.
- Produces: an OpenAI function tool named `run_code` and `execute_tool("run_code", arguments)` returning an awaitable JSON string.

- [ ] **Step 1: Write failing registry tests**

Create `tests/test_agent_tools.py`:

```python
from __future__ import annotations

import inspect
import json
import unittest
from unittest.mock import AsyncMock, patch

from sparkos.agent.tools.registry import TOOL_DEFINITIONS, execute_tool
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
            new=AsyncMock(return_value=expected),
        ) as run:
            pending = execute_tool(
                "run_code",
                {"language": "python", "code": "print('ok')"},
            )
            self.assertTrue(inspect.isawaitable(pending))
            result = await pending

        self.assertEqual(run.await_args.args[0].language, "python")
        self.assertEqual(json.loads(result)["output"], "ok\n")
```

- [ ] **Step 2: Verify the registry tests fail**

```bash
.venv/bin/python -m pytest tests/test_agent_tools.py -q
```

Expected: `run_code` is missing and `shell` is still exposed.

- [ ] **Step 3: Add the tool definition and dispatch**

In `sparkos/agent/tools/registry.py`:

1. Import `CodeRunRequest` and `CodeSandboxRunner`.
2. Create one module-level `_CODE_RUNNER = CodeSandboxRunner()` beside `_SPARK_RUNNER`.
3. Replace the `shell` function schema with `run_code`.
4. Remove the `if name == "shell"` branch and `_shell` implementation.
5. Dispatch `run_code` to an async `_run_code(arguments)` helper that constructs `CodeRunRequest` and returns `result.to_json()`.

Use this exact schema:

```python
{
    "type": "function",
    "function": {
        "name": "run_code",
        "description": (
            "在无网络、无宿主工作区访问的一次性 Docker 沙箱中运行 Python 或 Bash，返回退出码、限长输出和完整日志路径。"
        ),
        "parameters": {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "language": {"type": "string", "enum": ["python", "bash"]},
                "code": {"type": "string", "description": "要运行的完整代码"},
                "stdin": {"type": "string", "default": ""},
                "timeout_seconds": {
                    "type": "integer",
                    "minimum": 1,
                    "maximum": 30,
                    "default": 10,
                },
            },
            "required": ["language", "code"],
        },
    },
}
```

- [ ] **Step 4: Run the complete tool registry tests**

```bash
.venv/bin/python -m pytest tests/test_agent_tools.py tests/test_spark_tools.py -q
.venv/bin/python -m ruff check sparkos/agent/tools/registry.py tests/test_agent_tools.py
.venv/bin/python -m ruff format --check sparkos/agent/tools/registry.py tests/test_agent_tools.py
```

Expected: both new-tool and existing Spark tests pass; no schema named `shell` remains.

- [ ] **Step 5: Commit the Agent-facing code tool**

```bash
git add sparkos/agent/tools/registry.py tests/test_agent_tools.py
git commit -m "feat: expose sandboxed code execution tool"
```

---

### Task 3: Implement the isolated advisor service and configuration

**Files:**
- Modify: `config/config.py`
- Modify: `config/config.yaml`
- Create: `sparkos/infrastructure/advisor/__init__.py`
- Create: `sparkos/infrastructure/advisor/models.py`
- Create: `sparkos/infrastructure/advisor/service.py`
- Create: `tests/test_config.py`
- Create: `tests/test_advisor.py`

**Interfaces:**
- Consumes: the existing `OpenAIChatClient.chat_once(messages, json_object=False)` interface.
- Produces: `AdvisorConfig`, `AdvisorRequest`, `AdvisorResult`, and `AdvisorService.ask(request) -> AdvisorResult`.

- [ ] **Step 1: Write failing configuration tests**

Create `tests/test_config.py` using a temporary YAML file and `patch.dict(os.environ, ..., clear=True)`. Assert:

```python
self.assertTrue(config.enabled)
self.assertEqual(config.model, "advisor")
self.assertEqual(config.base_url, "http://localhost:20128/v1")
self.assertEqual(config.api_key, "main-key")
self.assertEqual(config.timeout_seconds, 90)
self.assertEqual(config.max_question_chars, 4_000)
self.assertEqual(config.max_context_chars, 16_000)
self.assertEqual(config.max_attempts_chars, 8_000)
```

Add a second test with these environment variables and assert they override YAML:

```python
{
    "SPARKMIND_ADVISOR_ENABLED": "false",
    "SPARKMIND_ADVISOR_BASE_URL": "http://advisor.test/v1",
    "SPARKMIND_ADVISOR_API_KEY": "advisor-key",
    "SPARKMIND_ADVISOR_MODEL": "advisor-v2",
}
```

- [ ] **Step 2: Implement advisor configuration**

Add this dataclass to `config/config.py`:

```python
@dataclass(frozen=True)
class AdvisorConfig:
    enabled: bool
    base_url: str
    api_key: str
    model: str
    timeout_seconds: int
    max_question_chars: int
    max_context_chars: int
    max_attempts_chars: int
```

Change `load` to remain backward compatible while allowing `get_advisor_config(path)` to load a test file. `get_advisor_config` must reuse `api.base_url` and `api.api_key` when advisor-specific values are absent, apply the four environment overrides shown above, parse enabled values from `1/true/yes/on` and `0/false/no/off`, and raise `ValueError` for an invalid boolean, an enabled blank model, non-positive timeout, or non-positive text limits.

Add this configuration to `config/config.yaml`:

```yaml
advisor:
  enabled: true
  model: "advisor"
  timeout_seconds: 90
  max_question_chars: 4000
  max_context_chars: 16000
  max_attempts_chars: 8000
```

The alias `advisor` is deliberate. It avoids coupling SparkMind to a vendor model name and lets the configured OpenAI-compatible gateway route that alias to a stronger model.

- [ ] **Step 3: Verify configuration tests pass**

```bash
.venv/bin/python -m pytest tests/test_config.py -q
```

Expected: YAML fallback and environment override tests pass.

- [ ] **Step 4: Write failing advisor model and service tests**

Create `tests/test_advisor.py` with a fake client that records `chat_once` requests. Cover:

```python
request = AdvisorRequest(
    question="How should this retry policy change?",
    context="Calls return 503 during bursts.",
    attempts="Fixed delay caused synchronized retries.",
)
result = await service.ask(request)
self.assertEqual(result.status, "succeeded")
self.assertEqual(result.model, "advisor")
self.assertEqual(result.answer, "Use full jitter.")
self.assertEqual(len(fake_client.requests), 1)
self.assertEqual(fake_client.requests[0][0]["role"], "system")
self.assertEqual(fake_client.requests[0][1]["role"], "user")
self.assertNotIn("tools", fake_client.requests[0][1])
```

Also assert:

- Empty `question`, oversized `question`, oversized `context`, and oversized `attempts` raise `ValueError`.
- Disabled advisor returns `status="disabled"` without calling the model.
- A model call exceeding `timeout_seconds` returns `status="timed_out"`.
- An empty model response returns `status="failed"` with a concise error.
- A model exception returns `status="failed"`; `CancelledError` still propagates.

- [ ] **Step 5: Implement advisor request and result models**

Create `sparkos/infrastructure/advisor/models.py` with:

```python
AdvisorStatus = Literal["succeeded", "failed", "timed_out", "disabled"]


@dataclass(frozen=True)
class AdvisorRequest:
    question: str
    context: str
    attempts: str

    def validate(self, config: AdvisorConfig) -> None:
        if not self.question.strip():
            raise ValueError("question 不能为空")
        if not self.context.strip():
            raise ValueError("context 不能为空")
        if not self.attempts.strip():
            raise ValueError("attempts 不能为空")
        if len(self.question) > config.max_question_chars:
            raise ValueError("question 超过长度限制")
        if len(self.context) > config.max_context_chars:
            raise ValueError("context 超过长度限制")
        if len(self.attempts) > config.max_attempts_chars:
            raise ValueError("attempts 超过长度限制")


@dataclass(frozen=True)
class AdvisorResult:
    status: AdvisorStatus
    model: str
    answer: str
    duration_seconds: float
    error: str | None = None

    def to_json(self) -> str:
        return json.dumps(asdict(self), ensure_ascii=False)
```

- [ ] **Step 6: Implement the no-tools advisor service**

Create `sparkos/infrastructure/advisor/service.py`. Define an `AdvisorModel` protocol containing only `chat_once`; do not expose `chat_stream` or tool definitions to this service. Use this system prompt:

```text
你是主 Agent 的高级顾问。你只提供分析和建议，不执行工具、不假设未提供的上下文。
请按“建议、理由、风险、验证方式”组织回答。指出不确定性；不要把建议描述成已经执行或验证的结果。
```

Serialize the three request fields into one JSON user message. Wrap `client.chat_once(messages, json_object=False)` in `asyncio.timeout(config.timeout_seconds)`, measure duration with `time.monotonic`, return typed failure results for timeout/empty output/model errors, and re-raise `asyncio.CancelledError`.

The constructor must support test injection while providing a production factory:

```python
class AdvisorService:
    def __init__(self, config: AdvisorConfig, client: AdvisorModel | None = None) -> None:
        self.config = config
        self.client = client or OpenAIChatClient(
            ChatConfig(
                base_url=config.base_url,
                api_key=config.api_key,
                model=config.model,
            )
        )

    @classmethod
    def from_config(cls) -> AdvisorService:
        return cls(get_advisor_config())
```

Export the public types from `sparkos/infrastructure/advisor/__init__.py`.

- [ ] **Step 7: Run focused verification and commit**

```bash
.venv/bin/python -m pytest tests/test_config.py tests/test_advisor.py -q
.venv/bin/python -m ruff check config/config.py sparkos/infrastructure/advisor tests/test_config.py tests/test_advisor.py
.venv/bin/python -m ruff format --check config/config.py sparkos/infrastructure/advisor tests/test_config.py tests/test_advisor.py
git add config/config.py config/config.yaml sparkos/infrastructure/advisor tests/test_config.py tests/test_advisor.py
git commit -m "feat: add isolated advisor model service"
```

---

### Task 4: Expose `ask_advisor` and enforce its per-step budget

**Files:**
- Modify: `sparkos/agent/tools/registry.py`
- Modify: `sparkos/agent/step_executor.py`
- Modify: `sparkos/agent/runtime.py`
- Modify: `config/config.py`
- Modify: `config/config.yaml`
- Modify: `tests/test_agent_tools.py`
- Modify: `tests/test_step_executor.py`

**Interfaces:**
- Consumes: `AdvisorService.ask(AdvisorRequest)` from Task 3.
- Produces: `ask_advisor` tool dispatch plus `StepExecutor(tool_call_limits={"ask_advisor": 1})` enforcement.

- [ ] **Step 1: Add failing schema and dispatch tests**

Append to `tests/test_agent_tools.py`:

```python
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
    with patch(
        "sparkos.agent.tools.registry._ADVISOR.ask",
        new=AsyncMock(return_value=expected),
    ) as ask:
        result = await execute_tool(
            "ask_advisor",
            {
                "question": "How should retries change?",
                "context": "Intermittent 503 responses.",
                "attempts": "Fixed delay.",
            },
        )
    self.assertEqual(ask.await_args.args[0].question, "How should retries change?")
    self.assertEqual(json.loads(result)["answer"], "Use full jitter.")
```

- [ ] **Step 2: Add failing budget tests**

Append to `tests/test_step_executor.py` a model turn sequence that requests `ask_advisor` twice and then returns final text. Construct:

```python
executor = StepExecutor(
    client=client,
    tools=[],
    tool_executor=tool_executor,
    tool_call_limits={"ask_advisor": 1},
)
```

Assert the executor calls `tool_executor` once, the second tool result contains `工具调用次数超过单步限制`, and the final execution succeeds. Add a control test proving two calls to an unlisted tool still dispatch normally within `max_tool_rounds`.

- [ ] **Step 3: Implement the advisor tool**

In `sparkos/agent/tools/registry.py`, create `_ADVISOR = AdvisorService.from_config()` and add:

```python
{
    "type": "function",
    "function": {
        "name": "ask_advisor",
        "description": (
            "当当前步骤经过一次具体尝试后仍存在关键技术难题或方案分歧时，"
            "向独立高级模型请求一次建议；建议必须再用当前工具或证据验证。"
        ),
        "parameters": {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "question": {"type": "string", "description": "需要决策的具体问题"},
                "context": {"type": "string", "description": "最小必要上下文和约束"},
                "attempts": {"type": "string", "description": "已经尝试的方法及结果"},
            },
            "required": ["question", "context", "attempts"],
        },
    },
}
```

Dispatch through an async `_ask_advisor(arguments)` helper that constructs `AdvisorRequest`, awaits `_ADVISOR.ask`, and returns `AdvisorResult.to_json()`.

- [ ] **Step 4: Implement generic per-tool step limits**

Extend `StepExecutor.__init__` with:

```python
tool_call_limits: dict[str, int] | None = (None,)
```

Copy and validate the mapping: names must be non-empty strings and limits must be positive integers. At the start of `stream`, create `tool_call_counts: dict[str, int] = {}`. Before `_execute_tool_call`, return `工具调用次数超过单步限制（ask_advisor: 1）` without dispatch when the count has reached its limit; otherwise increment the count and dispatch. Count calls even when the advisor returns a failed result, because the expensive model request was attempted.

Add `max_advisor_calls_per_step: int` to `RuntimeConfig`, load it from YAML, and validate it is positive. Add:

```yaml
runtime:
  max_advisor_calls_per_step: 1
```

In `AgentRuntime`, pass:

```python
tool_call_limits = ({"ask_advisor": rt.max_advisor_calls_per_step},)
```

when constructing the default `StepExecutor`. A caller-supplied `step_executor` remains untouched.

- [ ] **Step 5: Run focused integration tests**

```bash
.venv/bin/python -m pytest tests/test_agent_tools.py tests/test_step_executor.py tests/test_agent_runtime.py tests/test_spark_tools.py -q
.venv/bin/python -m ruff check sparkos/agent/tools/registry.py sparkos/agent/step_executor.py sparkos/agent/runtime.py tests/test_agent_tools.py tests/test_step_executor.py
.venv/bin/python -m ruff format --check sparkos/agent/tools/registry.py sparkos/agent/step_executor.py sparkos/agent/runtime.py tests/test_agent_tools.py tests/test_step_executor.py
```

Expected: advisor dispatches once, a second same-step request is returned to the model as a budget error, the model can still produce final text, and Spark tooling remains green.

- [ ] **Step 6: Commit the advisor tool integration**

```bash
git add config/config.py config/config.yaml sparkos/agent/tools/registry.py sparkos/agent/step_executor.py sparkos/agent/runtime.py tests/test_agent_tools.py tests/test_step_executor.py
git commit -m "feat: expose bounded advisor tool"
```

---

### Task 5: Document usage policy and verify the complete workflow

**Files:**
- Modify: `sparkos/agent/system_prompt.md`
- Modify: `README.md`
- Test: all files under `tests/`

**Interfaces:**
- Consumes: the complete `run_code` and `ask_advisor` implementations.
- Produces: operator setup instructions and a verified end-to-end runtime.

- [ ] **Step 1: Add the primary-Agent usage policy**

Append these rules to `sparkos/agent/system_prompt.md`:

```markdown
5. 需要验证 Python 或 Bash 代码时使用 `run_code`；不要声称沙箱能访问宿主文件或网络。
6. 只有在已经进行一次具体尝试、仍存在关键难题时才使用 `ask_advisor`。提供最小必要上下文和失败证据；advisor 输出是建议，必须通过工具结果、测试或已有证据验证后再采用。
```

- [ ] **Step 2: Document operator setup and observable behavior**

Add a `代码沙箱` section to `README.md` containing:

```bash
docker build --pull -f docker/code-sandbox.Dockerfile -t sparkmind-code-sandbox:latest docker
```

Document that every call has no network, a read-only root filesystem, no repository-root or project-source mount, 256 MiB memory, one CPU, 64 processes, and a maximum 30-second timeout. Document logs at `artifacts/code-runs/{run_id}/output.log`.

Add an `Advisor` section explaining that `config.yaml` uses model alias `advisor`, inherits the main API URL/key unless `SPARKMIND_ADVISOR_BASE_URL` and `SPARKMIND_ADVISOR_API_KEY` are set, and supports:

```bash
export SPARKMIND_ADVISOR_ENABLED=true
export SPARKMIND_ADVISOR_MODEL=advisor
```

State that the gateway must route `advisor` to the intended higher-capability deployment and that each plan step can call it once.

- [ ] **Step 3: Run real sandbox smoke tests**

```bash
.venv/bin/python - <<'PY'
import asyncio
from sparkos.infrastructure.code_sandbox import CodeRunRequest, CodeSandboxRunner

async def main():
    runner = CodeSandboxRunner()
    python_result = await runner.run(CodeRunRequest("python", "print(sum(range(10)))"))
    bash_result = await runner.run(CodeRunRequest("bash", "printf 'sandbox-ok\\n'"))
    assert python_result.status == "succeeded" and python_result.output == "45\n"
    assert bash_result.status == "succeeded" and bash_result.output == "sandbox-ok\n"

asyncio.run(main())
PY
```

Expected: both assertions pass and two artifact directories contain source plus complete logs.

- [ ] **Step 4: Verify isolation with executable probes**

Run a `CodeSandboxRunner` request containing Python that attempts a network connection to `1.1.1.1:53` and reads `/workspace/README.md`. Assert both attempts fail, while `/workspace/main.py` remains readable. Then inspect the constructed Docker command in `tests/test_code_sandbox.py` to confirm no repository or Docker socket mount was introduced.

- [ ] **Step 5: Verify advisor routing with a configured gateway**

Run the app with `SPARKMIND_ADVISOR_MODEL=advisor`, ask it to compare two retry strategies after presenting a failed fixed-delay attempt, and inspect the tool disclosure. Expected: one `ask_advisor` call appears, its JSON result names model `advisor`, and the final response distinguishes the advisor recommendation from locally verified facts.

- [ ] **Step 6: Run the full quality gate**

```bash
.venv/bin/python -m pytest -q
.venv/bin/python -m ruff check .
.venv/bin/python -m ruff format --check .
docker image inspect sparkmind-code-sandbox:latest
git diff --check
```

Expected: all tests pass, Ruff reports no issues, the image exists, and Git reports no whitespace errors.

- [ ] **Step 7: Commit documentation and policy**

```bash
git add README.md sparkos/agent/system_prompt.md
git commit -m "docs: explain code sandbox and advisor usage"
```

---

## Acceptance Checklist

- `TOOL_DEFINITIONS` includes `run_code` and `ask_advisor`, and no longer includes `shell`.
- Python and Bash code execute only in the one-off Docker sandbox.
- Code containers have no network, no repository-root or project-source mount, no Docker socket, a read-only root, dropped capabilities, and fixed CPU/memory/PID/time limits; the current run directory is the only bind mount.
- Complete logs remain local while model-visible output is capped at 20,000 bytes.
- Timeout and cancellation remove the named container; cancellation propagates to the runtime.
- Advisor uses model alias `advisor` through a separate client and receives no tool access or implicit conversation history.
- A plan step can invoke advisor once and can continue to a final answer after receiving advice or a budget error.
- Existing Spark, planning, runtime persistence, and TUI tests remain green.
- README contains the exact sandbox build command, artifact paths, advisor environment variables, and gateway alias requirement.

## Deferred Work

The first release intentionally excludes package installation, network-enabled sandboxes, repository mounts, extra languages, long-lived kernels, interactive terminals, advisor auto-routing, multiple advisor personas, token accounting, and cross-step advisor memory. Each changes the trust or cost model and should be designed as a separate increment after observing actual usage.
