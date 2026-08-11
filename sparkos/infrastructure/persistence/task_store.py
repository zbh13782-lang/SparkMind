"""Atomic JSON persistence for task execution snapshots."""

from __future__ import annotations

import json
import re
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from sparkos.agent.planner import Plan, PlanStep
from sparkos.agent.step import (
    ArtifactRef,
    StepResult,
    StepRun,
)
from sparkos.agent.task import AgentTask

TASKS_DIR = Path(__file__).resolve().parents[3] / ".sparkmind" / "tasks"
_SAFE_TASK_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}\Z")


class JsonTaskStore:
    def __init__(self, root: Path | str = TASKS_DIR) -> None:
        self.root = Path(root)

    def save(
        self,
        task: AgentTask,
        plan: Plan | None,
        step_runs: dict[str, StepRun],
    ) -> None:
        if _SAFE_TASK_ID.fullmatch(task.id) is None:
            raise ValueError("task id 只能包含字母、数字、点、下划线和连字符")
        self.root.mkdir(parents=True, exist_ok=True)
        target = self.root / f"{task.id}.json"
        temporary = self.root / f".{task.id}.{uuid.uuid4().hex}.tmp"
        serialized_plan = self._serialize_plan(plan) if plan is not None else None
        plan_history: list[dict[str, Any]] = []
        if target.is_file():
            previous = json.loads(target.read_text(encoding="utf-8"))
            plan_history = list(previous.get("plan_history", []))
            previous_plan = previous.get("plan")
            if isinstance(previous_plan, dict) and previous_plan != serialized_plan:
                plan_history.append(
                    {
                        "archived_at": previous.get("saved_at"),
                        "plan": previous_plan,
                        "step_runs": previous.get("step_runs", {}),
                    }
                )
        payload = {
            "saved_at": datetime.now(UTC).isoformat(),
            "task": self._serialize_task(task),
            "plan": serialized_plan,
            "step_runs": {step_id: self._serialize_step_run(run) for step_id, run in step_runs.items()},
            "plan_history": plan_history,
        }
        try:
            temporary.write_text(
                json.dumps(payload, ensure_ascii=False, indent=2, default=repr),
                encoding="utf-8",
            )
            temporary.replace(target)
        finally:
            if temporary.exists():
                temporary.unlink()

    @staticmethod
    def _serialize_task(task: AgentTask) -> dict[str, Any]:
        return {
            "id": task.id,
            "goal": task.goal,
            "input": task.input,
            "status": task.status.value,
            "parent_task_id": task.parent_task_id,
            "active_plan_id": task.active_plan_id,
            "result": task.result,
            "error": task.error,
            "clarification_question": task.clarification_question,
        }

    @classmethod
    def _serialize_plan(cls, plan: Plan) -> dict[str, Any]:
        return {
            "id": plan.id,
            "task_id": plan.task_id,
            "version": plan.version,
            "source": plan.source,
            "steps": [cls._serialize_plan_step(step) for step in plan.steps],
        }

    @staticmethod
    def _serialize_plan_step(step: PlanStep) -> dict[str, Any]:
        return {
            "id": step.id,
            "description": step.description,
            "depends_on": list(step.depends_on),
            "success_criteria": step.success_criteria,
        }

    @classmethod
    def _serialize_step_run(cls, run: StepRun) -> dict[str, Any]:
        return {
            "step_id": run.step_id,
            "status": run.status.value,
            "attempt_count": run.attempt_count,
            "result": cls._serialize_result(run.result) if run.result else None,
            "error": run.error,
            "transcript": run.transcript,
        }

    @classmethod
    def _serialize_result(cls, result: StepResult) -> dict[str, Any]:
        return {
            "success": result.success,
            "output": result.output,
            "evidence": list(result.evidence),
            "artifacts": [cls._serialize_artifact(artifact) for artifact in result.artifacts],
            "error": result.error,
        }

    @staticmethod
    def _serialize_artifact(artifact: ArtifactRef) -> dict[str, str]:
        return {"uri": artifact.uri, "kind": artifact.kind}


__all__ = ["TASKS_DIR", "JsonTaskStore"]
