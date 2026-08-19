"""历史会话选择界面。"""

from __future__ import annotations

from typing import ClassVar

from textual.app import ComposeResult
from textual.containers import VerticalScroll
from textual.screen import Screen
from textual.widgets import Button, Footer, Header, Markdown, Static

from sparkos.agent.memory import list_sessions
from sparkos.ui.runtime_panel import RuntimePanel
from sparkos.ui.welcome_panel import WelcomePanel


class HistoryScreen(Screen):
    """历史会话选择界面。"""

    BINDINGS: ClassVar = [
        ("escape", "dismiss", "返回"),
    ]

    def compose(self) -> ComposeResult:
        yield Header()
        yield Static(
            "选择历史会话（上下键选择，Enter 确认，Esc 返回）",
            id="history-title",
        )
        with VerticalScroll(id="history-list"):
            pass
        yield Footer()

    async def on_mount(self) -> None:
        await self._render_sessions()

    async def _render_sessions(self, expanded: bool = False) -> None:
        container = self.query_one("#history-list", VerticalScroll)
        await container.remove_children()

        sessions = list_sessions()
        display = sessions if expanded else sessions[:10]

        for s in display:
            first_msg = s.messages[0].get("content", "")[:50] if s.messages else "(空会话)"
            time_str = s.created_at[:19].replace("T", " ")
            label = f"{first_msg}\n{time_str} · {len(s.messages)} 条消息"

            btn = Button(label, id=f"session-{s.session_id}")
            btn.session_id = s.session_id
            container.mount(btn)

        if not expanded and len(sessions) > 10:
            btn = Button("展开全部", id="expand-all")
            container.mount(btn)

    async def on_button_pressed(self, event: Button.Pressed) -> None:
        btn = event.button
        if btn.id == "expand-all":
            await self._render_sessions(expanded=True)
            return

        session_id = getattr(btn, "session_id", None)
        if session_id:
            await self._load_session(session_id)

    async def _load_session(self, session_id: str) -> None:
        app = self.app
        if app.runtime.context.load_session(session_id):
            chat = app.query_one("#chat", VerticalScroll)
            await chat.remove_children([child for child in chat.children if child.id != "welcome"])
            app.query_one("#runtime-panel", RuntimePanel).reset()
            app.query_one("#status", Static).update("已加载历史会话")
            app._slash_hint.update("")
            welcome = chat.query("#welcome").first(WelcomePanel)
            has_messages = False
            for msg in app.runtime.context.history:
                if msg.role == "user":
                    widget = Static(
                        f"你：{msg.content}",
                        classes="user-message",
                        markup=False,
                    )
                elif msg.role == "assistant" and msg.content:
                    widget = Markdown(msg.content, classes="assistant-message")
                else:
                    continue
                has_messages = True
                await chat.mount(widget)
            welcome.display = not has_messages
            chat.scroll_end(animate=False)
        self.dismiss()
