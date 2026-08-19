from __future__ import annotations

import unittest
from unittest.mock import patch

from sparkos.agent.context import WINDOW, AgentContext
from sparkos.agent.memory import Session
from sparkos.infrastructure.llm.models import ChatMessage, ToolCall


class AgentContextTests(unittest.TestCase):
    def test_build_messages_never_silently_drops_uncompacted_history(self) -> None:
        context = AgentContext()
        for index in range(WINDOW + 1):
            context.record_user(str(index))

        messages = context.build_messages(skills=[], tools=[])

        self.assertEqual(
            [message.content for message in messages if message.role == "user"],
            [str(index) for index in range(WINDOW + 1)],
        )

    def test_tool_result_is_recorded_as_tool_message(self) -> None:
        context = AgentContext()

        context.record_tool("call-1", "result")

        self.assertEqual(context.history[-1].role, "tool")
        self.assertEqual(context.history[-1].tool_call_id, "call-1")
        self.assertEqual(context.history[-1].content, "result")

    def test_build_messages_includes_compact_catalog_summary(self) -> None:
        context = AgentContext()

        messages = context.build_messages(
            skills=[],
            tools=[],
            catalog_summary={
                "default_database": "sparkmind_demo",
                "tables": ["fact_order", "fact_event"],
            },
        )

        system_text = "\n".join(message.content for message in messages if message.role == "system")
        self.assertIn("sparkmind_demo", system_text)
        self.assertIn("fact_order", system_text)

    def test_build_messages_bounds_large_tool_history(self) -> None:
        context = AgentContext()
        context.record_user("分析数据")
        context.record_tool("call-1", "x" * 20_000)

        messages = context.build_messages(
            skills=[],
            tools=[],
            max_history_chars=1_000,
        )

        history_text = "\n".join(
            message.content for message in messages if message.role != "system"
        )
        self.assertLessEqual(len(history_text), 1_000)
        self.assertIn("已截断", history_text)

    def test_assistant_tool_call_is_preserved_during_serialization(self) -> None:
        context = AgentContext()
        call = ToolCall(call_id="call-1", name="read_file", arguments="{}")
        context.record_assistant("", [call.to_api_dict()])
        context.record_tool(call.call_id, "ok")

        self.assertEqual(
            context.serialize_history(),
            [
                {
                    "role": "assistant",
                    "content": "",
                    "tool_calls": [call.to_api_dict()],
                },
                {"role": "tool", "content": "ok", "tool_call_id": "call-1"},
            ],
        )

    def test_load_session_restores_tool_call_id(self) -> None:
        session = Session(
            session_id="session-1",
            messages=[{"role": "tool", "content": "ok", "tool_call_id": "call-1"}],
            created_at="2026-08-04T00:00:00",
        )
        context = AgentContext()

        with patch("sparkos.agent.context.memory.load_session", return_value=session):
            loaded = context.load_session("session-1")

        self.assertTrue(loaded)
        self.assertEqual(context.history[0].tool_call_id, "call-1")

    def test_compaction_payload_contains_only_overflow(self) -> None:
        context = AgentContext()
        for index in range(WINDOW + 2):
            context.record_user(str(index))

        overflow = context.messages_to_compact()

        self.assertEqual([message.content for message in overflow], ["0", "1"])
        context.apply_summary("old messages", len(overflow))
        self.assertEqual(context.summary, "old messages")
        self.assertEqual(context.summary_upto, 2)

    def test_compaction_never_splits_assistant_tool_exchange(self) -> None:
        context = AgentContext()
        call = ToolCall(call_id="call-1", name="read_file", arguments="{}")
        context.record_user("old request")
        context.record_assistant("", [call.to_api_dict()])
        context.record_tool(call.call_id, "result")
        context.record_assistant("old answer")
        for index in range(WINDOW - 2):
            context.record_user(f"new-{index}")

        overflow = context.messages_to_compact()
        remaining = context.history[len(overflow) :]

        self.assertEqual(
            [message.role for message in overflow],
            ["user", "assistant", "tool", "assistant"],
        )
        self.assertEqual(remaining[0].role, "user")

    def test_from_serialized_message_accepts_existing_chat_message(self) -> None:
        message = ChatMessage(role="user", content="hello")

        self.assertIs(AgentContext.deserialize_message(message), message)


if __name__ == "__main__":
    unittest.main()
