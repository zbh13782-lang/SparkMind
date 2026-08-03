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


@dataclass
class ToolCall:
    """工具调用信息。"""

    call_id: str
    name: str
    arguments: str
    result: str = ""


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
