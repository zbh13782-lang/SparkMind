# Agent Runtime Split Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Split session context from agent orchestration, introduce Task/Plan boundaries, and make tool-call transcripts protocol-correct without changing the TUI's user-facing workflow.

**Architecture:** `AgentContext` owns conversation state, prompt assembly, compaction state, and session persistence. `AgentRuntime` receives one `AgentTask` per run, optionally asks an injected `Planner` for a `Plan`, drives the model/tool loop, records canonical assistant/tool messages, and emits typed runtime events for the UI. `OpenAIChatClient` becomes a one-model-turn transport and no longer executes tools.

**Tech Stack:** Python 3.14, dataclasses, typing Protocol, asyncio, OpenAI-compatible Chat Completions, Textual, standard-library unittest.

## Global Constraints

- Preserve existing uncommitted user changes and current TUI commands.
- Do not add third-party dependencies.
- Use `unittest` because pytest is not installed.
- Keep Session, Task, Plan, PlanStep, and ToolCall as distinct concepts.
- Do not persist Task state inside the Session JSON in this change.

---

### Task 1: Runtime domain types

**Files:**
- Create: `sparkos/agent/task.py`
- Create: `sparkos/agent/planner.py`
- Create: `sparkos/agent/events.py`
- Modify: `sparkos/infrastructure/llm/models.py`
- Test: `tests/test_agent_models.py`

**Interfaces:**
- Produces: `AgentTask`, `TaskStatus`, `Plan`, `PlanStep`, `Planner`, `PlanningContext`, `TextDelta`, `ToolCompleted`, `PlanCreated`, `TaskCompleted`, and canonical `ChatMessage.to_api_dict()` / `ToolCall.to_api_dict()`.
- Consumes: no new internal interfaces.

- [ ] **Step 1: Write failing serialization and lifecycle tests**

```python
def test_tool_message_serializes_tool_call_id(self):
    message = ChatMessage(role="tool", content="ok", tool_call_id="call-1")
    self.assertEqual(
        message.to_api_dict(),
        {"role": "tool", "content": "ok", "tool_call_id": "call-1"},
    )


def test_task_lifecycle_records_result(self):
    task = AgentTask(goal="analyze")
    task.start()
    task.succeed("done")
    self.assertEqual(task.status, TaskStatus.SUCCEEDED)
    self.assertEqual(task.result, "done")
```

- [ ] **Step 2: Run tests and verify missing types fail**

Run: `.venv/bin/python -m unittest tests.test_agent_models -v`

Expected: import failure for `sparkos.agent.task` or missing serialization methods.

- [ ] **Step 3: Implement focused dataclasses and protocols**

```python
class TaskStatus(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"


@dataclass
class AgentTask:
    goal: str
    id: str = field(default_factory=lambda: uuid.uuid4().hex)
    status: TaskStatus = TaskStatus.PENDING
    parent_task_id: str | None = None
    active_plan_id: str | None = None
    result: str | None = None
    error: str | None = None
```

`ChatMessage.to_api_dict()` must include only non-`None` `tool_calls` and `tool_call_id`; `ToolCall.to_api_dict()` must return the OpenAI function-call object.

- [ ] **Step 4: Run tests and verify they pass**

Run: `.venv/bin/python -m unittest tests.test_agent_models -v`

Expected: all model tests pass.

### Task 2: Extract AgentContext

**Files:**
- Create: `sparkos/agent/context.py`
- Test: `tests/test_agent_context.py`

**Interfaces:**
- Consumes: `ChatMessage`, `Skill`, tool schemas, existing `sparkos.agent.memory` functions.
- Produces: `AgentContext.build_messages(skills, tools, skill_name)`, recording helpers, compaction helpers, and session persistence methods.

- [ ] **Step 1: Write failing context tests**

```python
def test_build_messages_never_silently_drops_uncompacted_history(self):
    context = AgentContext()
    for index in range(WINDOW + 1):
        context.record_user(str(index))
    messages = context.build_messages(skills=[], tools=[])
    self.assertEqual([m.content for m in messages], [str(i) for i in range(WINDOW + 1)])


def test_tool_result_is_recorded_as_tool_message(self):
    context = AgentContext()
    context.record_tool("call-1", "result")
    self.assertEqual(context.history[-1].tool_call_id, "call-1")
```

- [ ] **Step 2: Run tests and verify AgentContext is missing**

Run: `.venv/bin/python -m unittest tests.test_agent_context -v`

Expected: import failure for `sparkos.agent.context`.

- [ ] **Step 3: Move context/session behavior out of runtime**

Implement `AgentContext` with `history`, `summary`, `summary_upto`, `session_id`, context construction, recording, compaction payload/apply methods, persistence, load, and clear. `build_messages` must include all uncompressed messages; Runtime will compact before context construction rather than silently slicing them.

- [ ] **Step 4: Run context tests**

Run: `.venv/bin/python -m unittest tests.test_agent_context -v`

Expected: all context tests pass.

### Task 3: Make AgentRuntime the orchestrator

**Files:**
- Rewrite: `sparkos/agent/runtime.py`
- Modify: `sparkos/infrastructure/llm/client.py`
- Test: `tests/test_agent_runtime.py`

**Interfaces:**
- Consumes: `AgentContext`, `AgentTask`, optional `Planner`, one-turn model client, and tool executor.
- Produces: `AgentRuntime.run(task, skill_name) -> AsyncIterator[AgentEvent]` and compatibility accessors for skills/history/session operations.

- [ ] **Step 1: Write failing canonical tool-loop test**

```python
async def test_runtime_records_assistant_call_before_tool_result(self):
    client = FakeClient(turns=[[ToolCall("c1", "read_file", "{}")], ["done"]])
    context = AgentContext()
    runtime = AgentRuntime(
        context=context, client=client, tools=[], tool_executor=lambda *_: "ok"
    )
    await collect(runtime.run(AgentTask(goal="work")))
    self.assertEqual(
        [m.role for m in context.history], ["user", "assistant", "tool", "assistant"]
    )
    self.assertEqual(context.history[2].tool_call_id, "c1")
```

Also test max tool rounds and optional Planner invocation.

- [ ] **Step 2: Run runtime tests and verify old runtime API fails**

Run: `.venv/bin/python -m unittest tests.test_agent_runtime -v`

Expected: failure because `AgentRuntime.run` and injected client/tool executor do not exist.

- [ ] **Step 3: Implement the orchestration loop**

Runtime must record this exact protocol sequence for tool use:

```python
ChatMessage(role="assistant", content=turn_text, tool_calls=[call.to_api_dict()])
ChatMessage(role="tool", content=result, tool_call_id=call.call_id)
```

It must emit text and tool events, update Task status, compact before building when needed, persist on completion/failure/cancellation, and stop after `max_tool_rounds`. Synchronous tools must execute with `asyncio.to_thread`.

Convert `OpenAIChatClient.chat_stream` to one model turn. Accumulate streamed tool arguments by tool-call index, not nullable delta ID. Keep `chat_once` for compaction.

- [ ] **Step 4: Run runtime and model tests**

Run: `.venv/bin/python -m unittest tests.test_agent_runtime tests.test_agent_models -v`

Expected: all tests pass.

### Task 4: Adapt the TUI and verify the repository

**Files:**
- Modify: `sparkos/ui/chat_app.py`
- Modify: `sparkos/ui/history_screen.py`
- Modify: `CLAUDE.md`

**Interfaces:**
- Consumes: `AgentRuntime.run`, runtime events, `AgentRuntime.context`.
- Produces: unchanged user interaction with runtime-owned execution.

- [ ] **Step 1: Add/import-level integration coverage**

Extend `tests/test_agent_runtime.py` with imports for the event classes consumed by the UI and verify a no-tool task emits `TextDelta` followed by `TaskCompleted`.

- [ ] **Step 2: Run the integration test before adapting UI**

Run: `.venv/bin/python -m unittest tests.test_agent_runtime -v`

Expected: the event sequence test fails until Runtime emits completion.

- [ ] **Step 3: Update UI to create AgentTask and render Runtime events**

`ChatApp` must no longer call `stream_ai_response`, `record_assistant`, compaction, or persistence directly. `HistoryScreen` must read `app.runtime.context.history` and skip `role="tool"` messages during transcript rendering.

- [ ] **Step 4: Run full verification**

Run: `.venv/bin/python -m unittest discover -s tests -v`

Expected: all tests pass.

Run: `.venv/bin/ruff check sparkos tests`

Expected: `All checks passed!`

Run: `.venv/bin/ruff format --check sparkos tests`

Expected: all files already formatted.

Run: `.venv/bin/python -m compileall -q sparkos tests`

Expected: exit code 0 with no output.
