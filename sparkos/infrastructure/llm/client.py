"""OpenAI-compatible LLM transport for one model turn at a time."""

from __future__ import annotations

from collections.abc import AsyncIterator

from openai import AsyncOpenAI

from config.config import ChatConfig

from .models import ChatMessage, ToolCall


class OpenAIChatClient:
    def __init__(self, config: ChatConfig) -> None:
        self.config = config
        self.client = AsyncOpenAI(base_url=config.base_url, api_key=config.api_key)

    async def chat_once(
        self,
        messages: list[dict],
        *,
        json_object: bool = False,
    ) -> str:
        """单次非流式调用，返回完整文本。供记忆压缩等场景使用。"""
        kwargs: dict = {
            "model": self.config.model,
            "messages": messages,
            "stream": False,
        }
        if json_object:
            kwargs["response_format"] = {"type": "json_object"}
        response = await self.client.chat.completions.create(**kwargs)
        return response.choices[0].message.content or ""

    async def chat_stream(
        self,
        messages: list[ChatMessage],
        tools: list[dict] | None = None,
    ) -> AsyncIterator[str | ToolCall]:
        """Stream one assistant turn and return completed tool calls afterward."""
        api_messages = [message.to_api_dict() for message in messages]

        kwargs: dict = {
            "model": self.config.model,
            "messages": api_messages,
            "stream": True,
        }
        if tools:
            kwargs["tools"] = tools
            kwargs["tool_choice"] = "auto"

        response = await self.client.chat.completions.create(**kwargs)

        tool_calls: dict[int, ToolCall] = {}

        async for chunk in response:
            if not chunk.choices:
                continue
            choice = chunk.choices[0]
            delta = choice.delta

            # 流式文本
            if delta.content:
                yield delta.content

            # 流式工具调用参数
            if delta.tool_calls:
                for tc_delta in delta.tool_calls:
                    index = tc_delta.index if tc_delta.index is not None else 0
                    tool_call = tool_calls.setdefault(
                        index,
                        ToolCall(call_id="", name="", arguments=""),
                    )
                    if tc_delta.id:
                        tool_call.call_id = tc_delta.id
                    if tc_delta.function:
                        if tc_delta.function.name:
                            tool_call.name = tc_delta.function.name
                        if tc_delta.function.arguments:
                            tool_call.arguments += tc_delta.function.arguments

        for index in sorted(tool_calls):
            yield tool_calls[index]


async def stream_ai_response(
    config: ChatConfig,
    messages: list[ChatMessage],
    tools: list[dict] | None = None,
) -> AsyncIterator[str | ToolCall]:
    """Compatibility wrapper for one model turn."""
    client = OpenAIChatClient(config)
    async for item in client.chat_stream(
        messages=messages,
        tools=tools,
    ):
        yield item
