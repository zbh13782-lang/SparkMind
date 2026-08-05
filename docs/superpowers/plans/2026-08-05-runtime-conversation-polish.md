# Runtime Conversation Polish Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Collapse each task's tool calls into one nested summary, let the planner explicitly request missing details, and preserve model chunk boundaries through Runtime so final answers render progressively.

**Architecture:** Keep execution semantics in the agent layer and presentation grouping in the TUI. Add a typed clarification decision/event instead of overloading `None`, reuse the existing `WAITING_INPUT` task status, and carry final-turn text chunks through `StepExecution` so both direct and planned answers emit incremental `TextDelta` events. A dedicated Textual widget owns the nested tool-call disclosure UI.

**Tech Stack:** Python 3.14, Textual 8.2, Rich, `unittest`, Ruff.

## Global Constraints

- Do not add dependencies.
- Do not display step-internal draft text; only stream the accepted direct answer or final synthesis.
- Preserve API-valid assistant(tool_calls) → tool(tool_call_id) history ordering.
- Planner failures and invalid output must continue to fail open to direct execution.
- Keep user, tool, and planner-provided strings safe from Rich markup interpretation.
- Leave all changes unstaged and uncommitted unless the user explicitly requests Git operations.

---

### Task 1: Per-task tool-call summary

**Files:**
- Create: `sparkos/ui/tool_summary.py`
- Modify: `sparkos/ui/chat_app.py`
- Modify: `tests/test_ui_integration.py`

**Interfaces:**
- Consumes: completed `ToolCall` objects from `StepToolCompleted`.
- Produces: `ToolCallSummary.add_tool(tool_call)` and `ToolCallSummary.complete()`; one outer disclosure titled `执行了 N 个工具`, containing one inner disclosure per call.

- [x] **Step 1: Write the failing nested-summary UI test**

Mount one `ToolCallSummary`, add two calls, complete it, and assert there is one outer `.tool-summary`, two `.tool-call` children, the final count is 2, and literal arguments/results survive rendering.

- [x] **Step 2: Run the test and verify RED**

Run: `.venv/bin/python -m unittest tests.test_ui_integration.ChatAppLayoutTests.test_tool_calls_are_grouped_under_one_summary`

Expected: import or selector failure because `ToolCallSummary` does not exist.

- [x] **Step 3: Implement and integrate the widget**

Create one summary lazily on the first `StepToolCompleted`; update its count for subsequent calls. Inner collapsibles show `参数` and `结果` as `rich.text.Text` with `markup=False`. Finalize the title from `正在执行工具（N）` to `执行了 N 个工具` when the runtime worker ends.

- [x] **Step 4: Run the focused test and verify GREEN**

Run: `.venv/bin/python -m unittest tests.test_ui_integration`

Expected: all TUI integration tests pass.

---

### Task 2: Planner clarification decision

**Files:**
- Modify: `sparkos/agent/planner.py`
- Modify: `sparkos/agent/llm_planner.py`
- Modify: `sparkos/agent/events.py`
- Modify: `sparkos/agent/task.py`
- Modify: `sparkos/agent/runtime.py`
- Modify: `sparkos/agent/task_store.py`
- Modify: `sparkos/infrastructure/persistence/task_store.py`
- Modify: `sparkos/ui/runtime_panel.py`
- Modify: `sparkos/ui/chat_app.py`
- Modify: `tests/test_llm_planner.py`
- Modify: `tests/test_agent_runtime.py`
- Modify: `tests/test_agent_models.py`
- Modify: `tests/test_runtime_panel.py`
- Modify: `tests/test_ui_integration.py`
- Modify: `tests/test_task_store.py`

**Interfaces:**
- Produces: immutable `ClarificationRequest(question: str)`, `ClarificationRequested(task, question)` event, `AgentTask.wait_for_input(question)`, and nullable task snapshot plans.
- `Planner.create_plan(...) -> Plan | ClarificationRequest | None`.

- [x] **Step 1: Write failing planner and task-state tests**

Use planner JSON `{"should_plan": false, "clarification_question": "请提供目标文件。", "steps": []}` and assert a trimmed `ClarificationRequest`. Assert blank/non-string clarification values fail open, while `AgentTask.wait_for_input()` records `WAITING_INPUT` and the question.

- [x] **Step 2: Verify planner/task tests are RED**

Run: `.venv/bin/python -m unittest tests.test_llm_planner tests.test_agent_models`

Expected: missing clarification type and task transition.

- [x] **Step 3: Implement typed planner decision**

Extend the planner prompt with three outcomes: clarify, direct, or plan. Return `ClarificationRequest` only for a non-empty question when `should_plan` is false; replan continues to accept plans only.

- [x] **Step 4: Write failing Runtime/UI clarification tests**

Assert Runtime emits `TaskStarted`, then `ClarificationRequested`, creates no `PlanCreated`/step/tool/verification events, stores user + assistant question, persists `WAITING_INPUT`, and the TUI displays `等待补充` while re-enabling the prompt.

- [x] **Step 5: Implement Runtime, persistence, and UI handling**

Short-circuit after planning, persist a nullable-plan task snapshot, and return without `TaskCompleted`. Render the planner question in the existing assistant message and set the dashboard to its terminal waiting-input state.

- [x] **Step 6: Run clarification tests and verify GREEN**

Run: `.venv/bin/python -m unittest tests.test_llm_planner tests.test_agent_models tests.test_agent_runtime tests.test_task_store tests.test_runtime_panel tests.test_ui_integration`

Expected: all clarification and existing lifecycle tests pass.

---

### Task 3: End-to-end final-answer streaming

**Files:**
- Modify: `sparkos/agent/step_executor.py`
- Modify: `sparkos/agent/runtime.py`
- Modify: `sparkos/ui/chat_app.py`
- Modify: `tests/test_step_executor.py`
- Modify: `tests/test_agent_runtime.py`
- Modify: `tests/test_ui_integration.py`

**Interfaces:**
- `StepExecution.text_chunks: tuple[str, ...]` contains only the accepted final non-tool assistant turn.
- Runtime emits one `TextDelta` per provider chunk for direct execution and plan synthesis.
- TUI writes each delta and awaits a 0.01-second frame delay before consuming the next event.

- [x] **Step 1: Write failing chunk-preservation tests**

Assert a direct response `['hello', ' ', 'world']` yields three `TextDelta` events and a planned final synthesis does the same, while `task.result` and context still store the joined answer once.

- [x] **Step 2: Verify streaming tests are RED**

Run: `.venv/bin/python -m unittest tests.test_step_executor tests.test_agent_runtime`

Expected: current Runtime emits one joined `TextDelta`.

- [x] **Step 3: Preserve and emit accepted chunks**

Store the final non-tool turn's `turn_parts` on `StepExecution`. In direct mode yield each stored chunk; in synthesis mode yield each delta immediately while accumulating the final persisted answer. Keep fallback answers as a single delta.

- [x] **Step 4: Add and verify the TUI pacing test**

Patch `asyncio.sleep`, feed multiple `TextDelta` events, and assert one `sleep(0.01)` call per rendered delta without delaying planner/tool events.

- [x] **Step 5: Run full verification**

Run: `.venv/bin/python -m unittest discover -s tests`

Expected: all tests pass.

Run: `.venv/bin/ruff check sparkos tests`

Expected: `All checks passed!`

Run: `git diff --check && git status --short`

Expected: no whitespace errors and no changes to the unrelated `utils/` directory.
