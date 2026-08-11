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
