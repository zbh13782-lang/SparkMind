"""Agent task orchestration and model/tool execution loop."""

from __future__ import annotations

import asyncio
import inspect
import json
from collections.abc import AsyncIterator, Awaitable, Callable
from typing import Any, Protocol

from sparkos.agent.context import WINDOW, AgentContext
from sparkos.agent.events import (
    AgentEvent,
    PlanCreated,
    TaskCompleted,
    TaskFailed,
    TaskStarted,
    TextDelta,
    ToolCompleted,
)
from sparkos.agent.llm_planner import LLMPlanner
from sparkos.agent.planner import Plan, Planner, PlanningContext
from sparkos.agent.skills.loader import Skill, load_skills
from sparkos.agent.task import AgentTask
from sparkos.agent.tools.registry import TOOL_DEFINITIONS, execute_tool
from sparkos.infrastructure.llm.client import OpenAIChatClient
from sparkos.infrastructure.llm.models import ChatConfig, ChatMessage, ToolCall

ToolExecutor = Callable[[str, dict[str, Any]], str | Awaitable[str]]


class ModelClient(Protocol):
    async def chat_stream(
        self,
        messages: list[ChatMessage],
        tools: list[dict[str, Any]] | None = None,
    ) -> AsyncIterator[str | ToolCall]: ...

    async def chat_once(self, messages: list[dict]) -> str: ...


class AgentRuntime:
    """Execute independent AgentTask objects against one conversation context."""

    def __init__(
        self,
        config: ChatConfig | None = None,
        *,
        context: AgentContext | None = None,
        client: ModelClient | None = None,
        skills: list[Skill] | None = None,
        tools: list[dict[str, Any]] | None = None,
        tool_executor: ToolExecutor | None = execute_tool,
        planner: Planner | None = None,
        enable_planning: bool = False,
        max_tool_rounds: int = 8,
    ) -> None:
        if max_tool_rounds < 0:
            raise ValueError("max_tool_rounds 不能小于 0")

        self.config = config or ChatConfig.from_yaml()
        self.context = context or AgentContext()
        self.client: ModelClient = client or OpenAIChatClient(self.config)
        self.skills = load_skills() if skills is None else skills
        self.tools = list(TOOL_DEFINITIONS) if tools is None else tools
        self.tool_executor = tool_executor
        self.planner = planner or (LLMPlanner(self.client) if enable_planning else None)
        self.max_tool_rounds = max_tool_rounds

    async def run(
        self,
        task: AgentTask,
        skill_name: str | None = None,
    ) -> AsyncIterator[AgentEvent]:
        """Run one task and emit lifecycle, text, planning, and tool events."""
        task.start()
        self.context.record_user(task.goal)
        self.context.ensure_session()
        yield TaskStarted(task)

        try:
            await self._compact_if_needed()

            plan: Plan | None = None
            if self.planner is not None:
                plan = await self.planner.create_plan(task, self._planning_context())
                if plan is not None:
                    task.active_plan_id = plan.id
                    yield PlanCreated(plan)

            messages = self.context.build_messages(
                skills=self.skills,
                tools=self.tools,
                skill_name=skill_name,
            )
            if plan is not None:
                self._inject_plan(messages, plan)
            answer_parts: list[str] = []
            tool_rounds = 0

            while True:
                turn_parts: list[str] = []
                tool_calls: list[ToolCall] = []

                async for item in self.client.chat_stream(
                    messages,
                    tools=self.tools or None,
                ):
                    if isinstance(item, str):
                        turn_parts.append(item)
                        answer_parts.append(item)
                        yield TextDelta(item)
                    else:
                        tool_calls.append(item)

                turn_text = "".join(turn_parts)
                serialized_calls = (
                    [tool_call.to_api_dict() for tool_call in tool_calls]
                    if tool_calls
                    else None
                )
                assistant_message = ChatMessage(
                    role="assistant",
                    content=turn_text,
                    tool_calls=serialized_calls,
                )
                self.context.record_assistant(turn_text, serialized_calls)
                messages.append(assistant_message)

                if not tool_calls:
                    break

                if tool_rounds >= self.max_tool_rounds:
                    error = f"工具调用轮数超过限制（{self.max_tool_rounds}）"
                    for tool_call in tool_calls:
                        tool_call.result = error
                        self._record_tool_result(messages, tool_call)
                        yield ToolCompleted(tool_call)
                    raise RuntimeError(error)

                tool_rounds += 1
                for tool_call in tool_calls:
                    tool_call.result = await self._execute_tool_call(tool_call)
                    self._record_tool_result(messages, tool_call)
                    yield ToolCompleted(tool_call)

            task.succeed("".join(answer_parts))
            self.context.persist()
            yield TaskCompleted(task)

        except asyncio.CancelledError:
            task.cancel()
            self.context.persist()
            raise
        except Exception as exc:
            task.fail(str(exc))
            self.context.persist()
            yield TaskFailed(task)
            raise

    def _record_tool_result(
        self,
        messages: list[ChatMessage],
        tool_call: ToolCall,
    ) -> None:
        self.context.record_tool(tool_call.call_id, tool_call.result)
        messages.append(
            ChatMessage(
                role="tool",
                content=tool_call.result,
                tool_call_id=tool_call.call_id,
            )
        )

    async def _execute_tool_call(self, tool_call: ToolCall) -> str:
        if self.tool_executor is None:
            return "工具执行失败：未配置工具执行器"

        try:
            arguments = json.loads(tool_call.arguments or "{}")
        except json.JSONDecodeError as exc:
            return f"工具参数不是有效 JSON：{exc}"

        try:
            if inspect.iscoroutinefunction(self.tool_executor):
                result = await self.tool_executor(tool_call.name, arguments)
            else:
                result = await asyncio.to_thread(
                    self.tool_executor,
                    tool_call.name,
                    arguments,
                )
            if inspect.isawaitable(result):
                result = await result
            return str(result)
        except Exception as exc:  # noqa: BLE001
            return f"工具执行失败：{type(exc).__name__}: {exc}"

    async def _compact_if_needed(self) -> bool:
        request = self.context.build_compaction_request()
        if request is None:
            return False
        compacted_count = len(self.context.messages_to_compact())
        try:
            summary = (await self.client.chat_once(request)).strip()
        except Exception:  # noqa: BLE001
            return False
        if not summary:
            return False
        self.context.apply_summary(summary, compacted_count)
        return True

    def _planning_context(self) -> PlanningContext:
        tool_names = tuple(
            tool.get("function", {}).get("name", "") for tool in self.tools
        )
        return PlanningContext(
            session_id=self.context.session_id,
            summary=self.context.summary,
            recent_messages=tuple(self.context.history[self.context.summary_upto :]),
            skill_names=tuple(skill.name for skill in self.skills),
            tool_names=tool_names,
        )

    @staticmethod
    def _inject_plan(messages: list[ChatMessage], plan: Plan) -> None:
        plan_payload = {
            "plan_id": plan.id,
            "version": plan.version,
            "steps": [
                {
                    "id": step.id,
                    "description": step.description,
                    "depends_on": step.depends_on,
                }
                for step in plan.steps
            ],
        }
        plan_message = ChatMessage(
            role="system",
            content=(
                "当前任务已生成以下执行计划。请遵守依赖关系逐步执行；"
                "如果现实信息与计划冲突，可以调整执行细节，但不要跳过任务目标。\n"
                f"{json.dumps(plan_payload, ensure_ascii=False)}"
            ),
        )
        insertion_index = next(
            (
                index
                for index, message in enumerate(messages)
                if message.role != "system"
            ),
            len(messages),
        )
        messages.insert(insertion_index, plan_message)

    def get_tools(self) -> list[dict[str, Any]]:
        return self.tools

    def reload_skills(self) -> None:
        self.skills = load_skills()

    def load_session(self, session_id: str) -> bool:
        return self.context.load_session(session_id)

    def clear(self) -> None:
        self.context.clear()

    @property
    def history(self) -> list[ChatMessage]:
        return self.context.history


__all__ = ["WINDOW", "AgentRuntime", "ModelClient", "ToolExecutor"]
