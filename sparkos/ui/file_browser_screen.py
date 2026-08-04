"""文件选择界面。"""

from __future__ import annotations

import os
from typing import ClassVar

from textual.app import ComposeResult
from textual.containers import VerticalScroll
from textual.screen import Screen
from textual.widgets import Button, Footer, Header, Static


class FileBrowserScreen(Screen):
    """文件浏览器界面。"""

    BINDINGS: ClassVar = [
        ("escape", "dismiss", "返回"),
    ]

    CSS = """
    #file-list Button {
        width: 1fr;
        padding: 0;
    }
    """

    def __init__(self) -> None:
        super().__init__()
        root = os.environ.get("SPARKOS_REPO_ROOT", os.getcwd())
        self._current_dir: str = os.path.abspath(root)

    def compose(self) -> ComposeResult:
        yield Header()
        yield Static("", id="file-browser-path")
        with VerticalScroll(id="file-list"):
            pass
        yield Footer()

    async def on_mount(self) -> None:
        await self._populate()

    async def _populate(self) -> None:
        self.query_one("#file-browser-path", Static).update(
            f"目录：{self._current_dir}"
        )
        container = self.query_one("#file-list", VerticalScroll)
        await container.remove_children()

        try:
            entries = sorted(os.listdir(self._current_dir))
        except PermissionError:
            container.mount(Static("无权访问此目录"))
            return

        root = os.environ.get("SPARKOS_REPO_ROOT", os.getcwd())
        is_root = self._current_dir == os.path.abspath(root)

        if not is_root:
            up = os.path.dirname(self._current_dir)
            btn = Button("[..]", id="btn-parent")
            btn._full_path = up  # type: ignore[attr-defined]
            container.mount(btn)

        dirs: list[str] = []
        files: list[str] = []
        for name in entries:
            if name.startswith("."):
                continue
            full = os.path.join(self._current_dir, name)
            (dirs if os.path.isdir(full) else files).append(name)

        def _safe_id(prefix: str, name: str) -> str:
            safe = "".join(c if c.isalnum() else "-" for c in name).strip("-")
            return f"{prefix}-{safe}"

        for d in dirs:
            full = os.path.join(self._current_dir, d)
            btn = Button(f"[DIR] {d}/", id=_safe_id("btn-dir", d))
            btn._full_path = full  # type: ignore[attr-defined]
            container.mount(btn)

        for f in files:
            full = os.path.join(self._current_dir, f)
            btn = Button(f"[FILE] {f}", id=_safe_id("btn-file", f))
            btn._full_path = full  # type: ignore[attr-defined]
            container.mount(btn)

    async def on_button_pressed(self, event: Button.Pressed) -> None:
        btn = event.button
        path: str = btn._full_path  # type: ignore[attr-defined]
        if os.path.isdir(path):
            self._current_dir = path
            await self._populate()
        else:
            self.dismiss(path)
