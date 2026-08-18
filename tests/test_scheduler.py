from __future__ import annotations

import unittest

from sparkos.agent.planner import Plan, PlanStep
from sparkos.agent.scheduler import PlanScheduler, create_direct_plan, create_step_runs
from sparkos.agent.step import StepResult, StepStatus
from sparkos.agent.task import AgentTask


def sample_plan() -> Plan:
    return Plan(
        id="plan-1",
        task_id="task-1",
        steps=(
            PlanStep(
                id="s1",
                description="read",
                depends_on=(),
                success_criteria="data loaded",
            ),
            PlanStep(
                id="s2",
                description="analyze",
                depends_on=("s1",),
                success_criteria="analysis produced",
            ),
            PlanStep(
                id="s3",
                description="report",
                depends_on=("s2",),
                success_criteria="report produced",
            ),
        ),
    )


class TestPlanScheduler(unittest.TestCase):
    def test_only_dependency_ready_steps_are_returned(self) -> None:
        plan = sample_plan()
        runs = create_step_runs(plan)
        scheduler = PlanScheduler()

        self.assertEqual(
            [step.id for step in scheduler.ready_steps(plan, runs)],
            ["s1"],
        )

        runs["s1"].start()
        runs["s1"].succeed(StepResult(success=True, output="loaded"))

        self.assertEqual(
            [step.id for step in scheduler.ready_steps(plan, runs)],
            ["s2"],
        )

    def test_failed_dependency_blocks_all_descendants(self) -> None:
        plan = sample_plan()
        runs = create_step_runs(plan)
        scheduler = PlanScheduler()
        runs["s1"].start()
        runs["s1"].fail("read failed")

        scheduler.block_failed_dependents(plan, runs)

        self.assertEqual(runs["s2"].status, StepStatus.BLOCKED)
        self.assertEqual(runs["s3"].status, StepStatus.BLOCKED)
        self.assertTrue(scheduler.has_failed(runs))

    def test_plan_completes_only_when_every_step_succeeds(self) -> None:
        plan = sample_plan()
        runs = create_step_runs(plan)
        scheduler = PlanScheduler()
        for run in runs.values():
            run.start()
            run.succeed(StepResult(success=True, output=run.step_id))

        self.assertTrue(scheduler.is_complete(runs))
        self.assertFalse(scheduler.has_failed(runs))

    def test_direct_plan_wraps_simple_task_in_one_step(self) -> None:
        task = AgentTask(id="task-1", goal="Say hello")

        plan = create_direct_plan(task)

        self.assertEqual(plan.source, "direct")
        self.assertEqual(len(plan.steps), 1)
        self.assertEqual(plan.steps[0].id, "direct")
        self.assertEqual(plan.steps[0].description, "Say hello")
        self.assertTrue(plan.steps[0].success_criteria)


if __name__ == "__main__":
    unittest.main()
