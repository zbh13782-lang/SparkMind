# TUI Runtime Dashboard Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Turn the chat-only Textual interface into a responsive execution workspace that shows the active task, runtime phase, plan steps, tools, verification, retries, replans, and final response progress.

**Architecture:** Add a presentation-only `RuntimeTrace` state model and `RuntimePanel` widget that consume existing typed `AgentEvent` objects without changing runtime semantics. Place the conversation and dashboard side by side on wide terminals and stack them on narrow terminals. Keep full tool arguments/results in the existing collapsible chat detail while the dashboard shows concise execution metadata.

**Tech Stack:** Python 3.14, Textual 8.2, Rich markup, `unittest`, Textual headless `run_test()`/`Pilot`.

## Global Constraints

- Do not add dependencies or change the `AgentRuntime` event protocol.
- Keep `AgentRuntime` as the single source of execution truth; the UI only derives display state from emitted events.
- Escape task and step text before rendering Rich markup.
- Preserve the existing chat, slash commands, history picker, file picker, and per-tool collapsible details.
- Leave changes unstaged and uncommitted unless the user explicitly requests a Git operation.

---

### Task 1: Runtime execution presentation model

**Files:**
- Create: `sparkos/ui/runtime_panel.py`
- Create: `tests/test_runtime_panel.py`

**Interfaces:**
- Consumes: `AgentTask`, `AgentEvent`, `Plan`, `PlanStep`, and `ToolCall` domain objects.
- Produces: `RuntimeTrace.begin_task(task)`, `RuntimeTrace.apply(event)`, `RuntimeTrace.cancel()`, `RuntimeTrace.reset()`, `RuntimeTrace.render_markup()`, and `RuntimePanel` methods with matching names.

- [x] **Step 1: Write failing state-transition tests**

```python
trace = RuntimeTrace()
trace.begin_task(AgentTask(id="task-1", goal="Analyze sales"))
trace.apply(TaskStarted(task))
trace.apply(PlanCreated(plan))
trace.apply(StepStarted(plan.steps[0]))
trace.apply(StepToolCompleted(plan.steps[0], tool_call))

self.assertEqual(trace.phase, "tooling")
self.assertEqual(trace.steps["load"].tool_count, 1)
self.assertIn("Analyze sales", trace.render_markup())
```

- [x] **Step 2: Run tests and verify RED**

Run: `.venv/bin/python -m unittest tests.test_runtime_panel`

Expected: failure because `sparkos.ui.runtime_panel` does not exist.

- [x] **Step 3: Implement the event-driven trace and widget**

```python
@dataclass
class RuntimeStepView:
    id: str
    description: str
    status: str = "pending"
    attempt: int = 0
    tool_count: int = 0


class RuntimeTrace:
    def begin_task(self, task: AgentTask) -> None: ...
    def apply(self, event: AgentEvent) -> None: ...
    def cancel(self) -> None: ...
    def reset(self) -> None: ...
    def render_markup(self) -> str: ...


class RuntimePanel(Static):
    def begin_task(self, task: AgentTask) -> None:
        self.trace.begin_task(task)
        self.update(self.trace.render_markup())
```

- [x] **Step 4: Run tests and verify GREEN**

Run: `.venv/bin/python -m unittest tests.test_runtime_panel`

Expected: all runtime panel tests pass.

- [x] **Step 5: Review the focused diff**

Run: `git diff --check -- sparkos/ui/runtime_panel.py tests/test_runtime_panel.py`

Expected: exit code 0 with no whitespace errors.

---

### Task 2: Chat workspace integration and cancellation state

**Files:**
- Modify: `sparkos/ui/chat_app.py`
- Modify: `sparkos/ui/history_screen.py`
- Modify: `tests/test_ui_integration.py`

**Interfaces:**
- Consumes: `RuntimePanel` from Task 1 and the existing `AgentRuntime.run()` event stream.
- Produces: `#workspace`, `#conversation-pane`, `#runtime-sidebar`, `#runtime-panel`, responsive `compact` layout state, and a working `action_cancel_generation()`.

- [x] **Step 1: Write failing headless UI tests**

```python
async with ChatApp().run_test(size=(120, 40)) as pilot:
    panel = app.query_one("#runtime-panel", RuntimePanel)
    self.assertIsNotNone(panel)
    await pilot.resize_terminal(80, 40)
    self.assertTrue(app.query_one("#workspace").has_class("compact"))
```

- [x] **Step 2: Run tests and verify RED**

Run: `.venv/bin/python -m unittest tests.test_ui_integration`

Expected: failure because the runtime panel and workspace selectors are absent.

- [x] **Step 3: Compose and wire the responsive workspace**

```python
with Horizontal(id="workspace"):
    with Vertical(id="conversation-pane"):
        with VerticalScroll(id="chat"):
            pass
    with VerticalScroll(id="runtime-sidebar"):
        yield RuntimePanel(id="runtime-panel")
```

Call `panel.begin_task(task)` on submit, `panel.handle_event(event)` for every runtime event, `panel.cancel()` from Escape, and `panel.reset()` for `/clear` and loaded history.

- [x] **Step 4: Run UI tests and verify GREEN**

Run: `.venv/bin/python -m unittest tests.test_ui_integration`

Expected: all integration tests pass at wide and compact widths.

- [x] **Step 5: Review the focused diff**

Run: `git diff --check -- sparkos/ui/chat_app.py sparkos/ui/history_screen.py tests/test_ui_integration.py`

Expected: exit code 0 with no whitespace errors.

---

### Task 3: Full verification and visual QA

**Files:**
- Verify: `sparkos/ui/runtime_panel.py`
- Verify: `sparkos/ui/chat_app.py`
- Verify: `tests/test_runtime_panel.py`
- Verify: `tests/test_ui_integration.py`

**Interfaces:**
- Consumes: the completed runtime dashboard and responsive workspace.
- Produces: a verified TUI at 120x40 and 80x40 terminal sizes.

- [x] **Step 1: Run the full unit suite**

Run: `.venv/bin/python -m unittest discover -s tests`

Expected: all tests pass with zero failures and errors.

- [x] **Step 2: Run static checks**

Run: `.venv/bin/ruff check sparkos tests`

Expected: `All checks passed!`

- [x] **Step 3: Exercise representative runtime states headlessly**

```python
async with ChatApp().run_test(size=(120, 40)) as pilot:
    panel.begin_task(task)
    for event in representative_events:
        panel.handle_event(event)
    await pilot.pause()
```

Confirm task title, active phase, step statuses, tool counts, and activity lines remain visible without overlapping the prompt.

- [x] **Step 4: Exercise compact layout**

Resize the same headless app to `80x40` and confirm the runtime sidebar stacks below the chat with a bounded height and independent scrolling.

- [x] **Step 5: Check the final diff**

Run: `git diff --check && git status --short`

Expected: no whitespace errors; only intentional project changes plus the user's pre-existing dirty files are present.

---

### Task 4: Post-review lifecycle and text-safety hardening

**Files:**
- Modify: `sparkos/ui/runtime_panel.py`
- Modify: `sparkos/ui/chat_app.py`
- Modify: `sparkos/ui/history_screen.py`
- Modify: `sparkos/ui/file_browser_screen.py`
- Modify: `tests/test_runtime_panel.py`
- Modify: `tests/test_ui_integration.py`

**Interfaces:**
- Consumes: Rich `Text`, Textual `get_current_worker()`, and existing dashboard state transitions.
- Produces: literal untrusted-text rendering, owner-scoped worker cleanup, interrupted step state, and early response-phase display.

- [x] **Step 1: Reproduce malformed markup with a mounted widget**

Run: `.venv/bin/python -m unittest tests.test_ui_integration.ChatAppLayoutTests.test_runtime_panel_treats_malformed_markup_as_literal_text`

Observed before fix: `textual.markup.MarkupError` for incomplete and link-like task text.

- [x] **Step 2: Render untrusted values as literal Text**

Use `rich.text.Text` for styled dashboard content and `markup=False` for plain chat, status, history, file-path, skill, and tool-detail widgets.

- [x] **Step 3: Reproduce and fix exclusive-worker cleanup ownership**

Run: `.venv/bin/python -m unittest tests.test_ui_integration.ChatAppLayoutTests.test_replaced_worker_cannot_clear_active_worker_state`

Only the `get_current_worker()` identity that still matches `_generation_worker` may clear worker state and re-enable the prompt.

- [x] **Step 4: Make cancellation and response phases accurate**

Transition transient step states to `cancelled` on Escape, and enter `responding` as soon as every current-plan step has succeeded.

- [x] **Step 5: Verify review fixes**

Run: `.venv/bin/python -m unittest tests.test_runtime_panel tests.test_ui_integration tests.test_agent_runtime`

Expected: all focused tests pass with no worker lifecycle, markup, cancellation, or phase regressions.

- [x] **Step 6: Keep `/clear` cleanup owner-scoped**

Cancel the active worker without clearing `_generation_worker` early, allowing the matching worker's `finally` block to restore the prompt after cleanup.

- [x] **Step 7: Handle fully preserved replans**

When every step in a replacement plan is an unchanged completed step, keep the preserved step states and transition directly to `responding`.
