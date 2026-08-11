"""Tests for sandboxed code execution models and runner."""

from __future__ import annotations

import json
import unittest
from unittest.mock import AsyncMock, patch

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


class CodeSandboxRunnerTests(unittest.IsolatedAsyncioTestCase):
    async def test_python_builds_secure_docker_command(self) -> None:
        from sparkos.infrastructure.code_sandbox.runner import CodeSandboxRunner

        with patch(
            "asyncio.create_subprocess_exec",
            new_callable=AsyncMock,
        ) as create_process:
            proc_mock = AsyncMock()
            proc_mock.communicate.return_value = (b"45\n", b"")
            proc_mock.returncode = 0
            create_process.return_value = proc_mock

            from sparkos.infrastructure.code_sandbox.models import CodeRunRequest

            runner = CodeSandboxRunner()
            result = await runner.run(
                CodeRunRequest("python", "print(sum(range(10)))")
            )

        command = create_process.await_args.args
        self.assertEqual(command[:3], ("docker", "run", "--rm"))
        self.assertIn("--network", command)
        self.assertEqual(
            command[command.index("--network") + 1], "none"
        )
        self.assertIn("--read-only", command)
        self.assertIn("--cap-drop", command)
        self.assertEqual(
            command[command.index("--cap-drop") + 1], "ALL"
        )
        self.assertIn("no-new-privileges", command)
        self.assertIn("--pids-limit", command)
        self.assertIn("64", command)
        self.assertIn("--memory", command)
        self.assertIn("256m", command)
        self.assertIn("--cpus", command)
        self.assertIn("1.0", command)
        self.assertIn("--pull", command)
        self.assertEqual(
            command[command.index("--pull") + 1], "never"
        )
        self.assertNotIn("/var/run/docker.sock", " ".join(command))
        mount_spec = command[command.index("--mount") + 1]
        job_dir = CodeRunRequest("python", "x").language  # trigger dir creation
        self.assertNotEqual(mount_spec.split("src=")[1].split(",")[0], "")
        self.assertEqual(command[-3:], ("python", "-I", "/workspace/main.py"))

    async def test_bash_uses_correct_interpreter(self) -> None:
        from sparkos.infrastructure.code_sandbox.runner import CodeSandboxRunner

        with patch(
            "asyncio.create_subprocess_exec",
            new_callable=AsyncMock,
        ) as create_process:
            proc_mock = AsyncMock()
            proc_mock.communicate.return_value = (b"ok\n", b"")
            proc_mock.returncode = 0
            create_process.return_value = proc_mock

            from sparkos.infrastructure.code_sandbox.models import CodeRunRequest

            runner = CodeSandboxRunner()
            result = await runner.run(
                CodeRunRequest("bash", "printf 'sandbox-ok\\n'")
            )

        command = create_process.await_args.args
        self.assertEqual(command[-3:], ("/bin/bash", "--noprofile", "/workspace/main.sh"))

    async def test_timeout_returns_timed_out_status(self) -> None:
        from sparkos.infrastructure.code_sandbox.runner import CodeSandboxRunner
        import asyncio

        runner = CodeSandboxRunner()

        with patch(
            "asyncio.create_subprocess_exec",
            new_callable=AsyncMock,
        ) as create_process:
            proc_mock = AsyncMock()
            # Simulate a long-running process that doesn't finish before timeout
            async def slow_communicate(*args, **kwargs):
                await asyncio.sleep(10)
                return (b"", b"")

            proc_mock.communicate.side_effect = slow_communicate
            create_process.return_value = proc_mock

            from sparkos.infrastructure.code_sandbox.models import CodeRunRequest

            result = await runner.run(
                CodeRunRequest("python", "print('slow')", timeout_seconds=1)
            )

        self.assertEqual(result.status, "timed_out")
        self.assertLessEqual(len(result.output.encode("utf-8")), 20_000)

    async def test_cancellation_cleans_up_container(self) -> None:
        from sparkos.infrastructure.code_sandbox.runner import CodeSandboxRunner
        import asyncio

        runner = CodeSandboxRunner()

        with patch(
            "asyncio.create_subprocess_exec",
            new_callable=AsyncMock,
        ) as create_process:
            # Track subprocesses created for cleanup verification
            procs = []

            async def fake_exec(*args, **kwargs):
                p = AsyncMock()
                p.communicate = AsyncMock(
                    side_effect=asyncio.CancelledError()
                )
                p.returncode = -1
                procs.append(args)
                return p

            create_process.side_effect = fake_exec

            from sparkos.infrastructure.code_sandbox.models import CodeRunRequest

            with self.assertRaises(asyncio.CancelledError):
                await runner.run(
                    CodeRunRequest("python", "print(1)", timeout_seconds=30)
                )
