"""Agent task orchestration across planning, step execution, and synthesis."""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from typing import Any, Protocol

from config.config import (
    ChatConfig,
    RuntimeConfig,
    get_chat_config,
    get_runtime_config,
)
from sparkos.agent.context import AgentContext
from sparkos.agent.events import (
    AgentEvent,
    ClarificationRequested,
    PlanCreated,
    PlanReplanned,
    StepCompleted,
    StepFailed,
    StepStarted,
    StepToolCompleted,
    TaskCompleted,
    TaskFailed,
    TaskStarted,
    TextDelta,
)
from sparkos.agent.llm_planner import LLMPlanner
from sparkos.agent.planner import (
    ClarificationRequest,
    Plan,
    Planner,
    PlanningContext,
    PlanStep,
    Replanner,
    SkillCapability,
)
from sparkos.agent.scheduler import (
    PlanScheduler,
    create_direct_plan,
    create_step_runs,
)
from sparkos.agent.skills.loader import Skill, load_skills
from sparkos.agent.step import (
    StepResult,
    StepRun,
    StepStatus,
)
from sparkos.agent.step_executor import (
    StepExecution,
    StepExecutionError,
    StepExecutor,
    StepToolExecution,
    StepTranscriptUpdate,
    ToolExecutor,
)
from sparkos.agent.task import AgentTask
from sparkos.agent.task_store import TaskStore
from sparkos.agent.tools.registry import TOOL_DEFINITIONS, execute_tool
from sparkos.infrastructure.llm.client import OpenAIChatClient
from sparkos.infrastructure.llm.models import ChatMessage, ToolCall
from sparkos.infrastructure.persistence.task_store import JsonTaskStore


class ModelClient(Protocol):
    async def chat_stream(
        self,
        messages: list[ChatMessage],
        tools: list[dict[str, Any]] | None = None,
    ) -> AsyncIterator[str | ToolCall]: ...

    async def chat_once(
        self,
        messages: list[dict],
        *,
        json_object: bool = False,
    ) -> str: ...


class AgentRuntime:
    """Own the lifecycle of one task while sharing a conversation context."""

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
        runtime_cfg: RuntimeConfig | None = None,
        task_store: TaskStore | None = None,
        scheduler: PlanScheduler | None = None,
        step_executor: StepExecutor | None = None,
        replanner: Replanner | None = None,
    ) -> None:
        rt = runtime_cfg or get_runtime_config()
        max_tool_rounds = rt.max_tool_rounds
        max_replans = rt.max_replans
        if max_tool_rounds < 0:
            raise ValueError("max_tool_rounds 不能小于 0")
        if max_replans < 0:
            raise ValueError("max_replans 不能小于 0")
        if max_replans > 1:
            raise ValueError("每个任务最多允许 1 次重规划")

        self.config = config or get_chat_config()
        self.context = context or AgentContext()
        self.client: ModelClient = client or OpenAIChatClient(self.config)
        self.skills = load_skills() if skills is None else skills
        self.tools = list(TOOL_DEFINITIONS) if tools is None else tools
        self.tool_executor = tool_executor
        self.planner = planner or (
            LLMPlanner(self.client, max_steps=rt.max_steps) if enable_planning else None
        )
        self.max_tool_rounds = max_tool_rounds
        self.task_store = task_store if task_store is not None else JsonTaskStore()
        self.scheduler = scheduler or PlanScheduler()
        self.step_executor = step_executor or StepExecutor(
            client=self.client,
            tools=self.tools,
            tool_executor=self.tool_executor,
            max_tool_rounds=max_tool_rounds,
        )
        if replanner is not None:
            self.replanner = replanner
        elif self.planner is not None and hasattr(self.planner, "revise_plan"):
            self.replanner = self.planner
        else:
            self.replanner = None
        self.max_replans = max_replans

    async def run(
        self,
        task: AgentTask,
        skill_name: str | None = None,
    ) -> AsyncIterator[AgentEvent]:
        """Plan and execute one task, emitting task- and step-scoped events."""
        plan: Plan | None = None
        step_runs: dict[str, StepRun] = {}
        active_run: StepRun | None = None
        replan_count = 0

        try:
            task.start_planning()
            self.context.record_user(task.goal)
            self.context.ensure_session()
            yield TaskStarted(task)

            await self._compact_if_needed()

            planning_decision: Plan | ClarificationRequest | None = None
            if self.planner is not None:
                planning_decision = await self.planner.create_plan(
                    task,
                    self._planning_context(),
                )
            if isinstance(planning_decision, ClarificationRequest):
                question = planning_decision.question
                task.wait_for_input(question)
                self.context.record_assistant(question)
                self.context.persist()
                self._save_task(task, None, {})
                yield ClarificationRequested(task=task, question=question)
                return

            plan = planning_decision
            if plan is None:
                plan = create_direct_plan(task)

            task.active_plan_id = plan.id
            step_runs = create_step_runs(plan)
            self._save_task(task, plan, step_runs)
            yield PlanCreated(plan)

            task.start()
            self._save_task(task, plan, step_runs)
            base_messages = self.context.build_messages(
                skills=self.skills,
                tools=self.tools,
                skill_name=skill_name,
            )

            while not self.scheduler.is_complete(step_runs):
                self.scheduler.block_failed_dependents(plan, step_runs)
                if self.scheduler.has_failed(step_runs):
                    self._save_task(task, plan, step_runs)
                    raise RuntimeError("计划包含失败或被阻塞的步骤")

                ready_steps = self.scheduler.ready_steps(plan, step_runs)
                if not ready_steps:
                    raise RuntimeError("计划无可执行步骤，请检查依赖关系")

                step = ready_steps[0]
                run = step_runs[step.id]
                run.start()
                active_run = run
                self._save_task(task, plan, step_runs)
                yield StepStarted(step)

                dependency_results = {
                    dependency: step_runs[dependency].result
                    for dependency in step.depends_on
                    if step_runs[dependency].result is not None
                }
                execution: StepExecution | None = None
                try:
                    async for update in self.step_executor.stream(
                        task=task,
                        step=step,
                        dependency_results=dependency_results,
                        base_messages=base_messages,
                    ):
                        if isinstance(update, StepTranscriptUpdate):
                            run.record_transcript(update.transcript)
                            self._save_task(task, plan, step_runs)
                        elif isinstance(update, StepToolExecution):
                            run.record_transcript(update.transcript)
                            if update.history_messages:
                                self.context.record_tool_round(update.history_messages)
                            self._save_task(task, plan, step_runs)
                            yield StepToolCompleted(
                                step=step,
                                tool_call=update.tool_call,
                            )
                        else:
                            execution = update
                except Exception as exc:
                    error = str(exc)
                    if isinstance(exc, StepExecutionError):
                        run.record_transcript(exc.transcript)
                    run.fail(error)
                    active_run = None
                    self._save_task(task, plan, step_runs)
                    yield StepFailed(step=step, error=error)
                    raise

                if execution is None:
                    error = "步骤执行未返回结果"
                    run.fail(error)
                    active_run = None
                    self._save_task(task, plan, step_runs)
                    yield StepFailed(step=step, error=error)
                    raise RuntimeError(error)

                run.record_transcript(execution.transcript)

                if not execution.result.success:
                    error = execution.result.error or "步骤执行失败"
                    run.fail(error, execution.result)
                    active_run = None
                    self.scheduler.block_failed_dependents(plan, step_runs)
                    self._save_task(task, plan, step_runs)
                    yield StepFailed(step=step, error=error)
                    if self.replanner is not None and replan_count < self.max_replans:
                        revised_plan = await self._revise_plan(
                            task=task,
                            plan=plan,
                            step_runs=step_runs,
                            failed_step=step,
                            reason=error,
                        )
                        if revised_plan is not None:
                            previous_plan = plan
                            step_runs = self._reconcile_step_runs(
                                previous_plan,
                                revised_plan,
                                step_runs,
                            )
                            plan = revised_plan
                            task.active_plan_id = plan.id
                            replan_count += 1
                            self._save_task(task, plan, step_runs)
                            yield PlanReplanned(
                                previous_plan=previous_plan,
                                plan=plan,
                                reason=error,
                            )
                            continue
                    raise RuntimeError(error)

                run.succeed(execution.result)
                active_run = None
                self._save_task(task, plan, step_runs)
                yield StepCompleted(step=step, result=execution.result)

            if plan.source == "direct":
                final_answer = step_runs[plan.steps[0].id].result
                answer = final_answer.output if final_answer is not None else ""
                if self._is_textual_tool_call(answer):
                    answer = self._fallback_answer(plan, step_runs)
                if answer:
                    chunks = (
                        execution.text_chunks
                        if execution is not None
                        and "".join(execution.text_chunks).strip() == answer
                        else ()
                    )
                    if chunks:
                        for delta in chunks:
                            yield TextDelta(delta)
                    else:
                        yield TextDelta(answer)
            else:
                answer_parts: list[str] = []
                pending_deltas: list[str] = []
                streaming_started = False
                async for delta in self._synthesize_final(
                    task=task,
                    plan=plan,
                    step_runs=step_runs,
                    base_messages=base_messages,
                ):
                    answer_parts.append(delta)
                    if streaming_started:
                        yield TextDelta(delta)
                        continue

                    pending_deltas.append(delta)
                    prefix = "".join(answer_parts).lstrip().casefold()
                    if "<tool_call".startswith(prefix) or prefix.startswith(
                        "<tool_call"
                    ):
                        continue

                    streaming_started = True
                    for pending_delta in pending_deltas:
                        yield TextDelta(pending_delta)
                    pending_deltas.clear()
                answer = "".join(answer_parts).strip()
                if not answer or self._is_textual_tool_call(answer):
                    answer = self._fallback_answer(plan, step_runs)
                    if answer:
                        yield TextDelta(answer)
                elif not streaming_started:
                    for pending_delta in pending_deltas:
                        yield TextDelta(pending_delta)

            task.succeed(answer)
            self.context.record_assistant(answer)
            self.context.persist()
            self._save_task(task, plan, step_runs)
            yield TaskCompleted(task)

        except GeneratorExit:
            task.cancel()
            if active_run is not None and active_run.status == StepStatus.RUNNING:
                active_run.cancel()
            self.context.persist()
            self._save_task_best_effort(task, plan, step_runs)
            return
        except asyncio.CancelledError:
            task.cancel()
            if active_run is not None and active_run.status == StepStatus.RUNNING:
                active_run.cancel()
            self.context.persist()
            self._save_task_best_effort(task, plan, step_runs)
            raise
        except Exception as exc:
            task.fail(str(exc))
            self.context.persist()
            self._save_task_best_effort(task, plan, step_runs)
            yield TaskFailed(task)
            raise

    async def _synthesize_final(
        self,
        task: AgentTask,
        plan: Plan,
        step_runs: dict[str, StepRun],
        base_messages: list[ChatMessage],
    ) -> AsyncIterator[str]:
        messages = list(base_messages)
        payload = {
            "task_goal": task.goal,
            "task_input": task.input,
            "step_results": [
                {
                    "step_id": step.id,
                    "description": step.description,
                    "result": self._serialize_result(step_runs[step.id].result),
                }
                for step in plan.steps
            ],
        }
        self._insert_system_message(
            messages,
            "所有计划步骤已完成。请基于 step_results 给出直接、完整的最终答案，"
            "不要暴露内部执行过程。\n"
            f"{json.dumps(payload, ensure_ascii=False, default=repr)}",
        )
        async for item in self.client.chat_stream(messages, tools=None):
            if isinstance(item, ToolCall):
                raise TypeError("最终结果汇总阶段不允许调用工具")
            yield item

    @staticmethod
    def _serialize_result(result: StepResult | None) -> dict[str, Any] | None:
        if result is None:
            return None
        return {
            "success": result.success,
            "output": result.output,
            "evidence": list(result.evidence),
            "artifacts": [
                {"uri": artifact.uri, "kind": artifact.kind}
                for artifact in result.artifacts
            ],
            "error": result.error,
        }

    @classmethod
    def _fallback_answer(cls, plan: Plan, step_runs: dict[str, StepRun]) -> str:
        for step in reversed(plan.steps):
            result = step_runs[step.id].result
            if (
                result is not None
                and result.output
                and not cls._is_textual_tool_call(result.output)
            ):
                return result.output
        return "未能生成有效的最终回答，请重试。"

    @staticmethod
    def _is_textual_tool_call(text: str) -> bool:
        normalized = text.lstrip().casefold()
        return normalized.startswith("<tool_call") and ">" in normalized

    @staticmethod
    def _insert_system_message(
        messages: list[ChatMessage],
        content: str,
    ) -> None:
        insertion_index = next(
            (
                index
                for index, message in enumerate(messages)
                if message.role != "system"
            ),
            len(messages),
        )
        messages.insert(
            insertion_index,
            ChatMessage(role="system", content=content),
        )

    def _save_task(
        self,
        task: AgentTask,
        plan: Plan | None,
        step_runs: dict[str, StepRun],
    ) -> None:
        self.task_store.save(task, plan, step_runs)

    async def _revise_plan(
        self,
        task: AgentTask,
        plan: Plan,
        step_runs: dict[str, StepRun],
        failed_step: PlanStep,
        reason: str,
    ) -> Plan | None:
        assert self.replanner is not None
        try:
            revised = await self.replanner.revise_plan(
                task=task,
                context=self._planning_context(),
                current_plan=plan,
                step_runs=step_runs,
                failed_step=failed_step,
                reason=reason,
            )
        except Exception:  # noqa: BLE001
            return None
        if revised is None:
            return None
        if revised.task_id != task.id:
            return None
        if revised.id == plan.id:
            return None
        if revised.version != plan.version + 1:
            return None
        if revised.source != "replan":
            return None
        for revised_step in revised.steps:
            if revised_step.id == failed_step.id:
                return None
        return revised

    @staticmethod
    def _reconcile_step_runs(
        previous_plan: Plan,
        revised_plan: Plan,
        previous_runs: dict[str, StepRun],
    ) -> dict[str, StepRun]:
        previous_steps = {step.id: step for step in previous_plan.steps}
        reconciled: dict[str, StepRun] = {}
        for step in revised_plan.steps:
            previous_run = previous_runs.get(step.id)
            if (
                previous_run is not None
                and previous_run.status == StepStatus.SUCCEEDED
                and previous_steps.get(step.id) == step
            ):
                reconciled[step.id] = previous_run
            else:
                reconciled[step.id] = StepRun(step_id=step.id)
        return reconciled

    def _save_task_best_effort(
        self,
        task: AgentTask,
        plan: Plan | None,
        step_runs: dict[str, StepRun],
    ) -> None:
        try:
            self._save_task(task, plan, step_runs)
        except Exception:  # noqa: BLE001
            return

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
            skills=tuple(
                SkillCapability(
                    name=skill.name,
                    description=skill.description,
                )
                for skill in self.skills
            ),
            tool_names=tool_names,
        )

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


__all__ = ["AgentRuntime", "ModelClient", "ToolExecutor"]
