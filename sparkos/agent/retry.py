"""Deterministic retry decisions for rejected Step attempts."""

from __future__ import annotations

from dataclasses import dataclass

from sparkos.agent.step import StepRun, StepVerification


@dataclass(frozen=True)
class RetryPolicy:
    max_attempts: int = 2

    def __post_init__(self) -> None:
        if self.max_attempts < 1:
            raise ValueError("max_attempts 必须大于等于 1")
        if self.max_attempts > 2:
            raise ValueError("每个步骤最多允许 2 次尝试")

    def should_retry(
        self,
        run: StepRun,
        verification: StepVerification,
    ) -> bool:
        return (
            not verification.passed
            and verification.retryable
            and run.attempt_count < self.max_attempts
        )


__all__ = ["RetryPolicy"]
