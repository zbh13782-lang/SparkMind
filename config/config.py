"""统一配置入口：从 config/config.yaml 读取，暴露各层配置。"""

from __future__ import annotations

import os
from dataclasses import dataclass

import yaml


@dataclass(frozen=True)
class ChatConfig:
    base_url: str
    api_key: str
    model: str


@dataclass(frozen=True)
class RuntimeConfig:
    max_tool_rounds: int
    max_replans: int
    max_steps: int
    context_window: int


def load(path: str | None = None) -> dict:
    """读取 YAML 文件，返回原始字典。"""
    path = path or os.path.join(os.path.dirname(__file__), "config.yaml")
    if os.path.exists(path):
        with open(path, encoding="utf-8") as f:
            return yaml.safe_load(f) or {}
    return {}


def get_chat_config() -> ChatConfig:
    """读取 LLM 服务配置。"""
    cfg = load()
    api = cfg.get("api", {})
    return ChatConfig(
        base_url=api["base_url"],
        api_key=api["api_key"],
        model=api["model"],
    )


def get_runtime_config() -> RuntimeConfig:
    """读取 Agent 运行时参数。"""
    cfg = load()
    rt = cfg.get("runtime", {})
    return RuntimeConfig(
        max_tool_rounds=rt["max_tool_rounds"],
        max_replans=rt["max_replans"],
        max_steps=rt["max_steps"],
        context_window=rt["context_window"],
    )
