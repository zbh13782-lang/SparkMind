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
