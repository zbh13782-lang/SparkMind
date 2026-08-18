"""Agent 的上下文组装"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from config.config import get_runtime_config
from sparkos.agent import memory
from sparkos.agent.skills.loader import Skill, build_system_message, load_skill_content
from sparkos.infrastructure.llm.models import ChatMessage

_SYSTEM_PROMPT_PATH = Path(__file__).resolve().parent / "system_prompt.md"

# 最多可见短期窗口数，默认值来自 config.yaml runtime.context_window
try:
    WINDOW = get_runtime_config().context_window
except Exception:
    WINDOW = 12

_COMPACT_PROMPT = (
    "你是一个对话记忆压缩器。下面是某段对话的早期内容，以及（可能存在的）"
    "之前已压缩的摘要。请把它们合并成一段简洁、信息完整的滚动摘要，保留："
    "用户的目标、关键事实、已完成的操作、重要的结论或数据、未解决的问题。"
    "只输出摘要文本本身，不要额外解释。"
)


def _load_system_prompt() -> str:
    if not _SYSTEM_PROMPT_PATH.is_file():
        return ""
    return _SYSTEM_PROMPT_PATH.read_text(encoding="utf-8").strip()


class AgentContext:
    """Mutable state for one conversation session.

    This object contains no model client and does not execute tools. Runtime
    services decide when to compact it and how to run a task against it.
    """

    def __init__(self) -> None:
        self.history: list[ChatMessage] = []
        self.summary = ""
        self.summary_upto = 0
        self.session_id: str | None = None

    def build_messages(
        self,
        skills: list[Skill],
        tools: list[dict[str, Any]],
        skill_name: str | None = None,
    ) -> list[ChatMessage]:
        """Build model context without silently dropping uncompressed history."""
        messages: list[ChatMessage] = []

        base_prompt = _load_system_prompt()
        if base_prompt:
            messages.append(ChatMessage(role="system", content=base_prompt))

        skills_prompt = build_system_message(skills)
        if skills_prompt:
            messages.append(ChatMessage(role="system", content=skills_prompt))

        if skill_name:
            skill_content = load_skill_content(skill_name)
            if skill_content:
                messages.append(
                    ChatMessage(
                        role="system",
                        content=f"当前激活技能：{skill_name}\n\n{skill_content}",
                    )
                )

        tools_prompt = self._tools_overview(tools)
        if tools_prompt:
            messages.append(ChatMessage(role="system", content=tools_prompt))

        if self.summary:
            messages.append(
                ChatMessage(
                    role="system",
                    content=f"以下是本会话早期对话的摘要：\n{self.summary}",
                )
            )

        messages.extend(self.history[self.summary_upto :])
        return messages

    @staticmethod
    def _tools_overview(tools: list[dict[str, Any]]) -> str:
        if not tools:
            return ""
        lines = ["你可以使用以下工具："]
        for tool in tools:
            function = tool.get("function", {})
            lines.append(f"- {function.get('name', '')}: {function.get('description', '')}")
        return "\n".join(lines)

    def record_user(self, text: str) -> None:
        self.history.append(ChatMessage(role="user", content=text))

    def record_assistant(
        self,
        text: str,
        tool_calls: list[dict[str, Any]] | None = None,
    ) -> None:
        self.history.append(ChatMessage(role="assistant", content=text, tool_calls=tool_calls))

    def record_tool(self, tool_call_id: str, result: str) -> None:
        self.history.append(ChatMessage(role="tool", content=result, tool_call_id=tool_call_id))

    def record_tool_round(
        self,
        messages: tuple[dict[str, Any], ...],
    ) -> None:
        """Atomically record one complete assistant/tool round to history.

        The caller supplies the assistant(tool_calls) message followed by every
        corresponding tool(tool_call_id) result. Building the full list before
        extending history prevents a deserialization error from leaving a
        partial round behind.
        """
        round_messages = [self.deserialize_message(message) for message in messages]
        self.history.extend(round_messages)

    def needs_compact(self) -> bool:
        return len(self.history) - self.summary_upto > WINDOW

    def messages_to_compact(self) -> list[ChatMessage]:
        pending = self.history[self.summary_upto :]
        if len(pending) <= WINDOW:
            return []

        cutoff = len(pending) - WINDOW
        while cutoff < len(pending) and pending[cutoff].role != "user":
            cutoff += 1

        # A conversation is compacted only at a complete user-turn boundary.
        # If the current oversized turn has not finished, retain it in full.
        if cutoff == len(pending):
            return []
        return pending[:cutoff]

    def build_compaction_request(self) -> list[dict[str, str]] | None:
        overflow = self.messages_to_compact()
        if not overflow:
            return None

        prior = f"之前的摘要：\n{self.summary}\n\n" if self.summary else ""
        serialized = "\n".join(json.dumps(message.to_api_dict(), ensure_ascii=False) for message in overflow)
        return [
            {"role": "system", "content": _COMPACT_PROMPT},
            {
                "role": "user",
                "content": f"{prior}新增的对话内容：\n{serialized}",
            },
        ]

    def apply_summary(self, summary: str, message_count: int) -> None:
        if not summary.strip() or message_count <= 0:
            return
        self.summary = summary.strip()
        self.summary_upto = min(self.summary_upto + message_count, len(self.history))

    def ensure_session(self) -> None:
        if self.session_id is None:
            session = memory.create_session(self.serialize_history())
            self.session_id = session.session_id

    def persist(self) -> None:
        if self.session_id is None:
            return
        memory.save_session(
            self.session_id,
            self.serialize_history(),
            summary=self.summary,
            summary_upto=self.summary_upto,
        )

    def load_session(self, session_id: str) -> bool:
        session = memory.load_session(session_id)
        if session is None:
            return False
        self.session_id = session.session_id
        self.summary = session.summary
        self.summary_upto = session.summary_upto
        self.history = [self.deserialize_message(item) for item in session.messages]
        return True

    def clear(self) -> None:
        self.history = []
        self.summary = ""
        self.summary_upto = 0
        self.session_id = None

    def serialize_history(self) -> list[dict[str, Any]]:
        return [message.to_api_dict() for message in self.history]

    @staticmethod
    def deserialize_message(message: dict[str, Any] | ChatMessage) -> ChatMessage:
        if isinstance(message, ChatMessage):
            return message
        return ChatMessage(
            role=message["role"],
            content=message.get("content", ""),
            tool_calls=message.get("tool_calls"),
            tool_call_id=message.get("tool_call_id"),
        )


__all__ = ["WINDOW", "AgentContext"]
