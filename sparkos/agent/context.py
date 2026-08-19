"""Agent 的上下文组装"""

from __future__ import annotations

import json
from collections.abc import Mapping
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
except (OSError, KeyError, TypeError, ValueError):
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
        skill_names: tuple[str, ...] | None = None,
        catalog_summary: Mapping[str, Any] | None = None,
        history_start: int | None = None,
        max_history_chars: int = 12_000,
    ) -> list[ChatMessage]:
        """Build model context without silently dropping uncompressed history."""
        messages: list[ChatMessage] = []

        base_prompt = _load_system_prompt()
        if base_prompt:
            messages.append(ChatMessage(role="system", content=base_prompt))

        skills_prompt = build_system_message(skills)
        if skills_prompt:
            messages.append(ChatMessage(role="system", content=skills_prompt))

        active_names = skill_names
        if active_names is None and skill_name:
            active_names = (skill_name,)
        for active_name in active_names or ():
            skill_content = load_skill_content(active_name)
            if skill_content:
                messages.append(
                    ChatMessage(
                        role="system",
                        content=f"当前步骤激活技能：{active_name}\n\n{skill_content}",
                    )
                )

        tools_prompt = self._tools_overview(tools)
        if tools_prompt:
            messages.append(ChatMessage(role="system", content=tools_prompt))

        catalog_prompt = self._catalog_overview(catalog_summary or {})
        if catalog_prompt:
            messages.append(ChatMessage(role="system", content=catalog_prompt))

        if self.summary:
            messages.append(
                ChatMessage(
                    role="system",
                    content=f"以下是本会话早期对话的摘要：\n{self.summary}",
                )
            )

        start = self.summary_upto if history_start is None else max(history_start, self.summary_upto)
        history = self.history[start:]
        messages.extend(self._bound_history(history, max_history_chars))
        return messages

    @staticmethod
    def _bound_history(history: list[ChatMessage], max_chars: int) -> list[ChatMessage]:
        if max_chars <= 0:
            return []
        total = sum(len(message.content or "") for message in history)
        if total <= max_chars:
            return list(history)

        remaining = max_chars
        bounded: list[ChatMessage] = []
        for message in reversed(history):
            content = message.content or ""
            if len(content) > remaining:
                content = "[历史工具结果已截断，仅保留最近上下文片段]\n" + content[-max(0, remaining - 28) :]
            bounded.append(
                ChatMessage(
                    role=message.role,
                    content=content,
                    tool_calls=message.tool_calls,
                    tool_call_id=message.tool_call_id,
                )
            )
            remaining -= len(content)
            if remaining <= 0:
                break
        return list(reversed(bounded))

    @staticmethod
    def _tools_overview(tools: list[dict[str, Any]]) -> str:
        if not tools:
            return ""
        lines = ["你可以使用以下工具："]
        for tool in tools:
            function = tool.get("function", {})
            lines.append(f"- {function.get('name', '')}: {function.get('description', '')}")
        return "\n".join(lines)

    @staticmethod
    def _catalog_overview(summary: Mapping[str, Any]) -> str:
        database = str(summary.get("default_database", "")).strip()
        tables = summary.get("tables", [])
        if not database or not isinstance(tables, list):
            return ""
        names = ", ".join(str(item) for item in tables[:50]) or "（暂无已缓存表）"
        stale = "；当前摘要可能过期，请先调用 get_data_catalog(refresh=true)" if summary.get("stale") else ""
        return f"当前数据目录摘要：默认数据库 {database}；可用表 {names}{stale}。字段和分区请通过 get_data_catalog 按需获取。"

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
