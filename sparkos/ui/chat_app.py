"""SparkMind CLI — Textual 交互界面。"""

from __future__ import annotations

import asyncio
import traceback
from asyncio import sleep as async_sleep
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any, ClassVar

from rich.text import Text
from textual import events, work
from textual.app import App, ComposeResult
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.widgets import Footer, Header, Input, Markdown, Static
from textual.worker import Worker, get_current_worker

from sparkos.agent.events import (
    ClarificationRequested,
    PlanCreated,
    PlanReplanned,
    SkillActivated,
    StepCompleted,
    StepFailed,
    StepStarted,
    StepToolCompleted,
    TaskCompleted,
    TaskFailed,
    TextDelta,
)
from sparkos.agent.runtime import AgentRuntime
from sparkos.agent.skills.loader import infer_skill_name, load_skills, parse_slash_command
from sparkos.agent.task import AgentTask
from sparkos.startup.preflight import PreflightResult, ProgressCallback, run_preflight
from sparkos.ui.file_browser_screen import FileBrowserScreen
from sparkos.ui.history_screen import HistoryScreen
from sparkos.ui.runtime_panel import RuntimePanel
from sparkos.ui.skill_summary import SkillActivationSummary
from sparkos.ui.startup_panel import StartupPanel
from sparkos.ui.tool_summary import ToolCallSummary
from sparkos.ui.welcome_panel import WelcomePanel

RuntimeFactory = Callable[[], AgentRuntime]
PreflightRunner = Callable[[ProgressCallback | None], Awaitable[PreflightResult]]


class ChatApp(App):
    TITLE = "SparkMind"
    SUB_TITLE = "Agent Runtime"

    CSS_PATH = str(Path(__file__).with_name("chat_app.tcss"))

    BINDINGS: ClassVar[list[tuple[str, str, str]]] = [
        ("ctrl+c", "quit", "退出"),
        ("escape", "cancel_generation", "停止生成"),
    ]

    def __init__(
        self,
        tools: list[dict[str, Any]] | None = None,
        *,
        runtime: AgentRuntime | None = None,
        runtime_factory: RuntimeFactory | None = None,
        preflight_runner: PreflightRunner | None = None,
    ) -> None:
        super().__init__()
        self._runtime = runtime
        self._runtime_factory = runtime_factory or (
            lambda: AgentRuntime(
                enable_planning=True,
                tools=tools,
            )
        )
        self._preflight_runner = preflight_runner or run_preflight
        self._uses_default_preflight = preflight_runner is None
        self._startup_worker: Worker[None] | None = None
        self._generation_worker: Worker[None] | None = None

    @property
    def runtime(self) -> AgentRuntime:
        """Return the runtime, constructing it lazily for compatibility callers."""
        if self._runtime is None:
            self._runtime = self._runtime_factory()
        return self._runtime

    @runtime.setter
    def runtime(self, value: AgentRuntime) -> None:
        self._runtime = value

    def compose(self) -> ComposeResult:
        yield Header()

        yield StartupPanel(id="startup")

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
            yield Input(
                placeholder="今天想聊什么……(输入 / 以显示指令)",
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
        if self._runtime is not None:
            self._finish_startup()
        elif self.is_headless and self._uses_default_preflight:
            # Existing component tests should not perform network/Docker checks.
            # Keep construction in a Textual worker so the constructor remains lazy.
            self._startup_worker = self.initialize_runtime(run_preflight_checks=False)
        else:
            self._startup_worker = self.initialize_runtime()

    def _finish_startup(self) -> None:
        self.query_one("#startup", StartupPanel).display = False
        self.query_one("#workspace", Horizontal).display = True
        self.query_one("#composer", Vertical).display = True
        prompt = self.query_one("#prompt", Input)
        prompt.disabled = False
        prompt.focus()
        self.query_one("#status", Static).update("就绪")

    @work(exclusive=True, group="startup", exit_on_error=False)
    async def initialize_runtime(self, *, run_preflight_checks: bool = True) -> None:
        """Run health checks and runtime construction without blocking the first frame."""
        startup = self.query_one("#startup", StartupPanel)
        try:
            if run_preflight_checks:
                result = await self._preflight_runner(startup.set_stage)
                if not result.passed:
                    detail = next(
                        (
                            detail
                            for name, ok, detail, _ in result.results
                            if name == "LLM" and not ok
                        ),
                        "LLM 检查失败",
                    )
                    startup.fail(detail)
                    self.exit(return_code=1, message=detail)
                    return

            if run_preflight_checks:
                startup.set_stage("runtime", "正在加载 Agent Runtime")
            self._runtime = await asyncio.to_thread(self._runtime_factory)
            if run_preflight_checks:
                startup.succeed("服务检查完成")
            self._finish_startup()
        except Exception as exc:  # noqa: BLE001
            detail = f"{type(exc).__name__}: {exc}"
            startup.fail(detail)
            self.exit(return_code=1, message=detail)

    def on_resize(self, event: events.Resize) -> None:
        self._apply_responsive_layout(event.size.width)

    def _apply_responsive_layout(self, width: int) -> None:
        workspace = self.query_one("#workspace", Horizontal)
        workspace.set_class(width < 100, "compact")

    def on_input_changed(self, event: Input.Changed) -> None:
        if self._runtime is None:
            self._slash_hint.update("")
            return

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

        if self._runtime is None:
            self.query_one("#status", Static).update("正在启动，请稍候…")
            event.input.value = ""
            return

        welcome_query = self.query("#welcome")
        if len(welcome_query) > 0:
            welcome_query.first(WelcomePanel).display = False
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
        if skill_name is None:
            skill_name = infer_skill_name(prompt_text, self.runtime.skills)
        skill_summary: SkillActivationSummary | None = None

        task = AgentTask(goal=prompt_text or prompt)
        self.query_one("#runtime-panel", RuntimePanel).begin_task(task)

        self._generation_worker = self.generate_answer(
            task=task,
            skill_name=skill_name,
            skill_summary=skill_summary,
            output=assistant_message,
        )

        chat.scroll_end(animate=False)

    @work(exclusive=True, group="ai-generation", exit_on_error=False)
    async def generate_answer(
        self,
        task: AgentTask,
        skill_name: str | None,
        output: Markdown,
        skill_summary: SkillActivationSummary | None = None,
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
        displayed_skills: set[tuple[str, str | None]] = set()
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
                elif isinstance(event, SkillActivated):
                    if skill_summary is None:
                        skill_summary = SkillActivationSummary()
                        await chat.mount(skill_summary)
                    key = (event.skill.name, event.step.id if event.step else None)
                    if key not in displayed_skills:
                        await skill_summary.add_skill(
                            event.skill,
                            step_id=event.step.id if event.step else None,
                            source=event.source,
                        )
                        displayed_skills.add(key)
                    status.update(f"已激活技能: {event.skill.name}")
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
            if skill_summary is not None:
                skill_summary.complete()
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
        self._welcome = WelcomePanel(id="welcome")
        await chat.mount(self._welcome)
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
