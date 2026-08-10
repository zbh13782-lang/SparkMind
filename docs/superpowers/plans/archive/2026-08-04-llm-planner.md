# LLM Planner Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Implement an LLM-backed Planner that decides whether a task needs planning, returns a validated dependency-aware Plan, and makes that Plan guide Runtime execution.

**Architecture:** `LLMPlanner` is a concrete implementation of the existing `Planner` protocol and depends only on a `chat_once` planning model port. It requests strict JSON, converts valid step data into domain `Plan` objects, and fails open to direct execution on malformed output. `AgentRuntime` remains the orchestrator and injects a successfully created Plan into the executor model context.

**Tech Stack:** Python 3.14, dataclasses, typing Protocol, JSON, standard-library unittest.

## Global Constraints

- Preserve the current dirty working tree and existing Runtime behavior.
- Add no third-party dependencies.
- Simple tasks may return no Plan.
- Reject duplicate step IDs, unknown dependencies, self-dependencies, cycles, empty descriptions, and plans over 12 steps.
- Planner failure must not fail the AgentTask.

---

### Task 1: Concrete LLMPlanner

**Files:**
- Create: `sparkos/agent/llm_planner.py`
- Test: `tests/test_llm_planner.py`

**Interfaces:**
- Consumes: `AgentTask`, `PlanningContext`, and `PlanningModel.chat_once(messages)`.
- Produces: `LLMPlanner.create_plan(task, context) -> Plan | None`.

- [ ] **Step 1: Write failing Planner tests**

```python
async def test_complex_task_returns_dependency_aware_plan(self):
    model = FakePlanningModel(
        '{"should_plan":true,"steps":[{"id":"s1","description":"inspect","depends_on":[]},{"id":"s2","description":"report","depends_on":["s1"]}]}'
    )
    plan = await LLMPlanner(model).create_plan(task, context)
    self.assertEqual(plan.steps[1].depends_on, ["s1"])


async def test_simple_task_returns_none(self):
    model = FakePlanningModel('{"should_plan":false,"steps":[]}')
    self.assertIsNone(await LLMPlanner(model).create_plan(task, context))
```

Also cover fenced JSON, malformed JSON, invalid dependencies, cycles, empty descriptions, and capability information in the planning request.

- [ ] **Step 2: Run tests and verify missing implementation fails**

Run: `.venv/bin/python -m unittest tests.test_llm_planner -v`

Expected: import failure for `sparkos.agent.llm_planner`.

- [ ] **Step 3: Implement prompt, parser, and Plan validation**

```python
class LLMPlanner:
    def __init__(self, model: PlanningModel, max_steps: int = 12): ...

    async def create_plan(
        self,
        task: AgentTask,
        context: PlanningContext,
    ) -> Plan | None: ...
```

The response contract is `{"should_plan": bool, "steps": [{"id": str, "description": str, "depends_on": [str]}]}`. Strip an optional Markdown JSON fence, validate the full graph, and return `None` for direct execution or invalid model output.

- [ ] **Step 4: Run Planner tests**

Run: `.venv/bin/python -m unittest tests.test_llm_planner -v`

Expected: all Planner tests pass.

### Task 2: Runtime Plan injection and TUI activation

**Files:**
- Modify: `sparkos/agent/runtime.py`
- Modify: `sparkos/ui/chat_app.py`
- Modify: `CLAUDE.md`
- Test: `tests/test_agent_runtime.py`
- Test: `tests/test_ui_integration.py`

**Interfaces:**
- Consumes: `Plan` returned by the injected Planner.
- Produces: a leading system execution-plan message and a TUI Runtime configured with `LLMPlanner`.

- [ ] **Step 1: Write failing Runtime injection and UI activation tests**

```python
async def test_runtime_injects_created_plan_into_executor_context(self):
    await collect_events(runtime, task)
    contents = [message["content"] for message in client.requests[0]]
    self.assertTrue(any("inspect" in content for content in contents))


def test_chat_app_enables_llm_planner(self):
    app = ChatApp()
    self.assertIsInstance(app.runtime.planner, LLMPlanner)
```

- [ ] **Step 2: Run tests and verify injection and activation fail**

Run: `.venv/bin/python -m unittest tests.test_agent_runtime tests.test_ui_integration -v`

Expected: failures because the Plan is not in the model request and ChatApp has no Planner.

- [ ] **Step 3: Inject the Plan and configure ChatApp**

After planning, serialize the plan as a system message before conversation history. Construct ChatApp's runtime with a shared `OpenAIChatClient` and `LLMPlanner(client)` so planning and execution use the same configured model transport.

- [ ] **Step 4: Run full verification**

Run: `.venv/bin/python -m unittest discover -s tests -v`

Expected: all tests pass.

Run: `.venv/bin/ruff check sparkos tests`

Expected: `All checks passed!`

Run: `.venv/bin/ruff format --check sparkos tests`

Expected: all files formatted.

Run: `.venv/bin/python -m compileall -q sparkos tests`

Expected: exit code 0 with no output.
