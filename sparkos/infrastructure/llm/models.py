"""LLM 数据模型：消息、工具调用、配置。"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any

import yaml


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


@dataclass
class ChatConfig:
    base_url: str = "http://localhost:11434/v1"
    api_key: str = "ollama"
    model: str = "llama3"

    @classmethod
    def from_yaml(cls, path: str = "config/config.yaml") -> ChatConfig:
        """从 config/config.yaml 读取配置，缺失项使用默认值。"""
        cfg: dict = {}
        if os.path.exists(path):
            with open(path) as f:
                cfg = yaml.safe_load(f) or {}

        api = cfg.get("api", {})
        return cls(
            base_url=api.get("base_url", cls.base_url),
            api_key=api.get("api_key", cls.api_key),
            model=api.get("model", cls.model),
        )
