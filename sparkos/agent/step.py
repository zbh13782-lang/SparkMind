"""Execution state and results for immutable plan steps."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any


class StepStatus(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    VERIFYING = "verifying"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    BLOCKED = "blocked"
    SKIPPED = "skipped"
    CANCELLED = "cancelled"


@dataclass(frozen=True)
class ArtifactRef:
    uri: str
    kind: str


@dataclass(frozen=True)
class StepResult:
    success: bool
    output: str
    evidence: tuple[str, ...] = ()
    artifacts: tuple[ArtifactRef, ...] = ()
    error: str | None = None


@dataclass(frozen=True)
class StepVerification:
    passed: bool
    reason: str
    retryable: bool
    evidence: tuple[str, ...] = ()
    error: str | None = None


@dataclass
class StepRun:
    step_id: str
    status: StepStatus = StepStatus.PENDING
    attempt_count: int = 0
    result: StepResult | None = None
    error: str | None = None
    transcript: list[dict[str, Any]] = field(default_factory=list)
    verification: StepVerification | None = None
    verification_history: list[StepVerification] = field(default_factory=list)
    result_history: list[StepResult] = field(default_factory=list)
    transcript_history: list[list[dict[str, Any]]] = field(default_factory=list)

    def start(self) -> None:
        self.status = StepStatus.RUNNING
        self.attempt_count += 1
        self.error = None

    def succeed(self, result: StepResult) -> None:
        self.status = StepStatus.SUCCEEDED
        self.result = result
        self.error = None

    def begin_verification(self, result: StepResult) -> None:
        self.status = StepStatus.VERIFYING
        self.result = result
        self.error = None

    def record_verification(self, verification: StepVerification) -> None:
        self.verification = verification
        self.verification_history.append(verification)

    def prepare_retry(self) -> None:
        if self.result is not None:
            self.result_history.append(self.result)
        if self.transcript:
            self.transcript_history.append(deepcopy(self.transcript))
        self.status = StepStatus.PENDING
        self.result = None
        self.error = None
        self.transcript = []
        self.verification = None

    def fail(self, error: str, result: StepResult | None = None) -> None:
        self.status = StepStatus.FAILED
        self.result = result
        self.error = error

    def block(self, reason: str) -> None:
        self.status = StepStatus.BLOCKED
        self.error = reason

    def cancel(self) -> None:
        self.status = StepStatus.CANCELLED

    def record_transcript(
        self,
        transcript: tuple[dict[str, Any], ...] | list[dict[str, Any]],
    ) -> None:
        self.transcript = deepcopy(list(transcript))


__all__ = [
    "ArtifactRef",
    "StepResult",
    "StepRun",
    "StepStatus",
    "StepVerification",
]
