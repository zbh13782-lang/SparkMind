"""Visible bootstrap progress for the first Textual frame."""

from __future__ import annotations

from typing import ClassVar

from textual.app import ComposeResult
from textual.containers import Vertical
from textual.widgets import LoadingIndicator, Static


class StartupPanel(Vertical):
    """Show the current real startup stage while services are checked."""

    _STAGE_TITLES: ClassVar[dict[str, str]] = {
        "llm": "连接模型",
        "docker": "检查 Docker",
        "spark-hive": "检查数据目录",
        "advisor": "检查 Advisor",
        "runtime": "加载运行时",
    }

    def compose(self) -> ComposeResult:
        yield LoadingIndicator(id="startup-spinner")
        yield Static("正在启动 SparkMind", id="startup-title")
        yield Static("准备检查服务…", id="startup-detail", markup=False)

    def set_stage(self, stage: str, detail: str) -> None:
        title = self._STAGE_TITLES.get(stage, "正在启动")
        self.query_one("#startup-title", Static).update(f"{title} · 启动中")
        self.query_one("#startup-detail", Static).update(detail)

    def succeed(self, detail: str) -> None:
        self.query_one("#startup-title", Static).update("启动完成")
        self.query_one("#startup-detail", Static).update(detail)

    def fail(self, detail: str) -> None:
        self.query_one("#startup-title", Static).update("启动失败")
        self.query_one("#startup-detail", Static).update(detail)


__all__ = ["StartupPanel"]
