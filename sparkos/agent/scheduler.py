"""Deterministic serial scheduling for Plan dependency graphs."""

from __future__ import annotations

from sparkos.agent.planner import Plan, PlanStep
from sparkos.agent.step import StepRun, StepStatus
from sparkos.agent.task import AgentTask


def create_step_runs(plan: Plan) -> dict[str, StepRun]:
    return {step.id: StepRun(step_id=step.id) for step in plan.steps}


def create_direct_plan(task: AgentTask) -> Plan:
    return Plan(
        task_id=task.id,
        source="direct",
        steps=(
            PlanStep(
                id="direct",
                description=task.goal,
                depends_on=(),
                success_criteria="完整、准确地完成用户目标",
            ),
        ),
    )


class PlanScheduler:
    """Compute ready and terminal state without model or I/O dependencies."""

    @staticmethod
    def ready_steps(
        plan: Plan,
        step_runs: dict[str, StepRun],
    ) -> list[PlanStep]:
        return [
            step
            for step in plan.steps
            if step_runs[step.id].status == StepStatus.PENDING
            and all(step_runs[dependency].status == StepStatus.SUCCEEDED for dependency in step.depends_on)
        ]

    @staticmethod
    def block_failed_dependents(
        plan: Plan,
        step_runs: dict[str, StepRun],
    ) -> None:
        changed = True
        while changed:
            changed = False
            for step in plan.steps:
                run = step_runs[step.id]
                if run.status != StepStatus.PENDING:
                    continue
                failed_dependencies = [
                    dependency
                    for dependency in step.depends_on
                    if step_runs[dependency].status in {StepStatus.FAILED, StepStatus.BLOCKED}
                ]
                if failed_dependencies:
                    run.block("依赖步骤失败：" + ", ".join(failed_dependencies))
                    changed = True

    @staticmethod
    def is_complete(step_runs: dict[str, StepRun]) -> bool:
        return bool(step_runs) and all(run.status == StepStatus.SUCCEEDED for run in step_runs.values())

    @staticmethod
    def has_failed(step_runs: dict[str, StepRun]) -> bool:
        return any(run.status in {StepStatus.FAILED, StepStatus.BLOCKED} for run in step_runs.values())


__all__ = ["PlanScheduler", "create_direct_plan", "create_step_runs"]
