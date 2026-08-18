"""Docker-backed sandbox runner for Python and Bash snippets."""

import asyncio
import os
import uuid
from dataclasses import dataclass
from pathlib import Path

from .models import (
    CodeRunRequest,
    CodeRunResult,
)

_SCRIPT_MAP: dict[str, tuple[str, tuple[str, ...]]] = {
    "python": ("main.py", ("python", "-I", "/workspace/main.py")),
    "bash": ("main.sh", ("/bin/bash", "--noprofile", "/workspace/main.sh")),
}


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


class CodeSandboxRunner:
    def __init__(self, config: CodeSandboxConfig | None = None) -> None:
        self.config = config or CodeSandboxConfig.from_env()

    async def run(self, request: CodeRunRequest) -> CodeRunResult:
        script_name, language_command = _SCRIPT_MAP[request.language]
        run_id = uuid.uuid4().hex
        started = asyncio.get_event_loop().time()

        job_dir = self.config.repo_root / "artifacts" / "code-runs" / run_id
        job_dir.mkdir(parents=True, exist_ok=True)
        job_dir_path = Path(job_dir)
        script_path = job_dir_path / script_name
        script_path.write_text(request.code, encoding="utf-8")
        script_path.chmod(0o644)

        log_filepath = job_dir_path / "output.log"
        log_file = log_filepath.open("w", encoding="utf-8")

        stdin_r, stdin_w = os.pipe()
        stdin_w_fh = os.fdopen(stdin_w, "wb")
        container_name = f"sandbox-{run_id}"
        command = self._build_docker_command(container_name, job_dir_path, language_command)

        process: asyncio.subprocess.Process | None = None
        try:
            process = await asyncio.create_subprocess_exec(
                *command,
                stdin=stdin_r,
                stdout=log_file.fileno(),
                stderr=asyncio.subprocess.STDOUT,
            )
        except OSError:
            log_file.write("Docker 启动失败或不可用\n")
            log_file.close()
            self._close_fd(stdin_r)
            try:
                stdin_w_fh.close()
            except OSError:
                pass
            return CodeRunResult(
                run_id=run_id,
                status="failed",
                exit_code=None,
                duration_seconds=(asyncio.get_event_loop().time() - started),
                log_path=str(log_filepath),
                output="",
                output_truncated=False,
            )

        stdin_w_fh.write(request.stdin.encode("utf-8"))
        stdin_w_fh.close()

        try:
            async with asyncio.timeout(request.timeout_seconds):
                await process.communicate()
        except TimeoutError:
            await self._cleanup_container(container_name)
            duration = asyncio.get_event_loop().time() - started
            log_file.close()
            self._close_fd(stdin_r)
            output = self._read_output(log_filepath)
            return CodeRunResult(
                run_id=run_id,
                status="timed_out",
                exit_code=None,
                duration_seconds=duration,
                log_path=str(log_filepath),
                output=output,
                output_truncated=len(output.encode("utf-8")) < log_filepath.stat().st_size,
            )
        except asyncio.CancelledError:
            await self._cleanup_container(container_name)
            log_file.close()
            self._close_fd(stdin_r)
            raise

        duration = asyncio.get_event_loop().time() - started
        log_file.close()
        self._close_fd(stdin_r)
        log_size = log_filepath.stat().st_size
        output = self._read_output(log_filepath)

        return CodeRunResult(
            run_id=run_id,
            status="succeeded" if process.returncode == 0 else "failed",
            exit_code=process.returncode,
            duration_seconds=duration,
            log_path=str(log_filepath),
            output=output,
            output_truncated=len(output.encode("utf-8")) < log_size,
        )

    @staticmethod
    def _read_output(log_path: Path) -> str:
        try:
            raw = log_path.read_bytes()
        except OSError:
            return ""
        max_bytes = 20_000
        if len(raw) > max_bytes:
            return raw[-max_bytes:].decode("utf-8", errors="replace")
        return raw.decode("utf-8", errors="replace")

    @staticmethod
    def _close_fd(fd: int) -> None:
        try:
            os.close(fd)
        except OSError:
            pass

    @staticmethod
    async def _cleanup_container(name: str) -> None:
        for cmd in (
            ("docker", "stop", "--time", "1", name),
            ("docker", "rm", "-f", name),
        ):
            try:
                p = await asyncio.create_subprocess_exec(
                    *cmd,
                    stdout=asyncio.subprocess.DEVNULL,
                    stderr=asyncio.subprocess.DEVNULL,
                )
                await p.communicate()
            except OSError:
                pass

    def _build_docker_command(
        self,
        container_name: str,
        job_dir: Path,
        language_command: tuple[str, ...],
    ) -> list[str]:
        return [
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
            str(self.config.pids_limit),
            "--memory",
            self.config.memory,
            "--cpus",
            self.config.cpus,
            "--tmpfs",
            "/tmp:rw,nosuid,nodev,size=64m",
            "--mount",
            f"type=bind,src={job_dir},dst={self.config.container_workspace},readonly",
            "--workdir",
            self.config.container_workspace,
            self.config.image,
            *language_command,
        ]
