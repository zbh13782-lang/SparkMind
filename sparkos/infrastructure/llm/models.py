"""LLM 传输层数据模型：消息、工具调用。"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass
class ChatMessage:
    role: str
    content: str
    tool_calls: list[dict[str, Any]] | None = None
    tool_call_id: str | None = None

    def to_api_dict(self) -> dict[str, Any]:
        """Serialize the message without emitting unsupported null fields."""
        result: dict[str, Any] = {"role": self.role, "content": self.content}
        if self.tool_calls is not None:
            result["tool_calls"] = self.tool_calls
        if self.tool_call_id is not None:
            result["tool_call_id"] = self.tool_call_id
        return result


@dataclass
class ToolCall:
    """工具调用信息。"""

    call_id: str
    name: str
    arguments: str
    result: str = ""

    def to_api_dict(self) -> dict[str, Any]:
        return {
            "id": self.call_id,
            "type": "function",
            "function": {"name": self.name, "arguments": self.arguments},
        }
