"""LLM-backed task planner with validated structured output."""

from __future__ import annotations

import json
from typing import Any, Protocol

from sparkos.agent.planner import (
    ClarificationRequest,
    Plan,
    Planner,
    PlanningContext,
    PlanStep,
)
from sparkos.agent.step import StepResult, StepRun
from sparkos.agent.task import AgentTask

_PLANNER_PROMPT_TEMPLATE = """你是 Agent 的任务规划器。你的职责仅是判断任务是否需要多步规划，并在需要时给出最小、可执行的依赖图；不要执行任务。

只输出一个 JSON 对象，不要输出 Markdown 或解释：
{
  "should_plan": true,
  "clarification_question": null,
  "steps": [
    {
      "id": "s1",
      "description": "明确、可执行的动作",
      "depends_on": [],
      "success_criteria": "可验证的完成条件"
    }
  ]
}

规则：
- 如果缺少完成任务所必需、且无法从上下文推断的用户信息，返回 {"should_plan": false, "clarification_question": "一个简洁、具体的问题", "steps": []}。
- 不要追问可选偏好或能合理默认的信息；简单问答、单次工具调用或一步即可完成的任务，返回 {"should_plan": false, "clarification_question": null, "steps": []}。
- 需要规划时 clarification_question 必须为 null。
- 只有确实需要多个相互依赖动作时才规划。
- 最多 {max_steps} 步；每步只描述一个动作。
- 每步必须给出明确、可验证的 success_criteria。
- id 在当前计划中唯一；depends_on 只能引用当前计划中的步骤 id。
- 计划必须是无环图，避免重复、空泛或无法验证的步骤。
- 技能和工具只是可用能力，不要编造未列出的能力。"""

_REPLAN_PROMPT_TEMPLATE = """你是 Agent 的重规划器。当前 Plan 中的一个步骤在有界重试后仍未通过验证。请返回一份完整的替代 Plan，不要执行任务。

只输出一个 JSON 对象，格式与初始 Planner 相同：
{
  "should_plan": true,
  "steps": [
    {
      "id": "s1",
      "description": "明确、可执行的动作",
      "depends_on": [],
      "success_criteria": "可验证的完成条件"
    }
  ]
}

规则：
- 返回完整 Plan，最多 {max_steps} 步，必须是无环图。
- 已成功步骤如仍需要，必须原样保留 id、description、depends_on 和 success_criteria，以便复用结果。
- 替换失败步骤时使用新 id，修正后续依赖。
- 针对 failure_reason 更换方法、数据源或拆分方式，不要简单重复失败动作。
- 技能和工具只是可用能力，不要编造未列出的能力。"""


class PlanningModel(Protocol):
    async def chat_once(
        self,
        messages: list[dict],
        *,
        json_object: bool = False,
    ) -> str: ...


class LLMPlanner(Planner):
    """Ask an LLM for a plan and fail open when its output is invalid."""

    def __init__(self, model: PlanningModel, max_steps: int = 12) -> None:
        if max_steps <= 0:
            raise ValueError("max_steps 必须大于 0")
        self.model = model
        self.max_steps = max_steps

    async def create_plan(
        self,
        task: AgentTask,
        context: PlanningContext,
    ) -> Plan | ClarificationRequest | None:
        try:
            request = self._build_request(task, context)
            response = await self.model.chat_once(request, json_object=True)
            payload = self._parse_payload(response)
            return self._build_initial_decision(task, payload)
        except Exception:  # noqa: BLE001
            # Planning is an optional optimization. Invalid output or a planning
            # model failure must not prevent Runtime from executing the task.
            return None

    async def revise_plan(
        self,
        task: AgentTask,
        context: PlanningContext,
        current_plan: Plan,
        step_runs: dict[str, StepRun],
        failed_step: PlanStep,
        reason: str,
    ) -> Plan | None:
        try:
            request = self._build_replan_request(
                task,
                context,
                current_plan,
                step_runs,
                failed_step,
                reason,
            )
            response = await self.model.chat_once(request, json_object=True)
            payload = self._parse_payload(response)
            return self._build_plan(
                task,
                payload,
                version=current_plan.version + 1,
                source="replan",
            )
        except Exception:  # noqa: BLE001
            return None

    def _build_request(
        self,
        task: AgentTask,
        context: PlanningContext,
    ) -> list[dict[str, str]]:
        payload = {
            "goal": task.goal,
            "input": task.input,
            "summary": context.summary,
            "recent_messages": [
                message.to_api_dict() for message in context.recent_messages
            ],
            "skills": [
                {
                    "name": skill.name,
                    "description": skill.description,
                }
                for skill in context.skills
            ],
            "tools": list(context.tool_names),
        }
        return [
            {
                "role": "system",
                "content": _PLANNER_PROMPT_TEMPLATE.replace(
                    "{max_steps}", str(self.max_steps)
                ),
            },
            {
                "role": "user",
                "content": json.dumps(payload, ensure_ascii=False),
            },
        ]

    def _build_replan_request(
        self,
        task: AgentTask,
        context: PlanningContext,
        current_plan: Plan,
        step_runs: dict[str, StepRun],
        failed_step: PlanStep,
        reason: str,
    ) -> list[dict[str, str]]:
        payload = {
            "goal": task.goal,
            "input": task.input,
            "summary": context.summary,
            "recent_messages": [
                message.to_api_dict() for message in context.recent_messages
            ],
            "skills": [
                {"name": skill.name, "description": skill.description}
                for skill in context.skills
            ],
            "tools": list(context.tool_names),
            "current_plan": self._serialize_plan(current_plan),
            "step_runs": {
                step_id: self._serialize_run(run) for step_id, run in step_runs.items()
            },
            "failed_step": self._serialize_step(failed_step),
            "failure_reason": reason,
        }
        return [
            {
                "role": "system",
                "content": _REPLAN_PROMPT_TEMPLATE.replace(
                    "{max_steps}", str(self.max_steps)
                ),
            },
            {
                "role": "user",
                "content": json.dumps(payload, ensure_ascii=False, default=repr),
            },
        ]

    @staticmethod
    def _parse_payload(response: str) -> dict[str, Any]:
        text = response.strip()
        # 部分模型即使要求 json_object 仍会裹一层 markdown 代码围栏。
        if text.startswith("```"):
            text = text[3:]
            if text[:4].lower() == "json":
                text = text[4:]
            fence_end = text.rfind("```")
            if fence_end != -1:
                text = text[:fence_end]
            text = text.strip()
        payload = json.loads(text)
        if not isinstance(payload, dict):
            raise TypeError(f"Planner 返回了非对象 JSON: {type(payload).__name__}")
        return payload

    def _build_plan(
        self,
        task: AgentTask,
        payload: dict[str, Any],
        *,
        version: int = 1,
        source: str = "planner",
    ) -> Plan | None:
        should_plan = payload.get("should_plan")
        if not isinstance(should_plan, bool):
            raise TypeError("should_plan 必须是布尔值")
        if not should_plan:
            return None

        raw_steps = payload.get("steps")
        if not isinstance(raw_steps, list) or not raw_steps:
            raise ValueError("需要规划时 steps 不能为空")
        if len(raw_steps) > self.max_steps:
            raise ValueError(f"计划步骤不能超过 {self.max_steps}")

        steps: list[PlanStep] = []
        seen_ids: set[str] = set()
        for raw_step in raw_steps:
            if not isinstance(raw_step, dict):
                raise TypeError("每个计划步骤必须是对象")

            step_id = raw_step.get("id")
            description = raw_step.get("description")
            depends_on = raw_step.get("depends_on", [])
            success_criteria = raw_step.get("success_criteria")
            if not isinstance(step_id, str) or not step_id.strip():
                raise ValueError("步骤 id 不能为空")
            step_id = step_id.strip()
            if step_id in seen_ids:
                raise ValueError(f"重复的步骤 id：{step_id}")
            if not isinstance(description, str) or not description.strip():
                raise ValueError(f"步骤 {step_id} 的描述不能为空")
            if not isinstance(depends_on, list) or not all(
                isinstance(dependency, str) and dependency.strip()
                for dependency in depends_on
            ):
                raise ValueError(f"步骤 {step_id} 的 depends_on 无效")
            if success_criteria is not None and (
                not isinstance(success_criteria, str) or not success_criteria.strip()
            ):
                raise ValueError(f"步骤 {step_id} 的 success_criteria 无效")

            normalized_dependencies = tuple(
                dependency.strip() for dependency in depends_on
            )
            normalized_criteria = (
                success_criteria.strip()
                if success_criteria is not None
                else f"完成步骤：{description.strip()}"
            )
            steps.append(
                PlanStep(
                    id=step_id,
                    description=description.strip(),
                    depends_on=normalized_dependencies,
                    success_criteria=normalized_criteria,
                )
            )
            seen_ids.add(step_id)

        self._validate_graph(steps)
        return Plan(
            task_id=task.id,
            steps=tuple(steps),
            version=version,
            source=source,
        )

    def _build_initial_decision(
        self,
        task: AgentTask,
        payload: dict[str, Any],
    ) -> Plan | ClarificationRequest | None:
        should_plan = payload.get("should_plan")
        if not isinstance(should_plan, bool):
            raise TypeError("should_plan 必须是布尔值")

        clarification = payload.get("clarification_question")
        if not should_plan:
            if clarification is None:
                return None
            if not isinstance(clarification, str) or not clarification.strip():
                raise ValueError("clarification_question 必须是非空字符串或 null")
            return ClarificationRequest(question=clarification.strip())

        if clarification is not None:
            raise ValueError("需要规划时 clarification_question 必须为 null")
        return self._build_plan(task, payload)

    @staticmethod
    def _serialize_step(step: PlanStep) -> dict[str, Any]:
        return {
            "id": step.id,
            "description": step.description,
            "depends_on": list(step.depends_on),
            "success_criteria": step.success_criteria,
        }

    @classmethod
    def _serialize_plan(cls, plan: Plan) -> dict[str, Any]:
        return {
            "id": plan.id,
            "version": plan.version,
            "source": plan.source,
            "steps": [cls._serialize_step(step) for step in plan.steps],
        }

    @classmethod
    def _serialize_run(cls, run: StepRun) -> dict[str, Any]:
        return {
            "status": run.status.value,
            "attempt_count": run.attempt_count,
            "result": cls._serialize_result(run.result),
            "error": run.error,
        }

    @staticmethod
    def _serialize_result(result: StepResult | None) -> dict[str, Any] | None:
        if result is None:
            return None
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

    # 三色标记判定图内是否有环
    @staticmethod
    def _validate_graph(steps: list[PlanStep]) -> None:
        step_ids = {step.id for step in steps}
        graph = {step.id: step.depends_on for step in steps}

        for step in steps:
            for dependency in step.depends_on:
                if dependency not in step_ids:
                    raise ValueError(f"步骤 {step.id} 引用了未知依赖：{dependency}")
                if dependency == step.id:
                    raise ValueError(f"步骤 {step.id} 不能依赖自身")

        visiting: set[str] = set()
        visited: set[str] = set()

        def visit(step_id: str) -> None:
            if step_id in visiting:
                raise ValueError("计划依赖图存在环")
            if step_id in visited:
                return
            visiting.add(step_id)
            for dependency in graph[step_id]:
                visit(dependency)
            visiting.remove(step_id)
            visited.add(step_id)

        for step_id in graph:
            visit(step_id)


__all__ = ["LLMPlanner", "PlanningModel"]
