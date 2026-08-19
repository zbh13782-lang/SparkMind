from __future__ import annotations

import asyncio
import unittest
from pathlib import Path
from typing import ClassVar
from unittest.mock import AsyncMock, call, patch

from textual.containers import VerticalScroll
from textual.widgets import Input, Markdown

from sparkos.agent.events import (
    ClarificationRequested,
    PlanCreated,
    SkillActivated,
    StepStarted,
    StepToolCompleted,
    TaskCompleted,
    TextDelta,
)
from sparkos.agent.llm_planner import LLMPlanner
from sparkos.agent.planner import Plan, PlanStep
from sparkos.agent.runtime import AgentRuntime
from sparkos.agent.skills.loader import Skill
from sparkos.agent.task import AgentTask
from sparkos.infrastructure.llm.models import ToolCall
from sparkos.ui.chat_app import ChatApp
from sparkos.ui.runtime_panel import RuntimePanel
from sparkos.ui.skill_summary import SkillActivationSummary
from sparkos.ui.tool_summary import ToolCallSummary


class ChatAppIntegrationTests(unittest.TestCase):
    def test_chat_app_owns_runtime_facade(self) -> None:
        app = ChatApp()

        self.assertIsInstance(app.runtime, AgentRuntime)

    def test_chat_app_enables_llm_planner(self) -> None:
        app = ChatApp()

        self.assertIsInstance(app.runtime.planner, LLMPlanner)
        self.assertIs(app.runtime.planner.model, app.runtime.client)


class ChatAppLayoutTests(unittest.IsolatedAsyncioTestCase):
    async def test_activated_skills_are_grouped_under_one_summary(self) -> None:
        class SkillRuntime:
            skills: ClassVar[list[Skill]] = [
                Skill(
                    name="data-quality-test",
                    description="查询数据并执行多维质量测试。",
                    path=Path("sparkos/agent/skills/data-quality-test/SKILL.md"),
                )
            ]

            async def run(self, task: AgentTask, skill_name: str | None = None):
                del skill_name
                yield SkillActivated(self.skills[0], source="rule")
                yield TextDelta("done")
                task.succeed("done")
                yield TaskCompleted(task)

        app = ChatApp()
        app.runtime = SkillRuntime()  # type: ignore[assignment]

        async with app.run_test(size=(120, 40)) as pilot:
            prompt = app.query_one("#prompt", Input)
            await app.on_input_submitted(Input.Submitted(prompt, "查询订单并做质量测试"))
            await pilot.pause()

            summary = app.query(".skill-summary").first(SkillActivationSummary)
            self.assertEqual(summary.title, "激活了 1 个技能")
            self.assertEqual(len(summary.query(".skill-call")), 1)
            self.assertIn("data-quality-test", summary.query(".skill-call").first().title)
            detail = summary.query(".skill-detail").first().render().plain
            self.assertIn("查询数据并执行多维质量测试", detail)
            self.assertIn("规则触发", detail)

    async def test_text_deltas_are_rendered_with_frame_pacing(self) -> None:
        class StreamingRuntime:
            skills: ClassVar[list[object]] = []

            async def run(self, task: AgentTask, skill_name: str | None = None):
                del skill_name
                yield TextDelta("hello")
                yield TextDelta(" world")
                task.succeed("hello world")
                yield TaskCompleted(task)

        app = ChatApp()
        app.runtime = StreamingRuntime()  # type: ignore[assignment]
        pace = AsyncMock()

        async with app.run_test(size=(120, 40)) as pilot:
            prompt = app.query_one("#prompt", Input)
            with patch("sparkos.ui.chat_app.async_sleep", pace):
                await app.on_input_submitted(Input.Submitted(prompt, "greet"))
                await pilot.pause()

            answer = app.query(".assistant-message").first(Markdown)
            self.assertEqual(answer.source, "hello world")
            self.assertEqual(pace.await_args_list, [call(0.01), call(0.01)])

    async def test_clarification_is_rendered_and_prompt_is_reenabled(self) -> None:
        class ClarifyingRuntime:
            skills: ClassVar[list[object]] = []

            async def run(self, task: AgentTask, skill_name: str | None = None):
                del skill_name
                question = "请提供要分析的文件路径。"
                task.wait_for_input(question)
                yield ClarificationRequested(task, question)

        app = ChatApp()
        app.runtime = ClarifyingRuntime()  # type: ignore[assignment]

        async with app.run_test(size=(120, 40)) as pilot:
            prompt = app.query_one("#prompt", Input)
            await app.on_input_submitted(Input.Submitted(prompt, "帮我分析一下"))
            await pilot.pause()

            answer = app.query(".assistant-message").first(Markdown)
            self.assertEqual(answer.source, "请提供要分析的文件路径。")
            self.assertEqual(
                app.query_one("#runtime-panel", RuntimePanel).trace.task_status,
                "waiting_input",
            )
            self.assertEqual(app.query_one("#status").render().plain, "等待补充")
            self.assertFalse(prompt.disabled)

    async def test_tool_calls_are_grouped_under_one_summary(self) -> None:
        app = ChatApp()

        async with app.run_test(size=(120, 40)) as pilot:
            chat = app.query_one("#chat", VerticalScroll)
            summary = ToolCallSummary()
            await chat.mount(summary)
            await summary.add_tool(ToolCall("call-1", "read_file", "[unsafe]", "first result"))
            await summary.add_tool(ToolCall("call-2", "shell", '{"cmd":"pwd"}', "second result"))
            summary.complete()
            await pilot.pause()

            self.assertEqual(len(chat.query(".tool-summary")), 1)
            self.assertEqual(summary.title, "执行了 2 个工具")
            self.assertEqual(len(summary.query(".tool-call")), 2)
            details = [widget.render().plain for widget in summary.query(".tool-detail")]
            self.assertIn("[unsafe]", details[0])
            self.assertIn("second result", details[1])

    async def test_runtime_sidebar_is_responsive(self) -> None:
        app = ChatApp()

        async with app.run_test(size=(120, 40)) as pilot:
            panel = app.query_one("#runtime-panel", RuntimePanel)
            workspace = app.query_one("#workspace")
            self.assertIsNotNone(panel)
            self.assertFalse(workspace.has_class("compact"))

            await pilot.resize_terminal(80, 40)

            self.assertTrue(workspace.has_class("compact"))

    async def test_escape_cancels_worker_and_updates_runtime_panel(self) -> None:
        class BlockingRuntime:
            skills: ClassVar[list[object]] = []

            def __init__(self) -> None:
                self.started = asyncio.Event()

            async def run(self, task: AgentTask, skill_name: str | None = None):
                del task, skill_name
                self.started.set()
                await asyncio.Event().wait()
                if False:
                    yield

        app = ChatApp()
        runtime = BlockingRuntime()
        app.runtime = runtime  # type: ignore[assignment]

        async with app.run_test(size=(120, 40)) as pilot:
            output = Markdown()
            await app.query_one("#chat", VerticalScroll).mount(output)
            worker = app.generate_answer(AgentTask(goal="work"), None, output)
            app._generation_worker = worker
            panel = app.query_one("#runtime-panel", RuntimePanel)
            panel.begin_task(AgentTask(goal="work"))
            await runtime.started.wait()

            app.action_cancel_generation()

            self.assertEqual(panel.trace.task_status, "cancelled")
            self.assertIs(app._generation_worker, worker)
            self.assertTrue(app.query_one("#prompt", Input).disabled)

            await pilot.pause()

            self.assertIsNone(app._generation_worker)
            self.assertFalse(app.query_one("#prompt", Input).disabled)

    async def test_runtime_panel_treats_malformed_markup_as_literal_text(
        self,
    ) -> None:
        app = ChatApp()
        task = AgentTask(id="task-markup", goal="[not-closed \\[literal]")
        step = PlanStep(
            id="unsafe",
            description="[/bold] [link=https://example.com]step[/link]",
        )
        plan = Plan(task_id=task.id, steps=(step,))

        async with app.run_test(size=(120, 40)) as pilot:
            panel = app.query_one("#runtime-panel", RuntimePanel)
            panel.begin_task(task)
            panel.handle_event(PlanCreated(plan))
            panel.handle_event(StepStarted(step))
            panel.handle_event(
                StepToolCompleted(
                    step,
                    ToolCall("call-1", "[tool", "{}", "ok"),
                )
            )
            await pilot.pause()
            rendered = panel.render().plain
            self.assertIn(task.goal, rendered)
            self.assertIn("[/bold] [link=https://exa", rendered)
            self.assertIn("[tool", rendered)

    async def test_chat_treats_malformed_user_text_as_literal(self) -> None:
        class SilentRuntime:
            skills: ClassVar[list[object]] = []

            async def run(self, task: AgentTask, skill_name: str | None = None):
                del skill_name
                task.succeed("")
                yield TaskCompleted(task)

        app = ChatApp()
        app.runtime = SilentRuntime()  # type: ignore[assignment]
        prompt = "[not-closed [/bold] [link=x]link[/link] \\[literal]"

        async with app.run_test(size=(120, 40)) as pilot:
            input_widget = app.query_one("#prompt", Input)
            await app.on_input_submitted(Input.Submitted(input_widget, prompt))
            await pilot.pause()

            user_message = app.query(".user-message").first()
            self.assertEqual(user_message.render().plain, f"你：{prompt}")
            app.action_cancel_generation()

    async def test_replaced_worker_cannot_clear_active_worker_state(self) -> None:
        class BlockingRuntime:
            skills: ClassVar[list[object]] = []

            def __init__(self) -> None:
                self.call_count = 0
                self.first_started = asyncio.Event()
                self.second_started = asyncio.Event()

            async def run(self, task: AgentTask, skill_name: str | None = None):
                del task, skill_name
                self.call_count += 1
                started = self.first_started if self.call_count == 1 else self.second_started
                started.set()
                await asyncio.Event().wait()
                if False:
                    yield

        app = ChatApp()
        runtime = BlockingRuntime()
        app.runtime = runtime  # type: ignore[assignment]

        async with app.run_test(size=(120, 40)) as pilot:
            chat = app.query_one("#chat", VerticalScroll)
            first_output = Markdown()
            second_output = Markdown()
            await chat.mount(first_output, second_output)

            first_worker = app.generate_answer(
                AgentTask(goal="first"),
                None,
                first_output,
            )
            app._generation_worker = first_worker
            await runtime.first_started.wait()

            second_worker = app.generate_answer(
                AgentTask(goal="second"),
                None,
                second_output,
            )
            app._generation_worker = second_worker
            await runtime.second_started.wait()
            await pilot.pause()

            self.assertIs(app._generation_worker, second_worker)
            self.assertTrue(app.query_one("#prompt", Input).disabled)
            app.action_cancel_generation()

    async def test_clear_waits_for_worker_cleanup_before_enabling_prompt(
        self,
    ) -> None:
        class StubContext:
            def clear(self) -> None:
                pass

        class BlockingRuntime:
            skills: ClassVar[list[object]] = []

            def __init__(self) -> None:
                self.started = asyncio.Event()
                self.context = StubContext()

            async def run(self, task: AgentTask, skill_name: str | None = None):
                del task, skill_name
                self.started.set()
                await asyncio.Event().wait()
                if False:
                    yield

        app = ChatApp()
        runtime = BlockingRuntime()
        app.runtime = runtime  # type: ignore[assignment]

        async with app.run_test(size=(120, 40)):
            output = Markdown()
            await app.query_one("#chat", VerticalScroll).mount(output)
            worker = app.generate_answer(AgentTask(goal="work"), None, output)
            app._generation_worker = worker
            await runtime.started.wait()

            await app._handle_clear()

            self.assertIsNone(app._generation_worker)
            self.assertFalse(app.query_one("#prompt", Input).disabled)
            self.assertEqual(len(app.query_one("#chat").query(".user-message")), 0)
            self.assertEqual(len(app.query_one("#chat").query(".assistant-message")), 0)


if __name__ == "__main__":
    unittest.main()
