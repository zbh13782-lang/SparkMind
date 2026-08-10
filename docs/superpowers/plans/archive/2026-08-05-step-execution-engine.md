# Step Execution Engine Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace whole-plan prompt guidance with a persisted, serial DAG execution engine that runs and records each PlanStep before synthesizing one final user response.

**Status:** Implemented on 2026-08-05. Code review follow-ups added persisted Step transcripts, consistent cancellation, domain-level Plan validation, and safe Task snapshot filenames.

**Architecture:** Immutable `Plan`/`PlanStep` objects describe work while mutable `StepRun`/`StepResult` objects record execution. A pure `PlanScheduler` selects dependency-ready steps, `StepExecutor` owns the bounded model/tool loop for one step, and `AgentRuntime` coordinates planning, persistence, events, step execution, and final synthesis. Session history stores only user-facing messages; detailed step/tool state is stored in task snapshots.

**Tech Stack:** Python 3.14, dataclasses, asyncio, JSON, pathlib atomic replace, OpenAI-compatible chat transport, Textual, standard-library unittest.

## Global Constraints

- Preserve all existing uncommitted work.
- Add no third-party dependency.
- Execute ready steps serially in this phase.
- Do not implement retry, LLM verification, replan, user-input suspension, or parallel steps in this phase.
- Simple tasks must use a synthetic one-step direct Plan so Runtime has one execution path.
- Plan definitions are immutable; execution status belongs to StepRun.
- Session history stores only the user request and final assistant answer.
- Task state is stored as atomic JSON snapshots under `.sparkmind/tasks/`.

---

### Task 1: Immutable plan and step execution models

**Files:**
- Modify: `sparkos/agent/planner.py`
- Modify: `sparkos/agent/task.py`
- Create: `sparkos/agent/step.py`
- Test: `tests/test_agent_models.py`

**Interfaces:**
- Produces: immutable `PlanStep(id, description, depends_on, success_criteria)`, immutable `Plan`, `StepStatus`, `ArtifactRef`, `StepResult`, and mutable `StepRun.start/succeed/fail/block`.
- Consumes: existing `AgentTask` and Planner response data.

- [ ] **Step 1: Write failing model tests**

```python
def test_step_run_owns_execution_state(self):
    step = PlanStep(
        id="s1", description="read", depends_on=(), success_criteria="data loaded"
    )
    run = StepRun(step_id=step.id)
    run.start()
    run.succeed(StepResult(success=True, output="loaded"))
    self.assertEqual(run.status, StepStatus.SUCCEEDED)
    self.assertFalse(hasattr(step, "status"))
```

- [ ] **Step 2: Run and verify the desired models are missing**

Run: `.venv/bin/python -m unittest tests.test_agent_models -v`

Expected: import/assertion failures for StepRun and immutable PlanStep state separation.

- [ ] **Step 3: Implement the minimal domain models**

`AgentTask` gains `PLANNING` and `WAITING_INPUT` statuses. `Plan` gains `source`; `PlanStep` gains `success_criteria`. `StepResult` contains output, evidence, artifacts, and optional error.

- [ ] **Step 4: Run model tests**

Run: `.venv/bin/python -m unittest tests.test_agent_models -v`

Expected: all model tests pass.

### Task 2: Pure DAG scheduler and direct Plan factory

**Files:**
- Create: `sparkos/agent/scheduler.py`
- Test: `tests/test_scheduler.py`

**Interfaces:**
- Consumes: `Plan` and `dict[str, StepRun]`.
- Produces: `create_step_runs(plan)`, `create_direct_plan(task)`, `ready_steps(plan, runs)`, `block_failed_dependents(plan, runs)`, and completion/failure predicates.

- [ ] **Step 1: Write failing scheduler tests**

```python
def test_only_dependency_ready_steps_are_returned(self):
    runs = create_step_runs(plan)
    self.assertEqual([step.id for step in scheduler.ready_steps(plan, runs)], ["s1"])
    runs["s1"].succeed(StepResult(success=True, output="ok"))
    self.assertEqual([step.id for step in scheduler.ready_steps(plan, runs)], ["s2"])
```

Also test failed-dependency blocking and the synthetic `direct` step.

- [ ] **Step 2: Run and verify scheduler imports fail**

Run: `.venv/bin/python -m unittest tests.test_scheduler -v`

Expected: import failure for `sparkos.agent.scheduler`.

- [ ] **Step 3: Implement deterministic scheduler logic**

Ready means PENDING with all dependencies SUCCEEDED. A PENDING step with a FAILED or BLOCKED dependency becomes BLOCKED. A Plan succeeds only when every run is SUCCEEDED; terminal non-success states fail the Plan.

- [ ] **Step 4: Run scheduler tests**

Run: `.venv/bin/python -m unittest tests.test_scheduler -v`

Expected: all scheduler tests pass.

### Task 3: Atomic Task JSON store

**Files:**
- Create: `sparkos/agent/task_store.py`
- Create: `sparkos/infrastructure/persistence/__init__.py`
- Create: `sparkos/infrastructure/persistence/task_store.py`
- Test: `tests/test_task_store.py`

**Interfaces:**
- Produces: `TaskStore.save(task, plan, step_runs)` protocol and `JsonTaskStore(root)` implementation.
- Consumes: Task, Plan, StepRun, StepResult, and ArtifactRef models.

- [ ] **Step 1: Write failing persistence test**

```python
def test_json_store_writes_task_plan_and_step_runs(self):
    store.save(task, plan, runs)
    payload = json.loads((root / f"{task.id}.json").read_text())
    self.assertEqual(payload["task"]["status"], "running")
    self.assertEqual(payload["plan"]["steps"][0]["success_criteria"], "data loaded")
```

- [ ] **Step 2: Run and verify store imports fail**

Run: `.venv/bin/python -m unittest tests.test_task_store -v`

Expected: import failure for TaskStore/JsonTaskStore.

- [ ] **Step 3: Implement snapshot serialization and atomic replacement**

Write UTF-8 JSON to a unique temporary sibling and call `Path.replace()` only after serialization completes. Remove the temporary file on failure.

- [ ] **Step 4: Run persistence tests**

Run: `.venv/bin/python -m unittest tests.test_task_store -v`

Expected: all persistence tests pass.

### Task 4: Single-step model/tool executor

**Files:**
- Create: `sparkos/agent/step_executor.py`
- Modify: `sparkos/agent/events.py`
- Test: `tests/test_step_executor.py`

**Interfaces:**
- Consumes: one `PlanStep`, dependency results, base messages, model client, tools, and tool executor.
- Produces: `StepExecution(result, tool_calls)` and Step lifecycle event models.

- [ ] **Step 1: Write failing executor protocol tests**

```python
async def test_executor_keeps_tool_transcript_local_to_step(self):
    execution = await executor.execute(step, {}, base_messages)
    self.assertEqual(execution.result.output, "done")
    self.assertEqual(client.requests[1][-2]["tool_calls"][0]["id"], "c1")
    self.assertEqual(client.requests[1][-1]["tool_call_id"], "c1")
```

Also test dependency-result injection and tool-round limits.

- [ ] **Step 2: Run and verify StepExecutor is missing**

Run: `.venv/bin/python -m unittest tests.test_step_executor -v`

Expected: import failure for `sparkos.agent.step_executor`.

- [ ] **Step 3: Extract the bounded loop from Runtime**

Build a step-specific system message containing task goal, step definition, success criteria, and dependency results. Maintain assistant/tool messages locally and return a `StepResult`; do not mutate AgentContext.

- [ ] **Step 4: Run executor tests**

Run: `.venv/bin/python -m unittest tests.test_step_executor -v`

Expected: all executor tests pass.

### Task 5: Runtime orchestration, final synthesis, and TUI events

**Files:**
- Rewrite: `sparkos/agent/runtime.py`
- Modify: `sparkos/agent/llm_planner.py`
- Modify: `sparkos/agent/events.py`
- Modify: `sparkos/ui/chat_app.py`
- Modify: `CLAUDE.md`
- Test: `tests/test_agent_runtime.py`
- Test: `tests/test_llm_planner.py`
- Test: `tests/test_ui_integration.py`

**Interfaces:**
- Consumes: Planner, Scheduler, StepExecutor, TaskStore, and model client.
- Produces: serial Step execution with `StepStarted`, `StepToolCompleted`, `StepCompleted`, `StepFailed`, one final `TextDelta`, and persisted Task snapshots.

- [ ] **Step 1: Write failing orchestration tests**

```python
async def test_runtime_executes_plan_steps_in_dependency_order(self):
    events = await collect_events(runtime, task)
    self.assertEqual(
        [e.step.id for e in events if isinstance(e, StepStarted)], ["s1", "s2"]
    )
    self.assertEqual([m.role for m in context.history], ["user", "assistant"])
    self.assertEqual(task.status, TaskStatus.SUCCEEDED)
```

Also test direct Plan fallback, task snapshot saves, failed Step propagation, and final synthesis.

- [ ] **Step 2: Run and verify current whole-plan Runtime fails**

Run: `.venv/bin/python -m unittest tests.test_agent_runtime -v`

Expected: failures because Runtime does not schedule StepRun objects.

- [ ] **Step 3: Implement serial orchestration**

Plan or create a direct Plan, persist it, execute one ready step at a time, persist each state transition, synthesize a final answer from ordered StepResults, and record only that final answer in AgentContext.

- [ ] **Step 4: Adapt Planner schema and TUI event handling**

Planner accepts/returns `success_criteria`. TUI renders Step status and step-scoped tool completions while `TextDelta` remains the final user answer stream.

- [ ] **Step 5: Run full verification**

Run: `.venv/bin/python -m unittest discover -s tests -v`

Expected: all tests pass.

Run: `.venv/bin/ruff check sparkos tests`

Expected: `All checks passed!`

Run: `.venv/bin/ruff format --check sparkos tests`

Expected: all files formatted.

Run: `.venv/bin/python -m compileall -q sparkos tests`

Expected: exit code 0 with no output.
