"""Validated requests and structured results for advisor consultations."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from typing import Literal

from config.config import AdvisorConfig

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
