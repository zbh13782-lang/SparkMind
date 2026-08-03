"""SparkMind CLI — Textual 聊天界面。"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import ClassVar

from textual import work
from textual.app import App, ComposeResult
from textual.containers import VerticalScroll
from textual.widgets import Footer, Header, Input, Markdown, Static

from sparkos.agent.skills.loader import (
    build_system_message,
    load_skills,
    parse_slash_command,
)
from sparkos.agent.tools.registry import TOOL_DEFINITIONS, execute_tool
from sparkos.infrastructure.openai_compatible import (
    ChatConfig,
    ChatMessage,
    OpenAIChatClient,
    ToolCall,
)


async def stream_ai_response(
    config: ChatConfig,
    messages: list[ChatMessage],
) -> AsyncIterator[str | ToolCall]:
    """统一流式接口，封装底层 AI SDK + 工具调用循环。"""
    client = OpenAIChatClient(config)
    async for item in client.chat_stream(
        messages=messages,
        tools=TOOL_DEFINITIONS,
        execute_tool=execute_tool,
    ):
        yield item


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

    def compose(self) -> ComposeResult:
        yield Header()

        with VerticalScroll(id="chat"):
            yield Static("", id="slash-hint", classes="slash-hint")

        yield Static("就绪", id="status")
        yield Input(
            placeholder="今天想聊点什么……",
            id="prompt",
        )
        yield Footer()

    async def on_mount(self) -> None:
        self.query_one("#prompt", Input).focus()

    def on_input_changed(self, event: Input.Changed) -> None:
        input_widget = event.input
        value = input_widget.value

        if not value.startswith("/"):
            self.query_one("#slash-hint", Static).update("")
            return

        skills = load_skills()
        prefix = value[1:].casefold()
        matches = [s for s in skills if s.name.casefold().startswith(prefix)]

        if matches:
            lines = [f"[bold cyan]/{s.name}[/bold cyan] — {s.description}" for s in matches]
            self.query_one("#slash-hint", Static).update("\n".join(lines))
        else:
            self.query_one("#slash-hint", Static).update("")

    async def on_input_submitted(self, event: Input.Submitted) -> None:
        prompt = event.value.strip()
        if not prompt:
            return

        event.input.value = ""

        if prompt == "/skills":
            await self._show_skills_list()
            self.query_one("#slash-hint", Static).update("")
            return

        self.query_one("#slash-hint", Static).update("")

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

        config = ChatConfig.from_yaml()

        self._generation_worker = self.generate_answer(
            config=config,
            messages=self._history[-12:],
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
            api_messages.insert(0, ChatMessage(role="system", content=self._system_message))

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

        try:
            async for item in stream_ai_response(config, api_messages):
                if isinstance(item, str):
                    if not received_text:
                        received_text = True
                        status.update("正在生成……")
                    full_text += item
                    await markdown_stream.write(item)
                elif isinstance(item, ToolCall):
                    await self._show_tool_call(item, chat)
                    status.update(f"工具: {item.name}")

                distance_to_bottom = chat.max_scroll_y - chat.scroll_y
                if distance_to_bottom < 3:
                    chat.scroll_end(animate=False)

            self._history.append(ChatMessage(role="assistant", content=full_text))
            status.update("生成完成")

        except Exception as exc:
            await markdown_stream.write(
                f"\n\n**请求失败：** `{type(exc).__name__}`\n\n"
                "请检查网络、API Key 或模型配置。"
            )
            status.update("请求失败")
            raise

        finally:
            await markdown_stream.stop()
            prompt_input.disabled = False
            prompt_input.focus()

    async def _show_tool_call(self, tc: ToolCall, chat: VerticalScroll) -> None:
        """在聊天区显示工具调用信息。"""
        display = Static(
            f"[bold yellow]调用工具:[/bold yellow] {tc.name}\n"
            f"参数: {tc.arguments[:200]}\n"
            f"结果: {tc.result[:300]}",
            classes="assistant-message",
        )
        await chat.mount(display)

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

    def action_cancel_generation(self) -> None:
        worker = self._generation_worker
        if worker is not None and not getattr(worker, "is_finished", True):
            getattr(worker, "cancel", lambda: None)()
            self.query_one("#status", Static).update("已停止")


if __name__ == "__main__":
    ChatApp().run()
