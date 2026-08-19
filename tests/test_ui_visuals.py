from __future__ import annotations

import unittest
from typing import ClassVar

from textual.containers import VerticalScroll
from textual.widgets import Input

from sparkos.agent.events import TaskCompleted, TextDelta
from sparkos.agent.task import AgentTask
from sparkos.ui.chat_app import ChatApp


class SilentContext:
    def clear(self) -> None:
        pass


class SilentRuntime:
    skills: ClassVar[list[object]] = []

    def __init__(self) -> None:
        self.context = SilentContext()

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

    async def test_user_text_remains_literal_after_visual_refresh(self) -> None:
        app = ChatApp()
        app.runtime = SilentRuntime()  # type: ignore[assignment]
        prompt_text = "[not-markup] [bold]literal"

        async with app.run_test(size=(120, 40)) as pilot:
            prompt = app.query_one("#prompt", Input)
            await app.on_input_submitted(Input.Submitted(prompt, prompt_text))
            await pilot.pause()
            self.assertEqual(
                app.query_one(".user-message").render().plain,
                f"你：{prompt_text}",
            )

class ChatAppBrandingTests(unittest.TestCase):
    def test_welcome_panel_contains_terminal_logo_and_no_yellow_style(self) -> None:
        from sparkos.ui.welcome_panel import WelcomePanel

        rendered = WelcomePanel._content()
        self.assertIn("╭─✦", rendered.plain)
        self.assertIn("DATA / INSIGHT", rendered.plain)
        self.assertIn("SPARKMIND", rendered.plain)
        self.assertNotIn("yellow", " ".join(str(span.style) for span in rendered.spans))

    def test_runtime_status_palette_has_no_yellow(self) -> None:
        from sparkos.ui.runtime_panel import _STEP_MARKS, _TASK_COLORS

        self.assertNotIn("yellow", _TASK_COLORS.values())
        self.assertNotIn("yellow", [color for _, color in _STEP_MARKS.values()])

    def test_tui_stylesheet_does_not_use_theme_accent(self) -> None:
        from pathlib import Path

        from sparkos.ui.chat_app import ChatApp

        stylesheet = Path(ChatApp.CSS_PATH).read_text(encoding="utf-8")
        self.assertNotIn("$accent", stylesheet)


if __name__ == "__main__":
    unittest.main()

class ChatAppRegressionTests(unittest.IsolatedAsyncioTestCase):
    async def test_focused_prompt_uses_cyan_border_not_theme_accent(self) -> None:
        from textual.color import Color

        app = ChatApp()
        app.runtime = SilentRuntime()  # type: ignore[assignment]

        async with app.run_test(size=(120, 40)):
            prompt = app.query_one("#prompt", Input)
            self.assertTrue(prompt.has_focus)
            self.assertEqual(prompt.styles.border.top[1], Color(34, 211, 238))

    async def test_can_continue_after_loading_another_session(self) -> None:
        from sparkos.infrastructure.llm.models import ChatMessage
        from sparkos.ui.history_screen import HistoryScreen

        class SessionContext(SilentContext):
            history: ClassVar[list[ChatMessage]] = [ChatMessage(role="user", content="previous question")]

            def load_session(self, session_id: str) -> bool:
                return session_id == "session-2"

        class HistoryRuntime(SilentRuntime):
            def __init__(self) -> None:
                super().__init__()
                self.context = SessionContext()

        app = ChatApp()
        app.runtime = HistoryRuntime()  # type: ignore[assignment]

        async with app.run_test(size=(120, 40)) as pilot:
            history_screen = HistoryScreen()
            app.push_screen(history_screen)
            await pilot.pause()
            await history_screen._load_session("session-2")
            await pilot.pause()

            welcome = app.query_one("#welcome")
            self.assertFalse(welcome.display)
            prompt = app.query_one("#prompt", Input)
            await app.on_input_submitted(Input.Submitted(prompt, "continue here"))
            await pilot.pause()

            self.assertEqual(len(app.query(".user-message")), 2)
