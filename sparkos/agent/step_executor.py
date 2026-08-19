"""Bounded model/tool loop for one immutable PlanStep."""

from __future__ import annotations

import asyncio
import inspect
import json
from collections.abc import AsyncIterator, Awaitable, Callable
from copy import deepcopy
from dataclasses import dataclass
from typing import Any, Protocol

from sparkos.agent.planner import PlanStep
from sparkos.agent.step import ArtifactRef, StepResult
from sparkos.agent.task import AgentTask
from sparkos.infrastructure.llm.models import ChatMessage, ToolCall

type ToolExecutor = Callable[[str, dict[str, Any]], str | Awaitable[str]]


class StepModel(Protocol):
    async def chat_stream(
        self,
        messages: list[ChatMessage],
        tools: list[dict[str, Any]] | None = None,
    ) -> AsyncIterator[str | ToolCall]: ...


class StepExecutionError(RuntimeError):
    def __init__(
        self,
        message: str,
        transcript: tuple[dict[str, Any], ...] = (),
    ) -> None:
        super().__init__(message)
        self.transcript = deepcopy(transcript)


@dataclass(frozen=True)
class StepToolExecution:
    tool_call: ToolCall
    transcript: tuple[dict[str, Any], ...]
    history_messages: tuple[dict[str, Any], ...] = ()


@dataclass(frozen=True)
class StepTranscriptUpdate:
    transcript: tuple[dict[str, Any], ...]


@dataclass(frozen=True)
class StepExecution:
    result: StepResult
    tool_calls: tuple[ToolCall, ...] = ()
    transcript: tuple[dict[str, Any], ...] = ()
    text_chunks: tuple[str, ...] = ()


class StepExecutor:
    # Tool output is fed back into the model on the next round.  Keep that
    # per-round payload bounded even when a query prints hundreds of rows.
    MAX_TOOL_RESULT_CHARS = 12_000
    MAX_TOOL_CONTEXT_CHARS = 24_000

    def __init__(
        self,
        client: StepModel,
        tools: list[dict[str, Any]],
        tool_executor: ToolExecutor | None,
        max_tool_rounds: int = 8,
        tool_call_limits: dict[str, int] | None = None,
    ) -> None:
        if max_tool_rounds < 0:
            raise ValueError("max_tool_rounds 不能小于 0")
        self.client = client
        self.tools = tools
        self.tool_executor = tool_executor
        self.max_tool_rounds = max_tool_rounds
        self.tool_call_limits: dict[str, int] = {}
        if tool_call_limits:
            for name, limit in tool_call_limits.items():
                if not name or not isinstance(limit, int) or limit < 1:
                    raise ValueError(f"tool_call_limits 中的 {name!r} 无效")
                self.tool_call_limits[name] = limit

    async def execute(
        self,
        task: AgentTask,
        step: PlanStep,
        dependency_results: dict[str, StepResult],
        base_messages: list[ChatMessage],
    ) -> StepExecution:
        execution: StepExecution | None = None
        async for update in self.stream(
            task=task,
            step=step,
            dependency_results=dependency_results,
            base_messages=base_messages,
        ):
            if isinstance(update, StepExecution):
                execution = update
        if execution is None:
            raise StepExecutionError("步骤执行未返回结果")
        return execution

    async def stream(
        self,
        task: AgentTask,
        step: PlanStep,
        dependency_results: dict[str, StepResult],
        base_messages: list[ChatMessage],
    ) -> AsyncIterator[StepTranscriptUpdate | StepToolExecution | StepExecution]:
        messages = list(base_messages)
        self._inject_step_context(
            messages,
            task,
            step,
            dependency_results,
        )
        transcript: list[dict[str, Any]] = []
        executed_calls: list[ToolCall] = []
        tool_rounds = 0
        empty_result_retries = 0
        tool_call_counts: dict[str, int] = {}

        while True:
            turn_parts: list[str] = []
            tool_calls: list[ToolCall] = []
            self._trim_tool_context(messages)
            try:
                async for item in self.client.chat_stream(
                    messages,
                    tools=self.tools or None,
                ):
                    if isinstance(item, str):
                        turn_parts.append(item)
                    else:
                        tool_calls.append(item)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                if turn_parts:
                    partial_message = ChatMessage(
                        role="assistant",
                        content="".join(turn_parts),
                    )
                    transcript.append(partial_message.to_api_dict())
                raise StepExecutionError(
                    f"步骤模型调用失败：{type(exc).__name__}: {exc}",
                    tuple(transcript),
                ) from exc

            turn_text = "".join(turn_parts)
            serialized_calls = [tool_call.to_api_dict() for tool_call in tool_calls] if tool_calls else None
            assistant_message = ChatMessage(
                role="assistant",
                content=turn_text,
                tool_calls=serialized_calls,
            )
            messages.append(assistant_message)
            transcript.append(assistant_message.to_api_dict())
            if not tool_calls:
                if not turn_text.strip() and executed_calls and empty_result_retries == 0:
                    empty_result_retries += 1
                    retry_message = ChatMessage(
                        role="system",
                        content=(
                            "上一轮没有输出最终文本。请直接基于已有工具结果"
                            "完成 current_step，给出明确的最终结果；不要返回空内容。"
                        ),
                    )
                    messages.append(retry_message)
                    transcript.append(retry_message.to_api_dict())
                    yield StepTranscriptUpdate(
                        transcript=tuple(deepcopy(transcript)),
                    )
                    continue
                break

            # Persist the model's tool-call intent before executing the tool.
            # This closes the cancellation window around long-running tools.
            yield StepTranscriptUpdate(
                transcript=tuple(deepcopy(transcript)),
            )

            if tool_rounds >= self.max_tool_rounds:
                error = f"工具调用轮数超过限制（{self.max_tool_rounds}）"
                tool_result_messages: list[dict[str, Any]] = []
                for index, tool_call in enumerate(tool_calls):
                    tool_call.result = error
                    tool_message = self._append_tool_result(messages, tool_call)
                    serialized_tool_message = tool_message.to_api_dict()
                    transcript.append(serialized_tool_message)
                    tool_result_messages.append(serialized_tool_message)
                    yield StepToolExecution(
                        tool_call=tool_call,
                        transcript=tuple(deepcopy(transcript)),
                        history_messages=(
                            self._tool_round_history(
                                assistant_message,
                                tool_result_messages,
                            )
                            if index == len(tool_calls) - 1
                            else ()
                        ),
                    )
                raise StepExecutionError(error, tuple(transcript))

            tool_rounds += 1
            tool_result_messages = []
            for index, tool_call in enumerate(tool_calls):
                limit = self.tool_call_limits.get(tool_call.name)
                if limit is not None:
                    count = tool_call_counts.get(tool_call.name, 0)
                    if count >= limit:
                        error_msg = f"工具调用次数超过单步限制（{tool_call.name}: {limit}）"
                        tool_call.result = error_msg
                        tool_message = self._append_tool_result(messages, tool_call)
                        serialized_tool_message = tool_message.to_api_dict()
                        transcript.append(serialized_tool_message)
                        tool_result_messages.append(serialized_tool_message)
                        yield StepToolExecution(
                            tool_call=tool_call,
                            transcript=tuple(deepcopy(transcript)),
                            history_messages=(
                                self._tool_round_history(
                                    assistant_message,
                                    tool_result_messages,
                                )
                                if index == len(tool_calls) - 1
                                else ()
                            ),
                        )
                        continue
                    tool_call_counts[tool_call.name] = count + 1
                tool_call.result = await self._execute_tool_call(tool_call)
                executed_calls.append(tool_call)
                tool_message = self._append_tool_result(messages, tool_call)
                serialized_tool_message = tool_message.to_api_dict()
                transcript.append(serialized_tool_message)
                tool_result_messages.append(serialized_tool_message)
                yield StepToolExecution(
                    tool_call=tool_call,
                    transcript=tuple(deepcopy(transcript)),
                    history_messages=(
                        self._tool_round_history(
                            assistant_message,
                            tool_result_messages,
                        )
                        if index == len(tool_calls) - 1
                        else ()
                    ),
                )

        output = turn_text.strip()
        if not output:
            yield StepExecution(
                result=StepResult(
                    success=False,
                    output="",
                    error="步骤未产出结果",
                ),
                tool_calls=tuple(executed_calls),
                transcript=tuple(deepcopy(transcript)),
            )
            return
        yield StepExecution(
            result=StepResult(success=True, output=output),
            tool_calls=tuple(executed_calls),
            transcript=tuple(deepcopy(transcript)),
            text_chunks=tuple(turn_parts),
        )

    @staticmethod
    def _inject_step_context(
        messages: list[ChatMessage],
        task: AgentTask,
        step: PlanStep,
        dependency_results: dict[str, StepResult],
    ) -> None:
        payload = {
            "task_goal": task.goal,
            "task_input": task.input,
            "current_step": {
                "id": step.id,
                "description": step.description,
                "success_criteria": step.success_criteria,
            },
            "dependencies": {
                step_id: StepExecutor._serialize_result(result) for step_id, result in dependency_results.items()
            },
        }
        step_message = ChatMessage(
            role="system",
            content=(
                "你正在执行计划中的一个步骤。只完成 current_step，并产出可供后续"
                "步骤使用的明确结果；可以使用提供的工具。\n"
                f"{json.dumps(payload, ensure_ascii=False, default=repr)}"
            ),
        )
        insertion_index = next(
            (index for index, message in enumerate(messages) if message.role != "system"),
            len(messages),
        )
        messages.insert(insertion_index, step_message)

    @staticmethod
    def _serialize_result(result: StepResult) -> dict[str, Any]:
        return {
            "success": result.success,
            "output": StepExecutor._truncate(result.output),
            "evidence": [StepExecutor._truncate(item, 1000) for item in result.evidence[:20]],
            "artifacts": [StepExecutor._serialize_artifact(artifact) for artifact in result.artifacts],
            "error": result.error,
        }

    @staticmethod
    def _truncate(text: str, limit: int = 12_000) -> str:
        if len(text) <= limit:
            return text
        marker = f"\n[结果已截断，原长度 {len(text)} 字符]"
        return f"{text[: max(0, limit - len(marker))]}{marker}"[:limit]

    @classmethod
    def _trim_tool_context(cls, messages: list[ChatMessage]) -> None:
        """Bound accumulated tool payload while preserving API message pairs.

        Older tool messages are shortened first; the assistant tool-call
        messages and the newest results remain available for the next turn.
        """
        tool_messages = [message for message in messages if message.role == "tool"]
        total = sum(len(message.content or "") for message in tool_messages)
        if total <= cls.MAX_TOOL_CONTEXT_CHARS:
            return

        for message in tool_messages:
            if total <= cls.MAX_TOOL_CONTEXT_CHARS:
                break
            content = message.content or ""
            excess = total - cls.MAX_TOOL_CONTEXT_CHARS
            target = max(256, len(content) - excess)
            if target >= len(content):
                continue
            message.content = cls._truncate(content, target)
            total = sum(len(item.content or "") for item in tool_messages)

    @staticmethod
    def _serialize_artifact(artifact: ArtifactRef) -> dict[str, str]:
        return {"uri": artifact.uri, "kind": artifact.kind}

    @staticmethod
    def _append_tool_result(
        messages: list[ChatMessage],
        tool_call: ToolCall,
    ) -> ChatMessage:
        message = ChatMessage(
            role="tool",
            content=tool_call.result,
            tool_call_id=tool_call.call_id,
        )
        messages.append(message)
        return message

    @staticmethod
    def _tool_round_history(
        assistant_message: ChatMessage,
        tool_result_messages: list[dict[str, Any]],
    ) -> tuple[dict[str, Any], ...]:
        return tuple(deepcopy([assistant_message.to_api_dict(), *tool_result_messages]))

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
            return self._truncate(str(result), self.MAX_TOOL_RESULT_CHARS)
        except Exception as exc:  # noqa: BLE001
            return f"工具执行失败：{type(exc).__name__}: {exc}"


__all__ = [
    "StepExecution",
    "StepExecutionError",
    "StepExecutor",
    "StepModel",
    "StepToolExecution",
    "StepTranscriptUpdate",
    "ToolExecutor",
]
