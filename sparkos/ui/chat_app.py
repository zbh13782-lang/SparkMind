"""SparkMind CLI — Textual 聊天界面。"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import ClassVar

from textual import work
from textual.app import App, ComposeResult
from textual.containers import VerticalScroll
from textual.widgets import Footer, Header, Input, Markdown, Static

from sparkos.infrastructure.openai_compatible import ChatConfig, ChatMessage, OpenAIChatClient


async def stream_ai_response(
    config: ChatConfig,
    messages: list[ChatMessage],
) -> AsyncIterator[str]:
    """统一流式接口，封装底层 AI SDK。"""
    client = OpenAIChatClient(config)
    async for chunk in client.chat_stream(messages):
        yield chunk


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

    def compose(self) -> ComposeResult:
        yield Header()

        with VerticalScroll(id="chat"):
            pass

        yield Static("就绪", id="status")
        yield Input(
            placeholder="今天想聊点什么……",
            id="prompt",
        )
        yield Footer()

    async def on_mount(self) -> None:
        self.query_one("#prompt", Input).focus()

    async def on_input_submitted(self, event: Input.Submitted) -> None:
        prompt = event.value.strip()
        if not prompt:
            return

        event.input.value = ""

        chat = self.query_one("#chat", VerticalScroll)

        user_message = Static(f"你：{prompt}", classes="user-message")
        assistant_message = Markdown("", classes="assistant-message")

        await chat.mount(user_message)
        await chat.mount(assistant_message)

        self._history.append(ChatMessage(role="user", content=prompt))

        config = ChatConfig.from_yaml()

        self._generation_worker = self.generate_answer(
            config=config,
            messages=list(self._history),
            output=assistant_message,
        )

        chat.scroll_end(animate=False)

    @work(exclusive=True, group="ai-generation", exit_on_error=False)
    async def generate_answer(
        self,
        config: ChatConfig,
        messages: list[ChatMessage],
        output: Markdown,
    ) -> None:
        prompt_input = self.query_one("#prompt", Input)
        status = self.query_one("#status", Static)
        chat = self.query_one("#chat", VerticalScroll)

        prompt_input.disabled = True
        status.update("正在思考……")

        markdown_stream = Markdown.get_stream(output)
        received_text = False
        full_text = ""

        try:
            async for delta in stream_ai_response(config, messages):
                if not delta:
                    continue

                if not received_text:
                    received_text = True
                    status.update("正在生成……")

                full_text += delta
                await markdown_stream.write(delta)

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

    def action_cancel_generation(self) -> None:
        worker = self._generation_worker
        if worker is not None and not getattr(worker, "is_finished", True):
            getattr(worker, "cancel", lambda: None)()
            self.query_one("#status", Static).update("已停止")


if __name__ == "__main__":
    ChatApp().run()
