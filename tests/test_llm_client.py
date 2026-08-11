from __future__ import annotations

import unittest
from types import SimpleNamespace

from config.config import ChatConfig
from sparkos.infrastructure.llm.client import OpenAIChatClient
from sparkos.infrastructure.llm.models import ChatMessage, ToolCall


class AsyncChunks:
    def __init__(self, chunks: list[object]) -> None:
        self._chunks = iter(chunks)

    def __aiter__(self) -> AsyncChunks:
        return self

    async def __anext__(self) -> object:
        try:
            return next(self._chunks)
        except StopIteration as exc:
            raise StopAsyncIteration from exc


class FakeCompletions:
    def __init__(self, chunks: list[object]) -> None:
        self.chunks = chunks
        self.kwargs: dict | None = None

    async def create(self, **kwargs: object) -> AsyncChunks:
        self.kwargs = kwargs
        return AsyncChunks(self.chunks)


def tool_chunk(
    *,
    index: int,
    call_id: str | None,
    name: str | None,
    arguments: str | None,
) -> object:
    function = SimpleNamespace(name=name, arguments=arguments)
    tool_delta = SimpleNamespace(index=index, id=call_id, function=function)
    delta = SimpleNamespace(content=None, tool_calls=[tool_delta])
    return SimpleNamespace(choices=[SimpleNamespace(delta=delta)])


class OpenAIChatClientTests(unittest.IsolatedAsyncioTestCase):
    async def test_tool_arguments_accumulate_by_index_when_delta_id_is_missing(
        self,
    ) -> None:
        completions = FakeCompletions(
            [
                tool_chunk(
                    index=0,
                    call_id="call-1",
                    name="read_file",
                    arguments='{"path"',
                ),
                tool_chunk(
                    index=0,
                    call_id=None,
                    name=None,
                    arguments=':"x"}',
                ),
            ]
        )
        client = OpenAIChatClient(ChatConfig(base_url="http://localhost/v1", api_key="test", model="test"))
        client.client = SimpleNamespace(  # type: ignore[assignment]
            chat=SimpleNamespace(completions=completions)
        )

        items = [item async for item in client.chat_stream([ChatMessage(role="user", content="read")])]

        self.assertEqual(len(items), 1)
        self.assertIsInstance(items[0], ToolCall)
        self.assertEqual(items[0].call_id, "call-1")
        self.assertEqual(items[0].arguments, '{"path":"x"}')


if __name__ == "__main__":
    unittest.main()
