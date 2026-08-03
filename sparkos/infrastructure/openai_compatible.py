"""OpenAI API 封装：流式文本 + 工具调用循环。"""

from __future__ import annotations

import json
import os
from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Any

from openai import AsyncOpenAI


@dataclass
class ChatMessage:
    role: str
    content: str


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

    async def chat_stream(
        self,
        messages: list[ChatMessage],
        tools: list[dict[str, Any]] | None = None,
        execute_tool: Any = None,
    ) -> AsyncIterator[str | ToolCall]:
        """流式发送消息，支持工具调用循环。

        流程：
        1. 发送 messages + tools，流式接收文本
        2. 如果模型返回 tool_calls，执行工具
        3. 将工具结果发回模型，流式接收最终回复

        Yields:
            文本片段（str）或工具调用（ToolCall）
        """
        api_messages = [{"role": m.role, "content": m.content} for m in messages]
        kwargs: dict[str, Any] = {
            "model": self.config.model,
            "messages": api_messages,
            "stream": True,
        }
        if tools:
            kwargs["tools"] = tools
            kwargs["tool_choice"] = "auto"

        response = await self.client.chat.completions.create(**kwargs)

        full_text = ""
        tool_calls: dict[str, ToolCall] = {}

        async for chunk in response:
            if not chunk.choices:
                continue
            choice = chunk.choices[0]
            delta = choice.delta

            # 流式文本
            if delta.content:
                full_text += delta.content
                yield delta.content

            # 流式工具调用参数
            if delta.tool_calls:
                for tc_delta in delta.tool_calls:
                    if tc_delta.id:
                        tool_calls[tc_delta.id] = ToolCall(
                            call_id=tc_delta.id,
                            name="",
                            arguments="",
                        )
                    if tc_delta.function:
                        if tc_delta.function.name:
                            tool_calls[tc_delta.id].name = tc_delta.function.name
                        if tc_delta.function.arguments:
                            tool_calls[tc_delta.id].arguments += tc_delta.function.arguments

        # 执行工具调用
        for tc in tool_calls.values():
            if execute_tool is not None:
                tc.result = execute_tool(tc.name, json.loads(tc.arguments or "{}"))
            yield tc

        # 如果有工具调用，将结果发给模型获取最终回复
        if tool_calls and execute_tool is not None:
            async for text in self._follow_up(
                messages=api_messages,
                full_text=full_text,
                tool_calls=list(tool_calls.values()),
                tools=tools,
            ):
                yield text

    async def _follow_up(
        self,
        messages: list[dict[str, Any]],
        full_text: str,
        tool_calls: list[ToolCall],
        tools: list[dict[str, Any]] | None,
    ) -> AsyncIterator[str]:
        """将工具结果发送给模型，获取最终回复。"""
        follow_messages = list(messages)

        if full_text:
            follow_messages.append({"role": "assistant", "content": full_text})

        for tc in tool_calls:
            follow_messages.append({
                "role": "tool",
                "tool_call_id": tc.call_id,
                "content": json.dumps({"result": tc.result}),
            })

        kwargs: dict[str, Any] = {
            "model": self.config.model,
            "messages": follow_messages,
            "stream": True,
        }
        if tools:
            kwargs["tools"] = tools

        response = await self.client.chat.completions.create(**kwargs)

        async for chunk in response:
            if not chunk.choices:
                continue
            delta = chunk.choices[0].delta
            if delta.content:
                yield delta.content
