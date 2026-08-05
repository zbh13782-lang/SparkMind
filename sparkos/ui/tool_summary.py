"""Nested disclosure widget for one task's completed tool calls."""

from __future__ import annotations

from rich.text import Text
from textual.containers import Vertical
from textual.widgets import Collapsible, Static

from sparkos.infrastructure.llm.models import ToolCall


class ToolCallSummary(Collapsible):
    """Group tool calls behind one task-level disclosure."""

    def __init__(self) -> None:
        self._tool_list = Vertical(classes="tool-list")
        self._count = 0
        super().__init__(
            self._tool_list,
            title="正在执行工具（0）",
            collapsed=True,
            collapsed_symbol="",
            expanded_symbol="",
            classes="tool-summary",
        )

    async def add_tool(self, tool_call: ToolCall) -> None:
        self._count += 1
        detail = Text("参数:", style="bold")
        detail.append(f" {tool_call.arguments[:500]}\n")
        detail.append("结果:", style="bold")
        detail.append(f" {tool_call.result[:500]}")
        await self._tool_list.mount(
            Collapsible(
                Static(detail, classes="tool-detail", markup=False),
                title=f"{self._count}. {tool_call.name}",
                collapsed=True,
                collapsed_symbol="",
                expanded_symbol="",
                classes="tool-call",
            )
        )
        self.title = f"正在执行工具（{self._count}）"

    def complete(self) -> None:
        self.title = f"执行了 {self._count} 个工具"


__all__ = ["ToolCallSummary"]
