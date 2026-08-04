"""LLM 客户端：流式文本 + 工具调用循环。"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator

from openai import AsyncOpenAI

from .models import ChatConfig, ChatMessage, ToolCall


class OpenAIChatClient:
    def __init__(self, config: ChatConfig) -> None:
        self.config = config
        self.client = AsyncOpenAI(base_url=config.base_url, api_key=config.api_key)

    async def chat_stream(
        self,
        messages: list[ChatMessage],
        tools: list[dict] | None = None,
        execute_tool: object = None,
    ) -> AsyncIterator[str | ToolCall]:
        """流式发送消息，支持工具调用循环。

        流程：
        1. 发送 messages + tools，流式接收文本
        2. 如果模型返回 tool_calls，执行工具
        3. 将工具结果发回模型，流式接收最终回复

        Yields:
            文本片段（str）或工具调用（ToolCall）
        """
        api_messages: list[dict] = []
        for m in messages:
            msg_dict: dict = {"role": m.role, "content": m.content}
            if m.tool_calls:
                msg_dict["tool_calls"] = m.tool_calls
            api_messages.append(msg_dict)

        kwargs: dict = {
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
                            tool_calls[
                                tc_delta.id
                            ].arguments += tc_delta.function.arguments

        # 执行工具调用
        for tc in tool_calls.values():
            if execute_tool is not None:
                tc.result = execute_tool(tc.name, json.loads(tc.arguments or "{}"))
            yield tc

        # 循环处理多轮工具调用
        if tool_calls and execute_tool is not None:
            async for item in self._follow_up_loop(
                messages=api_messages,
                full_text=full_text,
                tool_calls=list(tool_calls.values()),
                tools=tools,
                execute_tool=execute_tool,
            ):
                yield item

    async def _follow_up_loop(
        self,
        messages: list[dict],
        full_text: str,
        tool_calls: list[ToolCall],
        tools: list[dict] | None,
        execute_tool: object = None,
    ) -> AsyncIterator[str | ToolCall]:
        """将工具结果发送给模型，持续循环直到模型不再调用工具。"""
        while True:
            follow_messages = list(messages)

            if full_text:
                follow_messages.append({"role": "assistant", "content": full_text})

            for tc in tool_calls:
                follow_messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": tc.call_id,
                        "content": json.dumps({"result": tc.result}),
                    }
                )

            kwargs: dict = {
                "model": self.config.model,
                "messages": follow_messages,
                "stream": True,
            }
            if tools:
                kwargs["tools"] = tools

            response = await self.client.chat.completions.create(**kwargs)

            full_text = ""
            new_tool_calls: dict[str, ToolCall] = {}

            async for chunk in response:
                if not chunk.choices:
                    continue
                delta = chunk.choices[0].delta

                if delta.content:
                    full_text += delta.content
                    yield delta.content

                if delta.tool_calls:
                    for tc_delta in delta.tool_calls:
                        if tc_delta.id:
                            new_tool_calls[tc_delta.id] = ToolCall(
                                call_id=tc_delta.id,
                                name="",
                                arguments="",
                            )
                        if tc_delta.function:
                            if tc_delta.function.name:
                                new_tool_calls[
                                    tc_delta.id
                                ].name = tc_delta.function.name
                            if tc_delta.function.arguments:
                                new_tool_calls[
                                    tc_delta.id
                                ].arguments += tc_delta.function.arguments

            # 本轮没有新工具调用，结束循环
            if not new_tool_calls:
                break

            # 执行新工具调用并 yield
            for tc in new_tool_calls.values():
                if execute_tool is not None:
                    tc.result = execute_tool(tc.name, json.loads(tc.arguments or "{}"))
                yield tc

            tool_calls = list(new_tool_calls.values())
            messages = follow_messages


async def stream_ai_response(
    config: ChatConfig,
    messages: list[ChatMessage],
    tools: list[dict] | None = None,
    execute_tool: object = None,
) -> AsyncIterator[str | ToolCall]:
    """统一流式接口，封装底层 AI SDK + 工具调用循环。"""
    client = OpenAIChatClient(config)
    async for item in client.chat_stream(
        messages=messages,
        tools=tools,
        execute_tool=execute_tool,
    ):
        yield item
