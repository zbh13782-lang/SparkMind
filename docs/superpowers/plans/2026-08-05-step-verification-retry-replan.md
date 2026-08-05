# Step Verification, Retry, and Replan Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a bounded reliability loop that verifies every StepResult against its success criteria, retries one rejected attempt with feedback, and replans once when retry cannot satisfy the criteria.

**Status:** Implemented and verified on 2026-08-05.

**Architecture:** `StepVerifier` is a separate port from execution and returns immutable structured verification data. `RetryPolicy` makes deterministic bounded decisions from `StepRun.attempt_count`; Runtime remains the state owner and persists every verify/retry/replan transition. `LLMPlanner.revise_plan()` returns a complete replacement Plan whose unchanged succeeded steps can be reused by id and definition.

**Tech Stack:** Python 3.14, frozen dataclasses, asyncio, JSON structured LLM output, standard-library unittest, existing OpenAI-compatible client.

## Global Constraints

- Preserve all existing uncommitted work.
- Add no third-party dependency.
- Keep Step scheduling serial.
- Maximum attempts per Step are exactly 2, including the initial attempt.
- Maximum replans per Task are exactly 1.
- A verifier transport or parsing failure is persisted but fails open; an explicit verification rejection does not fail open.
- Replan output is a complete immutable Plan with `version = previous.version + 1`.
- Reuse a succeeded StepRun only when the new Plan contains the same id, description, dependencies, and success criteria.
- Session history continues to contain only user input and the final assistant answer.

---

### Task 1: Verification domain model and retry policy

**Files:**
- Modify: `sparkos/agent/step.py`
- Create: `sparkos/agent/retry.py`
- Modify: `sparkos/infrastructure/persistence/task_store.py`
- Test: `tests/test_agent_models.py`
- Test: `tests/test_task_store.py`

**Interfaces:**
- Produces: `StepVerification(passed, reason, retryable, evidence, error)`, `StepStatus.VERIFYING`, `StepRun.begin_verification()`, `StepRun.record_verification()`, `StepRun.prepare_retry()`, and `RetryPolicy(max_attempts=2).should_retry(run, verification)`.
- Consumes: existing `StepRun`, `StepResult`, and JSON Task snapshot serialization.

- [ ] **Step 1: Write failing state and policy tests**

```python
run.start()
run.begin_verification()
verification = StepVerification(False, "missing temperature", True)
run.record_verification(verification)
self.assertTrue(RetryPolicy(max_attempts=2).should_retry(run, verification))
run.prepare_retry()
self.assertEqual(run.status, StepStatus.PENDING)
self.assertEqual(len(run.transcript_history), 1)
```

- [ ] **Step 2: Run the tests and observe missing imports/state**

Run: `.venv/bin/python -m unittest tests.test_agent_models tests.test_task_store -v`

Expected: failure for missing `StepVerification`, `VERIFYING`, and `RetryPolicy`.

- [ ] **Step 3: Implement state transitions and serialization**

`prepare_retry()` archives the current transcript, clears current result/error/verification, and returns the run to PENDING without decrementing `attempt_count`. JSON snapshots add `verification`, `verification_history`, and `transcript_history`.

- [ ] **Step 4: Run focused tests**

Run: `.venv/bin/python -m unittest tests.test_agent_models tests.test_task_store -v`

Expected: all focused tests pass.

### Task 2: Structured LLM StepVerifier

**Files:**
- Create: `sparkos/agent/verifier.py`
- Test: `tests/test_step_verifier.py`

**Interfaces:**
- Produces: `StepVerifier.verify(task, step, result, dependency_results) -> StepVerification` and `LLMStepVerifier(model)`.
- Consumes: a model exposing `chat_once(messages: list[dict]) -> str`, one candidate `StepResult`, the immutable `PlanStep`, and dependency results.

- [ ] **Step 1: Write failing verifier tests**

```python
verification = await verifier.verify(task, step, candidate, dependencies)
self.assertFalse(verification.passed)
self.assertTrue(verification.retryable)
self.assertEqual(verification.reason, "missing temperature")
```

Also assert the request contains `success_criteria`, candidate output, dependencies, and that malformed model output returns a fail-open verification with `error` populated.

- [ ] **Step 2: Run and observe missing verifier module**

Run: `.venv/bin/python -m unittest tests.test_step_verifier -v`

Expected: import failure for `sparkos.agent.verifier`.

- [ ] **Step 3: Implement strict JSON parsing with fail-open transport behavior**

The model response schema is:

```json
{
  "passed": false,
  "reason": "missing temperature",
  "retryable": true,
  "evidence": []
}
```

Invalid explicit field types raise inside parsing and are converted to `StepVerification(passed=True, retryable=False, error=...)` by `verify()`.

- [ ] **Step 4: Run verifier tests**

Run: `.venv/bin/python -m unittest tests.test_step_verifier -v`

Expected: all verifier tests pass.

### Task 3: Runtime verify and retry loop

**Files:**
- Modify: `sparkos/agent/step_executor.py`
- Modify: `sparkos/agent/events.py`
- Modify: `sparkos/agent/runtime.py`
- Modify: `sparkos/ui/chat_app.py`
- Test: `tests/test_agent_runtime.py`
- Test: `tests/test_ui_integration.py`

**Interfaces:**
- Consumes: `StepVerifier`, `RetryPolicy`, and optional `retry_feedback` in `StepExecutor.stream()`.
- Produces: `StepVerificationCompleted(step, verification)` and `StepRetrying(step, attempt, reason)` events; Runtime retries the same Step once before declaring it failed.

- [ ] **Step 1: Write failing Runtime retry tests**

```python
self.assertEqual(run.attempt_count, 2)
self.assertEqual(
    [type(e) for e in events if isinstance(e, StepRetrying)], [StepRetrying]
)
self.assertEqual(task.status, TaskStatus.SUCCEEDED)
self.assertIn("missing temperature", second_attempt_system_message)
```

Also test a non-retryable rejection fails immediately and verifier evidence is merged into the accepted `StepResult`.

- [ ] **Step 2: Run and observe the current unconditional success behavior**

Run: `.venv/bin/python -m unittest tests.test_agent_runtime -v`

Expected: rejected non-empty Step output is still marked SUCCEEDED and retry events are absent.

- [ ] **Step 3: Implement the bounded verify/retry loop**

After execution succeeds, Runtime sets VERIFYING, saves, calls the verifier, saves the structured outcome, and emits `StepVerificationCompleted`. A retryable rejection with `attempt_count < 2` archives the attempt, emits `StepRetrying`, and runs the same immutable PlanStep again with verifier feedback injected into its system payload.

- [ ] **Step 4: Run Runtime and UI tests**

Run: `.venv/bin/python -m unittest tests.test_agent_runtime tests.test_ui_integration -v`

Expected: all Runtime/UI tests pass.

### Task 4: LLM replan and state reconciliation

**Files:**
- Modify: `sparkos/agent/planner.py`
- Modify: `sparkos/agent/llm_planner.py`
- Modify: `sparkos/agent/events.py`
- Modify: `sparkos/agent/runtime.py`
- Modify: `sparkos/ui/chat_app.py`
- Test: `tests/test_llm_planner.py`
- Test: `tests/test_agent_runtime.py`

**Interfaces:**
- Produces: `Replanner.revise_plan(task, context, current_plan, step_runs, failed_step, reason) -> Plan | None`, `LLMPlanner.revise_plan()`, and `PlanReplanned(previous_plan, plan, reason)`.
- Consumes: the failed verified Step, current immutable Plan, persisted StepRuns, and the existing planning capability snapshot.

- [ ] **Step 1: Write failing replan parsing and orchestration tests**

```python
revised = await planner.revise_plan(
    task, context, old_plan, runs, failed_step, "blocked source"
)
self.assertEqual(revised.version, old_plan.version + 1)
self.assertEqual(revised.source, "replan")
```

Runtime test: verifier rejects both attempts, FakeReplanner returns a replacement Plan, unchanged succeeded StepRun is reused, replacement Step succeeds, and exactly one `PlanReplanned` event is emitted.

- [ ] **Step 2: Run and observe missing Replanner/event behavior**

Run: `.venv/bin/python -m unittest tests.test_llm_planner tests.test_agent_runtime -v`

Expected: failures for missing `revise_plan` and `PlanReplanned`.

- [ ] **Step 3: Implement one bounded replan and reconciliation**

The replan prompt includes task, current Plan, ordered StepRun results/verifications, failed Step, reason, capabilities, and the rule to retain completed steps unchanged. Runtime accepts only `version == old.version + 1`, reconciles unchanged SUCCEEDED runs, creates fresh runs for changed/new steps, updates `task.active_plan_id`, saves, and emits `PlanReplanned`.

- [ ] **Step 4: Run focused replan tests**

Run: `.venv/bin/python -m unittest tests.test_llm_planner tests.test_agent_runtime -v`

Expected: all focused tests pass.

### Task 5: Documentation and full verification

**Files:**
- Modify: `CLAUDE.md`
- Modify: `docs/superpowers/plans/2026-08-05-step-verification-retry-replan.md`

**Interfaces:**
- Consumes: completed verification/retry/replan implementation.
- Produces: documented state machine, limits, persistence schema, and verification evidence.

- [ ] **Step 1: Document the new flow**

Document `RUNNING -> VERIFYING -> SUCCEEDED`, bounded retry, one replan, fail-open verifier transport behavior, new events, and persisted verification history.

- [ ] **Step 2: Run full verification**

Run: `.venv/bin/python -m unittest discover -s tests -v`

Run: `.venv/bin/ruff check sparkos tests`

Run: `.venv/bin/ruff format --check sparkos tests`

Run: `.venv/bin/python -m compileall -q sparkos tests`

Run: `git diff --check`

Expected: every command exits 0.

## Verification Evidence

- `.venv/bin/python -m unittest discover -s tests -v`: 81 tests passed.
- `.venv/bin/ruff check sparkos tests`: passed.
- `.venv/bin/ruff format --check sparkos tests`: 40 files formatted.
- `.venv/bin/python -m compileall -q sparkos tests`: passed.
- `git diff --check`: passed.
- Read-only review findings were addressed with regression coverage for hard retry/replan limits, failed-step identity, legal downstream dependency rewrites, replacement Plan identity/source, defensive Plan history archival, strict verifier JSON, and event-stream closure from both PLANNING and VERIFYING.
