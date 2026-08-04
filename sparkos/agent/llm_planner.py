"""LLM-backed task planner with validated structured output."""

from __future__ import annotations

import json
from typing import Any, Protocol

from sparkos.agent.planner import Plan, Planner, PlanningContext, PlanStep
from sparkos.agent.task import AgentTask

_PLANNER_PROMPT_TEMPLATE = """你是 Agent 的任务规划器。你的职责仅是判断任务是否需要多步规划，并在需要时给出最小、可执行的依赖图；不要执行任务。

只输出一个 JSON 对象，不要输出 Markdown 或解释：
{
  "should_plan": true,
  "steps": [
    {"id": "s1", "description": "明确、可验证的动作", "depends_on": []}
  ]
}

规则：
- 简单问答、单次工具调用或一步即可完成的任务，返回 {"should_plan": false, "steps": []}。
- 只有确实需要多个相互依赖动作时才规划。
- 最多 {max_steps} 步；每步只描述一个动作。
- id 在当前计划中唯一；depends_on 只能引用当前计划中的步骤 id。
- 计划必须是无环图，避免重复、空泛或无法验证的步骤。
- 技能和工具只是可用能力，不要编造未列出的能力。"""


class PlanningModel(Protocol):
    async def chat_once(self, messages: list[dict]) -> str: ...


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
    ) -> Plan | None:
        try:
            request = self._build_request(task, context)
            response = await self.model.chat_once(request)
            payload = self._parse_payload(response)
            return self._build_plan(task, payload)
        except Exception:  # noqa: BLE001
            # Planning is an optional optimization. Invalid output or a planning
            # model failure must not prevent Runtime from executing the task.
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
            "skills": list(context.skill_names),
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

    @staticmethod
    def _parse_payload(response: str) -> dict[str, Any]:
        text = response.strip()
        if text.startswith("```"):
            lines = text.splitlines()
            if lines and lines[-1].strip() == "```":
                lines = lines[1:-1]
                text = "\n".join(lines).strip()

        try:
            payload = json.loads(text)
        except json.JSONDecodeError:
            start = text.find("{")
            end = text.rfind("}")
            if start == -1 or end <= start:
                raise
            payload = json.loads(text[start : end + 1])

        if not isinstance(payload, dict):
            raise TypeError("Planner 输出必须是 JSON 对象")
        return payload

    def _build_plan(
        self,
        task: AgentTask,
        payload: dict[str, Any],
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

            normalized_dependencies = [dependency.strip() for dependency in depends_on]
            steps.append(
                PlanStep(
                    id=step_id,
                    description=description.strip(),
                    depends_on=normalized_dependencies,
                )
            )
            seen_ids.add(step_id)

        self._validate_graph(steps)
        return Plan(task_id=task.id, steps=steps)

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
