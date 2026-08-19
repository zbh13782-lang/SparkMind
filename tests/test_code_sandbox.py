"""Tests for sandboxed code execution models and runner."""

from __future__ import annotations

import asyncio
import json
import unittest
from pathlib import Path
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

            runner = CodeSandboxRunner()
            result = await runner.run(CodeRunRequest("python", "print(sum(range(10)))"))

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
        self.assertRegex(mount_spec, r"^type=bind,src=.*?,dst=/workspace,readonly$")
        mount_src = mount_spec.split("src=")[1].split(",")[0]
        repo_root = str(Path(__file__).resolve().parents[3])
        self.assertNotEqual(mount_src, repo_root)
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

            runner = CodeSandboxRunner()
            await runner.run(CodeRunRequest("bash", "printf 'sandbox-ok\\n'"))

        command = create_process.await_args.args
        self.assertEqual(command[-3:], ("/bin/bash", "--noprofile", "/workspace/main.sh"))

    async def test_timeout_returns_timed_out_status(self) -> None:
        from sparkos.infrastructure.code_sandbox.runner import CodeSandboxRunner

        runner = CodeSandboxRunner()

        with patch(
            "asyncio.create_subprocess_exec",
            new_callable=AsyncMock,
        ) as create_process:
            proc_mock = AsyncMock()
            proc_mock.communicate = AsyncMock(side_effect=TimeoutError())
            create_process.return_value = proc_mock

            result = await runner.run(CodeRunRequest("python", "print('slow')", timeout_seconds=1))

        self.assertEqual(result.status, "timed_out")
        self.assertLessEqual(len(result.output.encode("utf-8")), 20_000)

    async def test_cancellation_cleans_up_container(self) -> None:
        from sparkos.infrastructure.code_sandbox.runner import CodeSandboxRunner

        runner = CodeSandboxRunner()

        cleanup_cmds: list[list[str]] = []

        original_cleanup = CodeSandboxRunner._cleanup_container

        async def tracking_cleanup(self, name: str) -> None:
            for cmd in (
                ("docker", "stop", "--time", "1", name),
                ("docker", "rm", "-f", name),
            ):
                cleanup_cmds.append(list(cmd))
                p = await asyncio.create_subprocess_exec(
                    *cmd,
                    stdout=asyncio.subprocess.DEVNULL,
                    stderr=asyncio.subprocess.DEVNULL,
                )
                try:
                    await p.communicate()
                except asyncio.CancelledError:
                    pass

        with (
            patch.object(CodeSandboxRunner, "_cleanup_container", tracking_cleanup),
            patch(
                "asyncio.create_subprocess_exec",
                new_callable=AsyncMock,
            ) as create_process,
        ):
            proc_mock = AsyncMock()
            proc_mock.communicate = AsyncMock(side_effect=asyncio.CancelledError())
            create_process.return_value = proc_mock

            with self.assertRaises(asyncio.CancelledError):
                await runner.run(CodeRunRequest("python", "print(1)", timeout_seconds=30))

        self.assertEqual(len(cleanup_cmds), 2)
        self.assertEqual(cleanup_cmds[0][:4], ["docker", "stop", "--time", "1"])
        container_name = cleanup_cmds[0][-1]
        self.assertEqual(cleanup_cmds[1], ["docker", "rm", "-f", container_name])

    async def test_os_error_returns_failed_status(self) -> None:
        from sparkos.infrastructure.code_sandbox.runner import CodeSandboxRunner

        runner = CodeSandboxRunner()

        with patch(
            "asyncio.create_subprocess_exec",
            side_effect=OSError("Docker not found"),
        ):
            result = await runner.run(CodeRunRequest("python", "print(1)"))

        self.assertEqual(result.status, "failed")
        self.assertIsNone(result.exit_code)
