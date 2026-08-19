"""Empty state for the SparkMind conversation pane."""

from __future__ import annotations

from rich.text import Text
from textual.widgets import Static


class WelcomePanel(Static):
    """Explain the first action without competing with the input box."""

    def __init__(self, **kwargs: object) -> None:
        super().__init__(self._content(), markup=False, **kwargs)

    @staticmethod
    def _content() -> Text:
        text = Text()
        text.append("╭─✦────────────────╮\n", style="dim cyan")
        text.append("│   ", style="dim cyan")
        text.append("SPARKMIND", style="bold cyan")
        text.append("      │\n", style="dim cyan")
        text.append("│   DATA / INSIGHT │\n", style="dim cyan")
        text.append("╰──────────────────╯\n\n", style="dim cyan")
        text.append("数据助手已准备好\n\n", style="bold")
        text.append("输入一个问题，或使用快捷命令开始：\n", style="dim")
        text.append("/skills", style="bold cyan")
        text.append("  查看技能    ", style="dim")
        text.append("/history", style="bold cyan")
        text.append("  打开历史会话\n", style="dim")
        text.append("/select", style="bold cyan")
        text.append("  选择文件    ", style="dim")
        text.append("Esc", style="bold magenta")
        text.append("  停止生成", style="dim")
        return text


__all__ = ["WelcomePanel"]
