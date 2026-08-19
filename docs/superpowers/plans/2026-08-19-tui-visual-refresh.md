# TUI Visual Refresh Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the existing Textual chat screen clearer and more polished without changing chat, slash-command, history, file-browser, streaming, or cancellation behavior.

**Architecture:** Keep `ChatApp` as the behavior owner and preserve all selectors/classes currently used by event handlers and tests. Extract the inline TCSS into a dedicated stylesheet, add a small reusable empty-state widget, and make the conversation, runtime dashboard, and composer explicit layout regions. The work is presentation-first and can be shipped independently of startup refactoring.

**Tech Stack:** Python 3.14, Textual >= 0.80, Rich `Text`, unittest/pytest, Textual `run_test` pilot.

## Global Constraints

- Preserve `#workspace`, `#conversation-pane`, `#chat`, `#runtime-sidebar`, `#runtime-panel`, `#status`, `#prompt`, and `#slash-hint`.
- Preserve `user-message`, `assistant-message`, `skill-summary`, `tool-summary`, `skill-call`, and `tool-call`.
- Preserve the compact breakpoint `width < 100` and vertical compact layout.
- Do not add a UI dependency; use Textual/Rich already in `pyproject.toml`.
- Keep all user-controlled text rendered with `markup=False`.
- Do not use yellow in the TUI palette; use cyan, magenta, green, red, and dim states.

## File Map

- Create: `sparkos/ui/chat_app.tcss` — stylesheet extracted from `ChatApp.CSS` and refined.
- Create: `sparkos/ui/welcome_panel.py` — empty-state widget.
- Modify: `sparkos/ui/chat_app.py:14-210, 261-317, 422-435` — stylesheet, layout, and empty-state lifecycle.
- Create: `tests/test_ui_visuals.py` — structural and responsive visual-contract tests.
- Modify: `tests/test_ui_integration.py:301-338` — clear-session assertion that ignores the persistent welcome widget.

### Task 1: Lock the visual contract with tests

**Files:** Create `tests/test_ui_visuals.py`; modify `tests/test_ui_integration.py:301-338`.

**Interfaces:** The tests consume `ChatApp`, `Input`, `VerticalScroll`, `AgentTask`, `TextDelta`, and `TaskCompleted`; they produce requirements for `#welcome` and `#composer` while retaining existing ids/classes.

- [ ] **Step 1: Write failing tests**

```python
from __future__ import annotations

import unittest
from typing import ClassVar

from textual.containers import VerticalScroll
from textual.widgets import Input

from sparkos.agent.events import TaskCompleted, TextDelta
from sparkos.agent.task import AgentTask
from sparkos.ui.chat_app import ChatApp


class SilentRuntime:
    skills: ClassVar[list[object]] = []

    async def run(self, task: AgentTask, skill_name: str | None = None):
        del skill_name
        yield TextDelta("ok")
        task.succeed("ok")
        yield TaskCompleted(task)


class ChatAppVisualTests(unittest.IsolatedAsyncioTestCase):
    async def test_empty_state_and_composer_are_present(self) -> None:
        app = ChatApp()
        app.runtime = SilentRuntime()  # type: ignore[assignment]
        async with app.run_test(size=(120, 40)):
            self.assertTrue(app.query_one("#welcome").display)
            self.assertIsNotNone(app.query_one("#composer"))
            self.assertIsInstance(app.query_one("#prompt"), Input)
            self.assertIsInstance(app.query_one("#chat"), VerticalScroll)

    async def test_empty_state_hides_for_message_and_returns_after_clear(self) -> None:
        app = ChatApp()
        app.runtime = SilentRuntime()  # type: ignore[assignment]
        async with app.run_test(size=(120, 40)) as pilot:
            prompt = app.query_one("#prompt", Input)
            await app.on_input_submitted(Input.Submitted(prompt, "hello"))
            await pilot.pause()
            self.assertFalse(app.query_one("#welcome").display)
            await app._handle_clear()
            self.assertTrue(app.query_one("#welcome").display)
            self.assertEqual(len(app.query(".user-message")), 0)

    async def test_compact_mode_keeps_runtime_sidebar_present(self) -> None:
        app = ChatApp()
        app.runtime = SilentRuntime()  # type: ignore[assignment]
        async with app.run_test(size=(80, 40)) as pilot:
            await pilot.pause()
            self.assertTrue(app.query_one("#workspace").has_class("compact"))
            self.assertIsNotNone(app.query_one("#runtime-sidebar"))

    async def test_user_text_remains_literal_after_refresh(self) -> None:
        app = ChatApp()
        app.runtime = SilentRuntime()  # type: ignore[assignment]
        prompt_text = "[not-markup] [bold]literal"
        async with app.run_test(size=(120, 40)) as pilot:
            prompt = app.query_one("#prompt", Input)
            await app.on_input_submitted(Input.Submitted(prompt, prompt_text))
            await pilot.pause()
            self.assertEqual(app.query_one(".user-message").render().plain, f"你：{prompt_text}")
```

Update the existing clear test at `tests/test_ui_integration.py:335-337` to assert zero `.user-message` and `.assistant-message` descendants rather than zero total children, because `#chat` now retains `#welcome`.

- [ ] **Step 2: Run the focused tests to verify failure**

Run: `./.venv/bin/python -m pytest tests/test_ui_visuals.py tests/test_ui_integration.py -q`.
Expected: failures for missing `#welcome`/`#composer`; no existing streaming or literal-text behavior should be changed.

- [ ] **Step 3: Commit the test contract**

```bash
git add tests/test_ui_visuals.py tests/test_ui_integration.py
git commit -m "test: define refreshed tui visual contract"
```

### Task 2: Add the visual primitives

**Files:** Create `sparkos/ui/welcome_panel.py` and `sparkos/ui/chat_app.tcss`.

**Interfaces:** `WelcomePanel(Static)` renders a stable empty-state message; `ChatApp` will load `chat_app.tcss` via `CSS_PATH`.

- [ ] **Step 1: Create `sparkos/ui/welcome_panel.py`**

```python
"""Empty state for the SparkMind conversation pane."""
from __future__ import annotations
from rich.text import Text
from textual.widgets import Static


class WelcomePanel(Static):
    def __init__(self, **kwargs: object) -> None:
        super().__init__(self._content(), markup=False, **kwargs)

    @staticmethod
    def _content() -> Text:
        text = Text()
        text.append("SPARKMIND\n", style="bold cyan")
        text.append("数据助手已准备好\n\n", style="bold")
        text.append("输入一个问题，或使用快捷命令开始：\n", style="dim")
        text.append("/skills", style="bold cyan")
        text.append("  查看技能    ", style="dim")
        text.append("/history", style="bold cyan")
        text.append("  打开历史会话\n", style="dim")
        text.append("/select", style="bold cyan")
        text.append("  选择文件    ", style="dim")
        text.append("Esc", style="bold magenta")
        text.append("  停止生成", style="dim")
        return text


__all__ = ["WelcomePanel"]
```

- [ ] **Step 2: Create `sparkos/ui/chat_app.tcss`**

Use this stylesheet as the complete replacement for the current inline CSS. It keeps every existing selector/class and adds the welcome/composer hierarchy:

```css
Screen { layout: vertical; background: $background; color: $text; }
Header { height: 3; background: $surface; color: $text; }
#workspace { height: 1fr; width: 1fr; padding: 1 1 0; }
#conversation-pane { height: 1fr; width: 1fr; padding-right: 1; }
#chat { height: 1fr; padding: 1 2; scrollbar-size: 1 1; }
#welcome { width: 1fr; min-height: 10; margin: 3 4; padding: 2 3; border: round $primary 35%; background: $surface; content-align: center middle; }
.user-message, .assistant-message { width: 94%; margin: 1 0; padding: 1 2; border: round $panel; }
.user-message { background: $primary 18%; border-left: thick $primary; }
.assistant-message { background: $surface; border-left: thick $success; }
#runtime-sidebar { width: 42; min-width: 32; height: 1fr; padding: 1 2; background: $surface; border: round $panel; border-left: solid $primary; }
#runtime-panel { width: 1fr; height: auto; }
.section-label { height: 1; margin-bottom: 1; color: $text-muted; text-style: bold; }
#workspace.compact { layout: vertical; }
#workspace.compact #conversation-pane { width: 1fr; height: 1fr; padding-right: 0; }
#workspace.compact #runtime-sidebar { width: 1fr; min-width: 1; height: 13; padding: 1 2; border-left: none; border-top: solid $primary; }
#composer { height: auto; padding: 0 1 1; background: $background; }
#status { height: 1; padding: 0 2; color: $text-muted; }
#prompt { height: 3; margin: 0 1; padding: 0 1; border: round $panel; background: $surface; }
#prompt:focus { border: round $accent; }
.slash-hint { height: auto; padding: 1 2 0; color: $text-muted; }
.tool-detail, .skill-detail { padding: 1 2; color: $text-muted; }
.tool-call, .skill-call { margin: 0 0 1 2; background: $surface; }
.tool-call { border-left: outer $accent; }
.skill-call { border-left: outer $success; }
.tool-summary, .skill-summary { margin: 0 0 1 2; background: $surface; }
.tool-summary { border-left: outer $primary; }
.skill-summary { border-left: outer $success; }
#history-list Button, #file-list Button { width: 1fr; border: none; }
#history-list Button.-active, #history-list Button:hover, #file-list Button:hover { background: $primary 20%; }
```

- [ ] **Step 3: Check stylesheet parsing and commit**

Run: `./.venv/bin/python -m pytest tests/test_ui_visuals.py -q`.
Expected: test failures only for composition; no CSS parse error.

```bash
git add sparkos/ui/welcome_panel.py sparkos/ui/chat_app.tcss
git commit -m "feat: add tui welcome panel and stylesheet"
```

### Task 3: Wire layout and empty-state lifecycle

**Files:** Modify `sparkos/ui/chat_app.py:14-210, 261-317, 422-435`.

**Interfaces:** Preserve `ChatApp` methods and event flow; add `#welcome`, `#composer`, and `WelcomePanel` without changing runtime event handling.

- [ ] **Step 1: Load the stylesheet and import the widget**

Add `from pathlib import Path` and `from sparkos.ui.welcome_panel import WelcomePanel`. Replace the full `CSS = """..."""` attribute with:

```python
CSS_PATH = str(Path(__file__).with_name("chat_app.tcss"))
```

- [ ] **Step 2: Replace `compose`**

```python
def compose(self) -> ComposeResult:
    yield Header()
    with Horizontal(id="workspace"):
        with Vertical(id="conversation-pane"):
            yield Static("对话", classes="section-label")
            with VerticalScroll(id="chat"):
                self._welcome = WelcomePanel(id="welcome")
                yield self._welcome
        with VerticalScroll(id="runtime-sidebar"):
            yield Static("运行监控", classes="section-label")
            yield RuntimePanel(id="runtime-panel")
    with Vertical(id="composer"):
        yield Static("就绪", id="status", markup=False)
        yield Input(placeholder="今天想聊点什么……(输入 / 以显示指令)", id="prompt")
        self._slash_hint = Static("", id="slash-hint", classes="slash-hint", markup=False)
        yield self._slash_hint
    yield Footer()
```

- [ ] **Step 3: Hide the empty state on submit**

Immediately after the existing `if not prompt: return` guard in `on_input_submitted`, add:

```python
self.query_one("#welcome", WelcomePanel).display = False
```

- [ ] **Step 4: Restore the empty state on clear**

Keep `await chat.remove_children()`, then add:

```python
self._welcome = WelcomePanel(id="welcome")
await chat.mount(self._welcome)
```

Keep the existing context/runtime/status reset statements unchanged.

- [ ] **Step 5: Run focused UI tests and commit**

Run: `./.venv/bin/python -m pytest tests/test_ui_visuals.py tests/test_ui_integration.py tests/test_runtime_panel.py -q`.
Expected: all focused tests pass.

```bash
git add sparkos/ui/chat_app.py
git commit -m "feat: refresh sparkmind tui layout"
```

### Task 4: Manual QA and regression gate

**Files:** Modify `tests/test_ui_visuals.py` only if a discovered layout edge case needs a regression test.

- [ ] **Step 1: Run the UI suite**

Run: `./.venv/bin/python -m pytest tests/test_ui_visuals.py tests/test_ui_integration.py tests/test_runtime_panel.py tests/test_skill_loader.py -q`.
Expected: all pass with no Textual CSS warnings.

- [ ] **Step 2: Run the app at 120, 100, and 80 columns**

Run: `./.venv/bin/python -c 'from sparkos.ui.chat_app import ChatApp; ChatApp().run()'`.
Check: welcome card is centered; composer is always visible; runtime sidebar moves below chat at 80 columns; long content scrolls; slash hints and literal user text remain correct.

- [ ] **Step 3: Run the full suite before declaring the visual work complete**

Run: `./.venv/bin/python -m pytest -q`.
Expected: all tests pass.

## Self-Review Checklist

- No behavior was moved out of `ChatApp`.
- Existing ids/classes and compact breakpoint remain intact.
- Clearing removes messages but recreates the welcome panel.
- User-controlled strings remain literal.
- The stylesheet is a standalone artifact that can be iterated without touching event logic.
