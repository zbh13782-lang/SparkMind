# TUI Startup Feedback and Lazy Bootstrap Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Show the first Textual frame immediately, replace the fixed five-second wait with real progress feedback, and move preflight/runtime initialization into a cancellable background worker while preserving existing health semantics.

**Architecture:** Extract preflight orchestration from `main.py` into `sparkos/startup/preflight.py` with an optional progress callback. `ChatApp` starts with no runtime on the production path, renders `StartupPanel` with a spinner/stage text, runs preflight asynchronously, constructs `AgentRuntime` in `asyncio.to_thread`, then reveals the normal workspace. A lazy `runtime` property keeps existing tests/integrations that explicitly access or assign `app.runtime` compatible.

**Tech Stack:** Python 3.14, Textual >= 0.80 workers, `asyncio`, `asyncio.to_thread`, Rich, unittest/pytest.

## Global Constraints

- LLM preflight remains hard-fail; failed LLM exits with return code `1` after the failure is rendered.
- Docker, Spark/Hive, and Advisor remain soft/degraded exactly as today.
- Remove unconditional `time.sleep(5)`; no artificial delay is permitted.
- Keep `PreflightResult` and the four named result rows in their current order.
- Do not run network, Docker, catalog refresh, or `AgentRuntime()` from `ChatApp.__init__` in the production path.
- Keep test construction deterministic via injected runtime, runtime factory, and preflight runner.

## File Map

- Create: `sparkos/startup/__init__.py` — public startup exports.
- Create: `sparkos/startup/preflight.py` — `PreflightResult`, `run_preflight`, and report rendering.
- Create: `sparkos/ui/startup_panel.py` — spinner/stage widget.
- Create: `tests/test_startup_flow.py` — startup worker, failure, cancellation, and no-sleep tests.
- Modify: `main.py:1-105` — compatibility shim that only runs `ChatApp`.
- Modify: `sparkos/ui/chat_app.py:1-214, 222-260, 261-317` — lazy runtime/startup worker/input guards.
- Modify: `tests/test_health.py:1-125` — progress callback and extracted-module mocks.
- Modify: `tests/test_ui_integration.py:35-45` — explicit lazy-property compatibility checks.
- Create: `scripts/measure_startup.py` — local constructor timing probe.

### Task 1: Extract preflight and expose real stage progress

**Files:** Create `sparkos/startup/__init__.py`, `sparkos/startup/preflight.py`; modify `main.py` and `tests/test_health.py`.

**Interfaces:** `run_preflight(on_progress: Callable[[str, str], None] | None = None) -> PreflightResult`; `render_preflight_report` and `PreflightResult` remain importable from `main.py` as compatibility exports.

- [ ] **Step 1: Write the failing progress test**

Append to `tests/test_health.py`:

```python
class PreflightProgressTests(unittest.IsolatedAsyncioTestCase):
    async def test_preflight_reports_each_real_stage_in_order(self) -> None:
        progress: list[tuple[str, str]] = []
        with (
            patch("config.health.check_llm", new=AsyncMock(return_value=CheckResult(True, "ok"))),
            patch("config.health.check_docker", new=AsyncMock(return_value=CheckResult(True, "docker"))),
            patch("config.health.check_spark_hive", new=AsyncMock(return_value=CheckResult(True, "spark"))),
            patch("config.health._check_advisor_with_fallback", new=AsyncMock(return_value=CheckResult(True, "advisor"))),
            patch("config.config.get_chat_config", return_value=object()),
        ):
            result = await run_preflight(lambda stage, detail: progress.append((stage, detail)))
        self.assertTrue(result.passed)
        self.assertEqual([stage for stage, _ in progress], ["llm", "docker", "spark-hive", "advisor"])
        self.assertTrue(all(detail for _, detail in progress))
```

- [ ] **Step 2: Run it to verify failure**

Run: `./.venv/bin/python -m pytest tests/test_health.py::PreflightProgressTests -q`.
Expected: FAIL because the current function has no callback argument.

- [ ] **Step 3: Move the implementation and add callback calls**

Create `sparkos/startup/__init__.py`:

```python
from .preflight import PreflightResult, render_preflight_report, run_preflight
__all__ = ["PreflightResult", "render_preflight_report", "run_preflight"]
```

Create `sparkos/startup/preflight.py` by moving the current `PreflightResult` and report-rendering code from `main.py`. Implement `run_preflight` with this exact control flow (the health functions and result tuple semantics stay unchanged):

```python
from collections.abc import Callable
ProgressCallback = Callable[[str, str], None]

async def run_preflight(on_progress: ProgressCallback | None = None) -> PreflightResult:
    from config.config import get_chat_config
    from config.health import CheckResult, _check_advisor_with_fallback, check_docker, check_llm, check_spark_hive
    def report(stage: str, detail: str) -> None:
        if on_progress is not None:
            on_progress(stage, detail)
    results: list[tuple[str, bool, str, bool]] = []
    report("llm", "正在连接模型服务")
    llm_result = await check_llm()
    results.append(("LLM", llm_result.ok, llm_result.detail, False))
    report("llm", llm_result.detail)
    if not llm_result.ok:
        return PreflightResult(passed=False, results=results)
    chat_config = get_chat_config()
    report("docker", "正在检查 Docker")
    docker_result = await check_docker()
    results.append(("Docker", docker_result.ok, docker_result.detail, docker_result.degraded))
    report("docker", docker_result.detail)
    if docker_result.ok:
        report("spark-hive", "正在刷新 Spark/Hive 目录")
        spark_hive_result = await check_spark_hive()
    else:
        spark_hive_result = CheckResult(ok=False, detail="Docker 不可用，跳过 Spark/Hive 检查", degraded=True)
    results.append(("Spark/Hive", spark_hive_result.ok, spark_hive_result.detail, spark_hive_result.degraded))
    report("spark-hive", spark_hive_result.detail)
    report("advisor", "正在检查 Advisor")
    advisor_result = await _check_advisor_with_fallback(chat_config)
    results.append(("Advisor", advisor_result.ok, advisor_result.detail, advisor_result.degraded))
    report("advisor", advisor_result.detail)
    return PreflightResult(passed=True, results=results)
```

Copy `render_preflight_report` unchanged. Update `tests/test_health.py` mocks to target `config.health` as before; only the function import moves to `sparkos.startup.preflight` (the `main` re-export keeps older imports working).

- [ ] **Step 4: Turn `main.py` into a compatibility shim**

Replace it with:

```python
from __future__ import annotations
from sparkos.startup.preflight import PreflightResult, render_preflight_report, run_preflight
from sparkos.ui.chat_app import ChatApp
__all__ = ["ChatApp", "PreflightResult", "render_preflight_report", "run_preflight", "main"]

def main() -> None:
    ChatApp().run()

if __name__ == "__main__":
    main()
```

- [ ] **Step 5: Run health tests and commit**

Run: `./.venv/bin/python -m pytest tests/test_health.py -q`.
Expected: existing order/skip/timeout tests plus the new callback test pass.

```bash
git add main.py sparkos/startup tests/test_health.py
git commit -m "refactor: extract startup preflight stages"
```

### Task 2: Add a first-frame startup panel

**Files:** Create `sparkos/ui/startup_panel.py`; modify `sparkos/ui/chat_app.py` and the stylesheet from the visual plan if that plan is already applied; create/modify `tests/test_startup_flow.py`.

**Interfaces:** `StartupPanel.set_stage(stage, detail)`, `.succeed(detail)`, and `.fail(detail)` update visible text while a `LoadingIndicator` animates.

- [ ] **Step 1: Write the widget test**

```python
class StartupPanelTests(unittest.IsolatedAsyncioTestCase):
    async def test_stage_text_is_visible(self) -> None:
        app = ChatApp(runtime=object())  # type: ignore[arg-type]
        async with app.run_test(size=(100, 30)):
            panel = app.query_one("#startup", StartupPanel)
            panel.set_stage("docker", "正在检查 Docker")
            self.assertIn("Docker", panel.render().plain)
            self.assertIn("启动中", panel.render().plain)
```

- [ ] **Step 2: Implement the panel**

Create `sparkos/ui/startup_panel.py`:

```python
from __future__ import annotations
from textual.app import ComposeResult
from textual.containers import Vertical
from textual.widgets import LoadingIndicator, Static

class StartupPanel(Vertical):
    def compose(self) -> ComposeResult:
        yield LoadingIndicator(id="startup-spinner")
        yield Static("正在启动 SparkMind", id="startup-title")
        yield Static("准备检查服务…", id="startup-detail", markup=False)

    def set_stage(self, stage: str, detail: str) -> None:
        title = {"llm": "连接模型", "docker": "检查 Docker", "spark-hive": "检查数据目录", "advisor": "检查 Advisor", "runtime": "加载运行时"}.get(stage, "正在启动")
        self.query_one("#startup-title", Static).update(f"{title} · 启动中")
        self.query_one("#startup-detail", Static).update(detail)

    def succeed(self, detail: str) -> None:
        self.query_one("#startup-title", Static).update("启动完成")
        self.query_one("#startup-detail", Static).update(detail)

    def fail(self, detail: str) -> None:
        self.query_one("#startup-title", Static).update("启动失败")
        self.query_one("#startup-detail", Static).update(detail)
```

- [ ] **Step 3: Compose it before the workspace and style it**

At the start of `ChatApp.compose`, yield `StartupPanel(id="startup")`. Initially hide `#workspace` with `display: none`; add these selectors to the TCSS:

```css
#startup { width: 1fr; height: 1fr; align: center middle; content-align: center middle; background: $background; }
#startup-spinner { width: 1fr; height: 3; color: $accent; content-align: center middle; }
#startup-title, #startup-detail { width: 1fr; content-align: center middle; }
#startup-title { text-style: bold; color: $text; }
#startup-detail { color: $text-muted; }
```

- [ ] **Step 4: Run the widget test and commit**

Run: `./.venv/bin/python -m pytest tests/test_startup_flow.py::StartupPanelTests -q`.
Expected: PASS.

```bash
git add sparkos/ui/startup_panel.py sparkos/ui/chat_app.py tests/test_startup_flow.py
git commit -m "feat: add visible tui startup panel"
```

### Task 3: Lazy-load `AgentRuntime` with a Textual worker

**Files:** Modify `sparkos/ui/chat_app.py`, `tests/test_startup_flow.py`, and `tests/test_ui_integration.py`.

**Interfaces:** `ChatApp(runtime=None, runtime_factory=None, preflight_runner=None)`, lazy `runtime` property, `initialize_runtime()` worker, and `_finish_startup()`.

- [ ] **Step 1: Write startup success/failure tests**

```python
async def test_successful_bootstrap_reveals_workspace(self) -> None:
    created: list[str] = []
    async def preflight(on_progress=None) -> PreflightResult:
        if on_progress: on_progress("llm", "模型正常")
        return PreflightResult(True, [("LLM", True, "模型正常", False)])
    def factory() -> object:
        created.append("runtime")
        return object()
    app = ChatApp(runtime_factory=factory, preflight_runner=preflight)  # type: ignore[arg-type]
    async with app.run_test(size=(100, 30)) as pilot:
        await pilot.pause(); await pilot.pause()
        self.assertEqual(created, ["runtime"])
        self.assertFalse(app.query_one("#startup").display)
        self.assertTrue(app.query_one("#workspace").display)

async def test_failed_preflight_is_rendered_and_exits(self) -> None:
    async def preflight(on_progress=None) -> PreflightResult:
        return PreflightResult(False, [("LLM", False, "模型不可达", False)])
    app = ChatApp(runtime_factory=object, preflight_runner=preflight)  # type: ignore[arg-type]
    with patch.object(app, "exit") as exit_mock:
        async with app.run_test(size=(100, 30)) as pilot:
            await pilot.pause(); await pilot.pause()
            self.assertIn("启动失败", app.query_one("#startup-title", Static).render().plain)
            exit_mock.assert_called_once_with(return_code=1, message="模型不可达")
```

- [ ] **Step 2: Run to verify failure**

Run: `./.venv/bin/python -m pytest tests/test_startup_flow.py -q`.
Expected: FAIL because `ChatApp` constructs `AgentRuntime` eagerly and has no startup worker/injection parameters.

- [ ] **Step 3: Implement injection and lazy property**

Use these exact constructor semantics:

```python
def __init__(self, tools=None, *, runtime=None, runtime_factory=None, preflight_runner=None):
    super().__init__()
    self._runtime = runtime
    self._runtime_factory = runtime_factory or (lambda: AgentRuntime(enable_planning=True, tools=tools))
    self._preflight_runner = preflight_runner or run_preflight
    self._startup_worker = None
    self._generation_worker = None

@property
def runtime(self) -> AgentRuntime:
    if self._runtime is None:
        self._runtime = self._runtime_factory()
    return self._runtime

@runtime.setter
def runtime(self, value: AgentRuntime) -> None:
    self._runtime = value
```

Production `ChatApp().run()` must not read `self.runtime` before initialization; tests that explicitly do `app.runtime` retain the old behavior.

- [ ] **Step 4: Start worker in `on_mount` and reveal the workspace**

```python
async def on_mount(self) -> None:
    self._apply_responsive_layout(self.size.width)
    if self._runtime is None:
        self._startup_worker = self.initialize_runtime()
    else:
        self._finish_startup()

def _finish_startup(self) -> None:
    self.query_one("#startup", StartupPanel).display = False
    self.query_one("#workspace", Horizontal).display = True
    prompt = self.query_one("#prompt", Input)
    prompt.disabled = False
    prompt.focus()
    self.query_one("#status", Static).update("就绪")

@work(exclusive=True, group="startup", exit_on_error=False)
async def initialize_runtime(self) -> None:
    startup = self.query_one("#startup", StartupPanel)
    try:
        result = await self._preflight_runner(startup.set_stage)
        if not result.passed:
            detail = next((detail for name, ok, detail, _ in result.results if name == "LLM" and not ok), "LLM 检查失败")
            startup.fail(detail)
            self.exit(return_code=1, message=detail)
            return
        startup.set_stage("runtime", "正在加载 Agent Runtime")
        self._runtime = await asyncio.to_thread(self._runtime_factory)
        startup.succeed("服务检查完成")
        self._finish_startup()
    except Exception as exc:
        detail = f"{type(exc).__name__}: {exc}"
        startup.fail(detail)
        self.exit(return_code=1, message=detail)
```

- [ ] **Step 5: Guard input while bootstrap is active**

At the beginning of `on_input_changed`, if `_runtime is None`, clear `#slash-hint` and return. At the beginning of `on_input_submitted`, if `_runtime is None`, update status to `正在启动，请稍候…`, clear the input, and return. This prevents a half-initialized runtime from receiving work.

- [ ] **Step 6: Run focused tests and commit**

Run: `./.venv/bin/python -m pytest tests/test_startup_flow.py tests/test_ui_integration.py tests/test_health.py -q`.
Expected: all pass; successful bootstrap constructs one runtime; failure renders `启动失败` and exits with code `1`.

```bash
git add sparkos/ui/chat_app.py tests/test_startup_flow.py tests/test_ui_integration.py
git commit -m "feat: bootstrap tui runtime asynchronously"
```

### Task 4: Verify cancellation, timing, and full regression

**Files:** Modify `tests/test_startup_flow.py`; create `scripts/measure_startup.py`.

- [ ] **Step 1: Add cancellation coverage**

Use a preflight runner that sets an `asyncio.Event` and waits forever; after the event is set, cancel `app._startup_worker` and assert `_runtime is None`. The test must also assert the startup panel remains visible and no generator worker was started.

- [ ] **Step 2: Add the timing probe**

Create `scripts/measure_startup.py`:

```python
from __future__ import annotations
import time
from sparkos.ui.chat_app import ChatApp
started = time.perf_counter()
ChatApp()
print(f"constructor_seconds={time.perf_counter() - started:.3f}")
print("first_frame=observed StartupPanel; ready=observed workspace reveal")
```

- [ ] **Step 3: Run focused and full suites**

Run: `./.venv/bin/python -m pytest tests/test_startup_flow.py tests/test_health.py tests/test_ui_integration.py -q`; then `./.venv/bin/python -m pytest -q`.
Expected: all pass; constructor timing contains no network/Docker work and no fixed five-second pause.

- [ ] **Step 4: Manually verify real startup**

Run: `./.venv/bin/python main.py`.
Verify the first frame shows the spinner and stage text; stage text advances through actual checks; degraded dependencies still reach chat; failed LLM renders `启动失败` and exits with code `1`.

- [ ] **Step 5: Commit verification artifacts**

```bash
git add tests/test_startup_flow.py scripts/measure_startup.py
git commit -m "test: verify responsive startup bootstrap"
```

## Self-Review Checklist

- `time.sleep(5)` is gone.
- Production `ChatApp.__init__` does not construct `AgentRuntime`.
- Progress text reflects real preflight stages, not a fake timer.
- Existing health result semantics and row order remain unchanged.
- `app.runtime` access/assignment remains compatible for tests and integrations.
- LLM failure is still hard; other checks remain degraded/soft.
