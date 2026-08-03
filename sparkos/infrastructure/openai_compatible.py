"""OpenAI Responses API 流式接口封装。"""

from __future__ import annotations

import os
from collections.abc import AsyncIterator
from dataclasses import dataclass

from openai import AsyncOpenAI


@dataclass
class ChatMessage:
    role: str
    content: str


@dataclass
class ChatConfig:
    base_url: str = "http://localhost:11434/v1"
    api_key: str = "ollama"
    model: str = "llama3"

    @classmethod
    def from_yaml(cls, path: str = "config/config.yaml") -> ChatConfig:
        """从 config/config.yaml 读取配置，缺失项使用默认值。"""
        import yaml

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


class OpenAIChatClient:
    def __init__(self, config: ChatConfig) -> None:
        self.config = config
        self.client = AsyncOpenAI(base_url=config.base_url, api_key=config.api_key)

    async def chat_stream(self, messages: list[ChatMessage]) -> AsyncIterator[str]:
        """通过 Responses API 流式发送消息，逐块 yield 文本增量。"""
        response = await self.client.responses.create(
            model=self.config.model,
            input=[{"role": m.role, "content": m.content} for m in messages],
            stream=True,
        )
        async for event in response:
            if event.type == "response.output_text.delta":
                yield event.delta
