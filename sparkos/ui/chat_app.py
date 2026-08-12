"""SparkMind CLI — Textual 交互界面。"""

from __future__ import annotations

import traceback
from asyncio import sleep as async_sleep
from typing import Any, ClassVar

from rich.text import Text
from textual import events, work
from textual.app import App, ComposeResult
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.widgets import Footer, Header, Input, Markdown, Static
from textual.worker import get_current_worker

from sparkos.agent.events import (
    ClarificationRequested,
    PlanCreated,
    PlanReplanned,
    StepCompleted,
    StepFailed,
    StepStarted,
    StepToolCompleted,
    TaskCompleted,
    TaskFailed,
    TextDelta,
)
from sparkos.agent.runtime import AgentRuntime
from sparkos.agent.skills.loader import load_skills, parse_slash_command
from sparkos.agent.task import AgentTask
from sparkos.ui.file_browser_screen import FileBrowserScreen
from sparkos.ui.history_screen import HistoryScreen
from sparkos.ui.runtime_panel import RuntimePanel
from sparkos.ui.tool_summary import ToolCallSummary


class ChatApp(App):
    TITLE = "SparkMind"
    SUB_TITLE = "Agent Runtime"

    CSS = """
    Screen {
        layout: vertical;
        background: $background;
    }

    #workspace {
        height: 1fr;
        width: 1fr;
    }

    #conversation-pane {
        height: 1fr;
        width: 1fr;
    }

    #chat {
        height: 1fr;
        padding: 1 2;
    }

    .user-message {
        margin: 1 0;
        padding: 1 2;
        background: $primary 20%;
        border-left: thick $primary;
    }

    .assistant-message {
        margin: 1 0;
        padding: 1 2;
        background: $surface;
        border-left: thick $success;
    }

    #runtime-sidebar {
        width: 42;
        min-width: 32;
        height: 1fr;
        padding: 1 2;
        background: $surface;
        border-left: solid $primary;
    }

    #runtime-panel {
        width: 1fr;
        height: auto;
    }

    #workspace.compact {
        layout: vertical;
    }

    #workspace.compact #conversation-pane {
        width: 1fr;
        height: 1fr;
    }

    #workspace.compact #runtime-sidebar {
        width: 1fr;
        min-width: 1;
        height: 13;
        padding: 1 2;
        border-left: none;
        border-top: solid $primary;
    }

    #prompt {
        margin: 0 1;
    }

    .slash-hint {
        height: auto;
        padding: 1 2;
        color: $text-muted;
    }

    .tool-detail {
        padding: 1 2;
        color: $text-muted;
    }

    .tool-call {
        margin: 0 0 1 2;
        background: $surface;
        border-left: outer $accent;
    }

    .tool-summary {
        margin: 0 0 1 2;
        background: $surface;
        border-left: outer $primary;
    }

    #history-list Button {
        width: 1fr;
        border: none;
    }

    #history-list Button.-active {
        background: $primary 30%;
    }

    #history-list Button:hover {
        background: $primary 20%;
    }

    #status {
        height: 1;
        padding: 0 2;
        color: $text-muted;
        background: $surface;
    }
    """

    BINDINGS: ClassVar[list[tuple[str, str, str]]] = [
        ("ctrl+c", "quit", "退出"),
        ("escape", "cancel_generation", "停止生成"),
    ]

    def __init__(self, tools: list[dict[str, Any]] | None = None) -> None:
        super().__init__()
        self.runtime = AgentRuntime(
            enable_planning=True,
            tools=tools,
        )
        self._generation_worker: object | None = None

    def compose(self) -> ComposeResult:
        yield Header()

        with Horizontal(id="workspace"):
            with Vertical(id="conversation-pane"), VerticalScroll(id="chat"):
                pass
            with VerticalScroll(id="runtime-sidebar"):
                yield RuntimePanel(id="runtime-panel")

        yield Static("就绪", id="status", markup=False)
        yield Input(
            placeholder="今天想聊点什么……(输入/ 以显示指令)",
            id="prompt",
        )
        self._slash_hint = Static(
            "",
            id="slash-hint",
            classes="slash-hint",
            markup=False,
        )
        yield self._slash_hint
        yield Footer()

    async def on_mount(self) -> None:
        self._apply_responsive_layout(self.size.width)
        self.query_one("#prompt", Input).focus()

    def on_resize(self, event: events.Resize) -> None:
        self._apply_responsive_layout(event.size.width)

    def _apply_responsive_layout(self, width: int) -> None:
        workspace = self.query_one("#workspace", Horizontal)
        workspace.set_class(width < 100, "compact")

    def on_input_changed(self, event: Input.Changed) -> None:
        input_widget = event.input
        value = input_widget.value

        if not value.startswith("/"):
            self._slash_hint.update("")
            return

        prefix = value[1:].casefold()

        # 内置命令
        builtin_commands: list[tuple[str, str]] = [
            ("/skills", "列出所有可用技能"),
            ("/choice", "选择历史会话"),
            ("/history", "查看历史会话列表"),
            ("/clear", "清除当前对话"),
            ("/select", "选择文件"),
        ]

        # 匹配内置命令
        matched_builtin = [(cmd, desc) for cmd, desc in builtin_commands if cmd[1:].casefold().startswith(prefix)]

        # 匹配 skill
        skills = load_skills()
        matched_skills = [(f"/{s.name}", s.description) for s in skills if s.name.casefold().startswith(prefix)]

        all_matches = matched_builtin + matched_skills

        if all_matches:
            hint = Text()
            for index, (command, description) in enumerate(all_matches):
                hint.append(command, style="bold cyan")
                hint.append(f" — {description}")
                if index < len(all_matches) - 1:
                    hint.append("\n")
            self._slash_hint.update(hint)
        else:
            self._slash_hint.update("")

    async def on_input_submitted(self, event: Input.Submitted) -> None:
        prompt = event.value.strip()
        if not prompt:
            return

        event.input.value = ""

        if prompt == "/skills":
            await self._show_skills_list()
            self._slash_hint.update("")
            return

        if prompt == "/clear":
            await self._handle_clear()
            self._slash_hint.update("")
            return

        if prompt in ("/history", "/choice"):
            await self._show_history()
            self._slash_hint.update("")
            return

        if prompt == "/select":
            await self._show_file_browser()
            self._slash_hint.update("")
            return

        self._slash_hint.update("")

        chat = self.query_one("#chat", VerticalScroll)

        user_message = Static(
            f"你：{prompt}",
            classes="user-message",
            markup=False,
        )
        assistant_message = Markdown("", classes="assistant-message")

        await chat.mount(user_message)
        await chat.mount(assistant_message)

        skill_name, prompt_text = parse_slash_command(prompt, self.runtime.skills)

        task = AgentTask(goal=prompt_text or prompt)
        self.query_one("#runtime-panel", RuntimePanel).begin_task(task)

        self._generation_worker = self.generate_answer(
            task=task,
            skill_name=skill_name,
            output=assistant_message,
        )

        chat.scroll_end(animate=False)

    @work(exclusive=True, group="ai-generation", exit_on_error=False)
    async def generate_answer(
        self,
        task: AgentTask,
        skill_name: str | None,
        output: Markdown,
    ) -> None:
        generation_worker = get_current_worker()
        prompt_input = self.query_one("#prompt", Input)
        status = self.query_one("#status", Static)
        chat = self.query_one("#chat", VerticalScroll)
        runtime_panel = self.query_one("#runtime-panel", RuntimePanel)

        prompt_input.disabled = True
        status.update("正在思考……")

        markdown_stream = Markdown.get_stream(output)
        received_text = False
        tool_summary: ToolCallSummary | None = None

        try:
            async for event in self.runtime.run(task, skill_name=skill_name):
                runtime_panel.handle_event(event)
                if isinstance(event, TextDelta):
                    if not received_text:
                        received_text = True
                        status.update("正在生成……")
                    await markdown_stream.write(event.text)
                    await async_sleep(0.01)
                elif isinstance(event, ClarificationRequested):
                    received_text = True
                    await markdown_stream.write(event.question)
                    status.update("等待补充")
                elif isinstance(event, StepToolCompleted):
                    if tool_summary is None:
                        tool_summary = ToolCallSummary()
                        await chat.mount(tool_summary)
                    await tool_summary.add_tool(event.tool_call)
                    status.update(f"步骤 {event.step.id} 工具: {event.tool_call.name}")
                elif isinstance(event, PlanCreated):
                    status.update(f"已生成计划（{len(event.plan.steps)} 步）")
                elif isinstance(event, PlanReplanned):
                    status.update(f"已重新规划（v{event.plan.version}，{len(event.plan.steps)} 步）")
                elif isinstance(event, StepStarted):
                    status.update(f"正在执行: {event.step.description}")
                elif isinstance(event, StepCompleted):
                    status.update(f"步骤完成: {event.step.description}")
                elif isinstance(event, StepFailed):
                    status.update(f"步骤失败: {event.step.description}")
                elif isinstance(event, TaskCompleted):
                    status.update("生成完成")
                elif isinstance(event, TaskFailed):
                    status.update("请求失败")

                distance_to_bottom = chat.max_scroll_y - chat.scroll_y
                if distance_to_bottom < 3:
                    chat.scroll_end(animate=False)

        except Exception as exc:
            self.log(f"[red]请求失败:[/red] {type(exc).__name__}: {exc}")
            traceback.print_exc()
            await markdown_stream.write(
                f"\n\n**请求失败：** `{type(exc).__name__}`: {exc}\n\n"
                "请重试；如果持续失败，请查看 Task 快照或模型服务日志。"
            )
            status.update("请求失败")
            raise

        finally:
            if tool_summary is not None:
                tool_summary.complete()
            await markdown_stream.stop()
            if self._generation_worker is generation_worker:
                self._generation_worker = None
                prompt_input.disabled = False
                prompt_input.focus()

    def action_cancel_generation(self) -> None:
        """Cancel the active runtime worker and reflect it in the dashboard."""
        worker = self._generation_worker
        if worker is None:
            return
        getattr(worker, "cancel", lambda: None)()
        self.query_one("#runtime-panel", RuntimePanel).cancel()
        self.query_one("#status", Static).update("已停止")

    async def _handle_clear(self) -> None:
        """清除当前对话，新建会话。"""
        # 取消正在进行的生成，防止孤儿 worker 写入已清空的历史
        worker = self._generation_worker
        if worker is not None:
            getattr(worker, "cancel", lambda: None)()

        self.runtime.context.clear()
        chat = self.query_one("#chat", VerticalScroll)
        await chat.remove_children()
        self.query_one("#runtime-panel", RuntimePanel).reset()
        self.query_one("#status", Static).update("就绪")
        self._slash_hint.update("")

    async def _show_skills_list(self) -> None:
        chat = self.query_one("#chat", VerticalScroll)
        skills = load_skills()

        lines = ["可用技能："]
        for s in skills:
            lines.append(f"  /{s.name} — {s.description}")
        if not skills:
            lines.append("  （暂无）")

        await chat.mount(
            Static(
                "\n".join(lines),
                classes="assistant-message",
                markup=False,
            )
        )
        chat.scroll_end(animate=False)

    async def _show_history(self) -> None:
        """显示历史会话选择界面。"""
        self.push_screen(HistoryScreen())

    async def _show_file_browser(self) -> None:
        """弹出文件选择界面，选中文件后填入输入框供用户继续编辑。"""

        def _on_select(path: str) -> None:
            prompt_input = self.query_one("#prompt", Input)
            prompt_input.value = f"选择文件：{path}，"
            prompt_input.focus()

        self.push_screen(FileBrowserScreen(), _on_select)


if __name__ == "__main__":
    ChatApp().run()
