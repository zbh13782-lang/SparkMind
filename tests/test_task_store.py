from __future__ import annotations

import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from sparkos.agent.planner import Plan, PlanStep
from sparkos.agent.scheduler import create_step_runs
from sparkos.agent.step import ArtifactRef, StepResult
from sparkos.agent.task import AgentTask
from sparkos.agent.task_store import TaskStore
from sparkos.infrastructure.persistence.task_store import JsonTaskStore


class JsonTaskStoreTests(unittest.TestCase):
    def test_json_store_writes_waiting_input_task_without_plan(self) -> None:
        task = AgentTask(id="task-clarify", goal="帮我分析一下")
        task.wait_for_input("请提供要分析的文件路径。")

        with TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            JsonTaskStore(root).save(task, None, {})
            payload = json.loads((root / f"{task.id}.json").read_text(encoding="utf-8"))

        self.assertEqual(payload["task"]["status"], "waiting_input")
        self.assertEqual(
            payload["task"]["clarification_question"],
            "请提供要分析的文件路径。",
        )
        self.assertIsNone(payload["plan"])
        self.assertEqual(payload["step_runs"], {})

    def test_json_store_writes_task_plan_and_step_runs(self) -> None:
        task = AgentTask(id="task-1", goal="analyze", input={"file": "sales.csv"})
        plan = Plan(
            id="plan-1",
            task_id=task.id,
            source="planner",
            steps=(
                PlanStep(
                    id="s1",
                    description="read data",
                    depends_on=(),
                    success_criteria="data loaded",
                ),
            ),
        )
        task.active_plan_id = plan.id
        task.start()
        runs = create_step_runs(plan)
        runs["s1"].start()
        runs["s1"].succeed(
            StepResult(
                success=True,
                output="loaded",
                evidence=("120 rows",),
                artifacts=(ArtifactRef(uri="artifact://data", kind="table"),),
            )
        )
        runs["s1"].record_transcript(
            (
                {"role": "assistant", "content": ""},
                {"role": "tool", "content": "loaded", "tool_call_id": "c1"},
            )
        )

        with TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            store: TaskStore = JsonTaskStore(root)

            store.save(task, plan, runs)

            payload = json.loads((root / f"{task.id}.json").read_text(encoding="utf-8"))
            self.assertEqual(payload["task"]["status"], "running")
            self.assertEqual(payload["task"]["active_plan_id"], "plan-1")
            self.assertEqual(
                payload["plan"]["steps"][0]["success_criteria"],
                "data loaded",
            )
            self.assertEqual(
                payload["step_runs"]["s1"]["result"]["output"],
                "loaded",
            )
            self.assertEqual(
                payload["step_runs"]["s1"]["result"]["artifacts"][0],
                {"uri": "artifact://data", "kind": "table"},
            )
            self.assertEqual(
                payload["step_runs"]["s1"]["transcript"][-1]["tool_call_id"],
                "c1",
            )
            self.assertEqual(list(root.glob("*.tmp")), [])

    def test_json_store_writes_attempt_history(self) -> None:
        task = AgentTask(id="task-retry", goal="analyze")
        plan = Plan(
            task_id=task.id,
            steps=(PlanStep(id="s1", description="analyze"),),
        )
        runs = create_step_runs(plan)
        run = runs["s1"]
        first = StepResult(success=True, output="incomplete")
        run.start()
        run.record_transcript([{"role": "assistant", "content": "incomplete"}])
        run.fail("missing totals", first)
        run.start()
        run.record_transcript([{"role": "assistant", "content": "complete"}])
        run.succeed(StepResult(success=True, output="complete"))

        with TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            JsonTaskStore(root).save(task, plan, runs)
            payload = json.loads((root / f"{task.id}.json").read_text(encoding="utf-8"))

        stored = payload["step_runs"]["s1"]
        self.assertEqual(stored["attempt_count"], 2)
        self.assertEqual(stored["result"]["output"], "complete")
        self.assertEqual(
            stored["transcript"][0]["content"],
            "complete",
        )

    def test_json_store_atomically_replaces_existing_snapshot(self) -> None:
        task = AgentTask(id="task-1", goal="analyze")
        plan = Plan(
            id="plan-1",
            task_id=task.id,
            source="direct",
            steps=(
                PlanStep(
                    id="direct",
                    description="analyze",
                    success_criteria="done",
                ),
            ),
        )
        runs = create_step_runs(plan)

        with TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            store = JsonTaskStore(root)
            store.save(task, plan, runs)
            task.succeed("done")

            store.save(task, plan, runs)

            payload = json.loads((root / f"{task.id}.json").read_text(encoding="utf-8"))
            self.assertEqual(payload["task"]["status"], "succeeded")
            self.assertEqual(payload["task"]["result"], "done")
            self.assertEqual(list(root.iterdir()), [root / "task-1.json"])

    def test_json_store_rejects_task_id_path_escape(self) -> None:
        task = AgentTask(id="../outside", goal="unsafe")
        plan = Plan(
            task_id=task.id,
            steps=(PlanStep(id="direct", description="unsafe"),),
        )
        runs = create_step_runs(plan)

        with TemporaryDirectory() as temporary_directory:
            parent = Path(temporary_directory)
            store = JsonTaskStore(parent / "tasks")

            with self.assertRaisesRegex(ValueError, "task id"):
                store.save(task, plan, runs)

            self.assertFalse((parent / "outside.json").exists())

    def test_json_store_archives_previous_plan_when_replanned(self) -> None:
        task = AgentTask(id="task-replan", goal="analyze")
        first_plan = Plan(
            id="plan-1",
            task_id=task.id,
            steps=(PlanStep(id="s1", description="primary"),),
        )
        first_runs = create_step_runs(first_plan)
        first_runs["s1"].start()
        first_runs["s1"].fail("source blocked")
        second_plan = Plan(
            id="plan-2",
            task_id=task.id,
            version=2,
            source="replan",
            steps=(PlanStep(id="s2", description="fallback"),),
        )
        second_runs = create_step_runs(second_plan)

        with TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            store = JsonTaskStore(root)
            store.save(task, first_plan, first_runs)
            store.save(task, second_plan, second_runs)
            payload = json.loads((root / f"{task.id}.json").read_text(encoding="utf-8"))

        self.assertEqual(payload["plan"]["id"], "plan-2")
        self.assertEqual(payload["plan_history"][0]["plan"]["id"], "plan-1")
        self.assertEqual(
            payload["plan_history"][0]["step_runs"]["s1"]["error"],
            "source blocked",
        )

    def test_json_store_archives_changed_plan_even_if_id_is_reused(self) -> None:
        task = AgentTask(id="task-reused-plan-id", goal="analyze")
        first_plan = Plan(
            id="plan-1",
            task_id=task.id,
            steps=(PlanStep(id="s1", description="primary"),),
        )
        first_runs = create_step_runs(first_plan)
        first_runs["s1"].start()
        first_runs["s1"].fail("source blocked")
        second_plan = Plan(
            id="plan-1",
            task_id=task.id,
            version=2,
            source="replan",
            steps=(PlanStep(id="s2", description="fallback"),),
        )

        with TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            store = JsonTaskStore(root)
            store.save(task, first_plan, first_runs)
            store.save(task, second_plan, create_step_runs(second_plan))
            payload = json.loads((root / f"{task.id}.json").read_text(encoding="utf-8"))

        self.assertEqual(payload["plan_history"][0]["plan"]["version"], 1)
        self.assertEqual(
            payload["plan_history"][0]["step_runs"]["s1"]["error"],
            "source blocked",
        )


if __name__ == "__main__":
    unittest.main()
