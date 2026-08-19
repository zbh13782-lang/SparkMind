from __future__ import annotations

import asyncio
import unittest
from collections.abc import Callable
from unittest.mock import patch

from textual.widgets import Input, Static
from textual.worker import Worker, WorkerCancelled

from main import PreflightResult
from sparkos.ui.chat_app import ChatApp


class StartupPanelTests(unittest.IsolatedAsyncioTestCase):
    async def test_stage_text_is_visible(self) -> None:
        from sparkos.ui.startup_panel import StartupPanel

        app = ChatApp(runtime=object())  # type: ignore[arg-type]

        async with app.run_test(size=(100, 30)):
            panel = app.query_one("#startup", StartupPanel)
            panel.set_stage("docker", "正在检查 Docker")
            title = app.query_one("#startup-title", Static).render().plain
            detail = app.query_one("#startup-detail", Static).render().plain
            self.assertIn("Docker", title)
            self.assertIn("启动中", title)
            self.assertEqual(detail, "正在检查 Docker")


class StartupFlowTests(unittest.IsolatedAsyncioTestCase):
    def test_constructor_does_not_create_runtime(self) -> None:
        created: list[str] = []

        def factory() -> object:
            created.append("runtime")
            return object()

        ChatApp(runtime_factory=factory)
        self.assertEqual(created, [])

    async def test_successful_bootstrap_reveals_workspace(self) -> None:
        created: list[str] = []

        async def preflight(
            on_progress: Callable[[str, str], None] | None = None,
        ) -> PreflightResult:
            if on_progress is not None:
                on_progress("llm", "模型正常")
            return PreflightResult(True, [("LLM", True, "模型正常", False)])

        def factory() -> object:
            created.append("runtime")
            return object()

        app = ChatApp(runtime_factory=factory, preflight_runner=preflight)  # type: ignore[arg-type]

        async with app.run_test(size=(100, 30)) as pilot:
            await pilot.pause()
            await pilot.pause()
            self.assertEqual(created, ["runtime"])
            self.assertFalse(app.query_one("#startup").display)
            self.assertTrue(app.query_one("#workspace").display)
            self.assertTrue(app.query_one("#composer").display)
            self.assertTrue(app.query_one("#prompt", Input).has_focus)
            self.assertIsNotNone(app._runtime)

    async def test_failed_preflight_is_rendered_and_exits(self) -> None:
        async def preflight(
            on_progress: Callable[[str, str], None] | None = None,
        ) -> PreflightResult:
            return PreflightResult(False, [("LLM", False, "模型不可达", False)])

        app = ChatApp(runtime_factory=object, preflight_runner=preflight)  # type: ignore[arg-type]
        with patch.object(app, "exit") as exit_mock:
            async with app.run_test(size=(100, 30)) as pilot:
                await pilot.pause()
                await pilot.pause()
                self.assertIn("启动失败", app.query_one("#startup-title", Static).render().plain)
                self.assertEqual(app.query_one("#startup-detail", Static).render().plain, "模型不可达")
                self.assertIsNone(app._runtime)
                exit_mock.assert_called_once_with(return_code=1, message="模型不可达")

    async def test_startup_worker_can_be_cancelled_before_runtime_creation(self) -> None:
        started = asyncio.Event()

        async def preflight(
            on_progress: Callable[[str, str], None] | None = None,
        ) -> PreflightResult:
            started.set()
            await asyncio.Event().wait()
            raise AssertionError("unreachable")

        app = ChatApp(runtime_factory=object, preflight_runner=preflight)  # type: ignore[arg-type]
        async with app.run_test(size=(100, 30)):
            await started.wait()
            worker = app._startup_worker
            self.assertIsInstance(worker, Worker)
            worker.cancel()
            with self.assertRaises(WorkerCancelled):
                await worker.wait()
            self.assertIsNone(app._runtime)
            self.assertIsNone(app._generation_worker)
            self.assertTrue(app.query_one("#startup").display)

    async def test_input_is_rejected_cleanly_while_startup_is_active(self) -> None:
        started = asyncio.Event()

        async def preflight(
            on_progress: Callable[[str, str], None] | None = None,
        ) -> PreflightResult:
            started.set()
            await asyncio.Event().wait()
            raise AssertionError("unreachable")

        app = ChatApp(runtime_factory=object, preflight_runner=preflight)  # type: ignore[arg-type]
        async with app.run_test(size=(100, 30)):
            await started.wait()
            prompt = app.query_one("#prompt", Input)
            prompt.value = "启动时输入"

            await app.on_input_submitted(Input.Submitted(prompt, prompt.value))

            self.assertEqual(prompt.value, "")
            self.assertEqual(app.query_one("#status", Static).render().plain, "正在启动，请稍候…")
            self.assertIsNone(app._runtime)
            self.assertIsNone(app._generation_worker)

            worker = app._startup_worker
            self.assertIsInstance(worker, Worker)
            worker.cancel()
            with self.assertRaises(WorkerCancelled):
                await worker.wait()


if __name__ == "__main__":
    unittest.main()
