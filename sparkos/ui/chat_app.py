"""SparkMind CLI — Textual 交互界面。"""

from __future__ import annotations

from typing import Any, ClassVar

from textual import work
from textual.app import App, ComposeResult
from textual.containers import VerticalScroll
from textual.widgets import Collapsible, Footer, Header, Input, Markdown, Static

from sparkos.agent.memory import create_session, save_session
from sparkos.agent.skills.loader import (
    build_system_message,
    load_skills,
    parse_slash_command,
)
from sparkos.agent.tools.registry import TOOL_DEFINITIONS, execute_tool
from sparkos.infrastructure.llm.client import stream_ai_response
from sparkos.infrastructure.llm.models import ChatConfig, ChatMessage, ToolCall
from sparkos.ui.history_screen import HistoryScreen


class ChatApp(App):
    CSS = """
    Screen {
        layout: vertical;
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

    #prompt {
        dock: bottom;
        margin: 0 1 1 1;
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

    #history-list Button {
        width: 1fr;
        text-align: left;
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
        padding-left: 2;
        color: $text-muted;
    }
    """

    BINDINGS: ClassVar[list[tuple[str, str, str]]] = [
        ("ctrl+c", "quit", "退出"),
        ("escape", "cancel_generation", "停止生成"),
    ]

    def __init__(self) -> None:
        super().__init__()
        self._history: list[ChatMessage] = []
        self._generation_worker: object | None = None
        self._system_message: str = build_system_message(load_skills())
        self._session_id: str | None = None

    def compose(self) -> ComposeResult:
        yield Header()

        with VerticalScroll(id="chat"):
            pass

        yield Static("就绪", id="status")
        yield Input(
            placeholder="今天想聊点什么……",
            id="prompt",
        )
        self._slash_hint = Static("", id="slash-hint", classes="slash-hint")
        yield self._slash_hint
        yield Footer()

    async def on_mount(self) -> None:
        self.query_one("#prompt", Input).focus()

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
        ]

        # 匹配内置命令
        matched_builtin = [
            (cmd, desc) for cmd, desc in builtin_commands
            if cmd[1:].casefold().startswith(prefix)
        ]

        # 匹配 skill
        skills = load_skills()
        matched_skills = [
            (f"/{s.name}", s.description) for s in skills
            if s.name.casefold().startswith(prefix)
        ]

        all_matches = matched_builtin + matched_skills

        if all_matches:
            lines = [
                f"[bold cyan]{cmd}[/bold cyan] — {desc}"
                for cmd, desc in all_matches
            ]
            self._slash_hint.update("\n".join(lines))
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

        self._slash_hint.update("")

        chat = self.query_one("#chat", VerticalScroll)

        user_message = Static(f"你：{prompt}", classes="user-message")
        assistant_message = Markdown("", classes="assistant-message")

        await chat.mount(user_message)
        await chat.mount(assistant_message)

        skills = load_skills()
        skill_name, prompt_text = parse_slash_command(prompt, skills)

        # 传给 API 用干净的消息（去掉 /skill 前缀），UI 显示保留原始输入
        api_user_msg = ChatMessage(role="user", content=prompt_text or prompt)
        self._history.append(api_user_msg)

        # 首次对话时创建会话
        if self._session_id is None:
            session = create_session(self._serialize_history())
            self._session_id = session.session_id

        config = ChatConfig.from_yaml()

        self._generation_worker = self.generate_answer(
            config=config,
            messages=self._api_messages(),
            skill_name=skill_name,
            output=assistant_message,
        )

        chat.scroll_end(animate=False)

    @work(exclusive=True, group="ai-generation", exit_on_error=False)
    async def generate_answer(
        self,
        config: ChatConfig,
        messages: list[ChatMessage],
        skill_name: str | None,
        output: Markdown,
    ) -> None:
        prompt_input = self.query_one("#prompt", Input)
        status = self.query_one("#status", Static)
        chat = self.query_one("#chat", VerticalScroll)

        prompt_input.disabled = True
        status.update("正在思考……")

        api_messages: list[ChatMessage] = list(messages)
        if self._system_message:
            api_messages.insert(
                0, ChatMessage(role="system", content=self._system_message)
            )

        if skill_name:
            from pathlib import Path

            skill_path = Path("sparkos/agent/skills") / skill_name / "SKILL.md"
            if skill_path.is_file():
                content = skill_path.read_text(encoding="utf-8")
                api_messages.insert(
                    1,
                    ChatMessage(
                        role="system",
                        content=f"当前激活技能：{skill_name}\n\n{content}",
                    ),
                )

        markdown_stream = Markdown.get_stream(output)
        received_text = False
        full_text = ""
        tool_calls: list[ToolCall] = []

        try:
            async for item in stream_ai_response(
                config, api_messages, tools=TOOL_DEFINITIONS, execute_tool=execute_tool
            ):
                if isinstance(item, str):
                    if not received_text:
                        received_text = True
                        status.update("正在生成……")
                    full_text += item
                    await markdown_stream.write(item)
                elif isinstance(item, ToolCall):
                    tool_calls.append(item)
                    await self._show_tool_call(item, chat)
                    status.update(f"工具: {item.name}")

                distance_to_bottom = chat.max_scroll_y - chat.scroll_y
                if distance_to_bottom < 3:
                    chat.scroll_end(animate=False)

            assistant_msg = ChatMessage(
                role="assistant",
                content=full_text,
                tool_calls=[
                    {
                        "id": tc.call_id,
                        "type": "function",
                        "function": {"name": tc.name, "arguments": tc.arguments},
                    }
                    for tc in tool_calls
                ]
                or None,
            )
            self._history.append(assistant_msg)
            self._save_current_session()
            status.update("生成完成")

        except Exception as exc:
            self.log(f"[red]请求失败:[/red] {type(exc).__name__}: {exc}")
            import traceback
            traceback.print_exc()
            await markdown_stream.write(
                f"\n\n**请求失败：** `{type(exc).__name__}`: {exc}\n\n"
                "请检查网络、API Key 或模型配置。"
            )
            status.update("请求失败")
            raise

        finally:
            await markdown_stream.stop()
            prompt_input.disabled = False
            prompt_input.focus()

    def _api_messages(self) -> list[ChatMessage]:
        """构造发给 API 的消息列表，只包含 ChatMessage 实例。"""
        return [m for m in self._history[-12:] if isinstance(m, ChatMessage)]

    async def _show_tool_call(self, tc: ToolCall, chat: VerticalScroll) -> None:
        """在聊天区显示工具调用信息。"""
        detail = Static(
            f"[bold]参数:[/bold] {tc.arguments[:500]}\n"
            f"[bold]结果:[/bold] {tc.result[:500]}",
            classes="tool-detail",
        )
        await chat.mount(
            Collapsible(
                detail,
                title=f"调用工具 [bold cyan]{tc.name}[/bold cyan]",
                collapsed_symbol="",
                expanded_symbol="",
            )
        )

    def _serialize_history(self) -> list[dict[str, Any]]:
        """将 _history 序列化为可持久化的 dict 列表。"""
        messages: list[dict[str, Any]] = []
        for m in self._history:
            entry: dict[str, Any] = {"role": m.role, "content": m.content}
            if m.tool_calls:
                entry["tool_calls"] = m.tool_calls
            messages.append(entry)
        return messages

    def _save_current_session(self) -> None:
        """保存当前对话到历史文件。"""
        if self._session_id is None:
            return
        save_session(self._session_id, self._serialize_history())

    async def _show_skills_list(self) -> None:
        chat = self.query_one("#chat", VerticalScroll)
        skills = load_skills()

        lines = ["可用技能："]
        for s in skills:
            lines.append(f"  /{s.name} — {s.description}")
        if not skills:
            lines.append("  （暂无）")

        await chat.mount(Static("\n".join(lines), classes="assistant-message"))
        chat.scroll_end(animate=False)

    async def _handle_clear(self) -> None:
        """清除当前对话，新建会话。"""
        # 取消正在进行的生成，防止孤儿 worker 写入已清空的历史
        worker = self._generation_worker
        if worker is not None:
            getattr(worker, "cancel", lambda: None)()
            self._generation_worker = None

        self._save_current_session()
        self._session_id = None
        self._history = []
        chat = self.query_one("#chat", VerticalScroll)
        await chat.remove_children()
        self._slash_hint.update("")

    async def _show_history(self) -> None:
        """显示历史会话选择界面。"""
        self.push_screen(HistoryScreen())


if __name__ == "__main__":
    ChatApp().run()
