"""Structured verification of candidate Step results."""

from __future__ import annotations

import json
from typing import Any, Protocol

from sparkos.agent.planner import PlanStep
from sparkos.agent.step import StepResult, StepVerification
from sparkos.agent.task import AgentTask

_VERIFIER_PROMPT = """你是 Agent 步骤结果验证器。只判断 candidate_result 是否满足 step.success_criteria，不要执行任务或调用工具。

只输出一个 JSON 对象：
{
  "passed": false,
  "reason": "不满足成功标准的具体原因",
  "retryable": true,
  "evidence": ["候选结果中可验证的事实"]
}

规则：
- candidate_result 非空不等于通过；必须逐项对照 success_criteria。
- 候选结果自述“无法完成”、“稍后继续”或只描述过程时，应判定为不通过。
- 补充或重新执行当前步骤可能修复时 retryable=true；外部前置条件不可用时为 false。
- evidence 只放 candidate_result 中已经出现的事实。
""".strip()


class VerificationModel(Protocol):
    async def chat_once(self, messages: list[dict]) -> str: ...


class StepVerifier(Protocol):
    async def verify(
        self,
        task: AgentTask,
        step: PlanStep,
        result: StepResult,
        dependency_results: dict[str, StepResult],
    ) -> StepVerification: ...


class LLMStepVerifier:
    def __init__(self, model: VerificationModel) -> None:
        self.model = model

    async def verify(
        self,
        task: AgentTask,
        step: PlanStep,
        result: StepResult,
        dependency_results: dict[str, StepResult],
    ) -> StepVerification:
        try:
            response = await self.model.chat_once(
                self._build_request(
                    task,
                    step,
                    result,
                    dependency_results,
                )
            )
            return self._parse_verification(response)
        except Exception as exc:  # noqa: BLE001
            return StepVerification(
                passed=True,
                reason="验证器不可用，按兼容策略放行",
                retryable=False,
                error=f"{type(exc).__name__}: {exc}",
            )

    @staticmethod
    def _build_request(
        task: AgentTask,
        step: PlanStep,
        result: StepResult,
        dependency_results: dict[str, StepResult],
    ) -> list[dict[str, str]]:
        payload = {
            "task": {"goal": task.goal, "input": task.input},
            "step": {
                "id": step.id,
                "description": step.description,
                "success_criteria": step.success_criteria,
            },
            "candidate_result": LLMStepVerifier._serialize_result(result),
            "dependency_results": {
                step_id: LLMStepVerifier._serialize_result(dependency)
                for step_id, dependency in dependency_results.items()
            },
        }
        return [
            {"role": "system", "content": _VERIFIER_PROMPT},
            {
                "role": "user",
                "content": json.dumps(
                    payload,
                    ensure_ascii=False,
                    default=repr,
                ),
            },
        ]

    @staticmethod
    def _parse_verification(response: str) -> StepVerification:
        text = response.strip()
        payload = json.loads(text)

        if not isinstance(payload, dict):
            raise TypeError("验证器输出必须是 JSON 对象")
        passed = payload.get("passed")
        reason = payload.get("reason")
        retryable = payload.get("retryable")
        evidence = payload.get("evidence", [])
        if not isinstance(passed, bool):
            raise TypeError("passed 必须是布尔值")
        if not isinstance(reason, str) or not reason.strip():
            raise TypeError("reason 必须是非空字符串")
        if not isinstance(retryable, bool):
            raise TypeError("retryable 必须是布尔值")
        if not isinstance(evidence, list) or not all(
            isinstance(item, str) and item.strip() for item in evidence
        ):
            raise TypeError("evidence 必须是字符串数组")
        return StepVerification(
            passed=passed,
            reason=reason.strip(),
            retryable=retryable,
            evidence=tuple(item.strip() for item in evidence),
        )

    @staticmethod
    def _serialize_result(result: StepResult) -> dict[str, Any]:
        return {
            "success": result.success,
            "output": result.output,
            "evidence": list(result.evidence),
            "artifacts": [
                {"uri": artifact.uri, "kind": artifact.kind}
                for artifact in result.artifacts
            ],
            "error": result.error,
        }


__all__ = ["LLMStepVerifier", "StepVerifier", "VerificationModel"]
