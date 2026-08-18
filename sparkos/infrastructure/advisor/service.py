"""Advisor service: asks a separately-configured model for bounded advice."""

from __future__ import annotations

import asyncio
import time
from typing import Protocol

from config.config import ChatConfig, get_advisor_config
from config.config import AdvisorConfig
from sparkos.infrastructure.advisor.models import AdvisorRequest, AdvisorResult
from sparkos.infrastructure.llm.client import OpenAIChatClient


class ChatModel(Protocol):
    async def chat_once(
        self,
        messages: list[dict],
        *,
        json_object: bool = False,
    ) -> str: ...


_ADVISOR_SYSTEM_PROMPT = (
    "你是主 Agent 的高级顾问。你只提供分析和建议，不执行工具、不假设未提供的上下文。"
    "请按“建议、理由、风险、验证方式”组织回答。指出不确定性；不要把建议描述成已经执行或验证的结果。"
)


class AdvisorService:
    def __init__(
        self,
        config: AdvisorConfig,
        client: ChatModel | None = None,
    ) -> None:
        self.config = config
        self.client = client or OpenAIChatClient(
            ChatConfig(
                base_url=config.base_url,
                api_key=config.api_key,
                model=config.model,
            )
        )

    @classmethod
    def from_config(cls, config: AdvisorConfig | None = None) -> AdvisorService:
        return cls(config or get_advisor_config())

    async def ask(self, request: AdvisorRequest) -> AdvisorResult:
        if not self.config.enabled:
            return AdvisorResult(
                status="disabled",
                model=self.config.model,
                answer="",
                duration_seconds=0.0,
            )

        request.validate(self.config)
        started = time.monotonic()
        user_message = {
            "role": "user",
            "content": (f"问题：{request.question}\n\n上下文：{request.context}\n\n已尝试：{request.attempts}"),
        }

        try:
            async with asyncio.timeout(self.config.timeout_seconds):
                answer = await self.client.chat_once(
                    [
                        {"role": "system", "content": _ADVISOR_SYSTEM_PROMPT},
                        user_message,
                    ],
                    json_object=False,
                )
        except asyncio.CancelledError:
            raise
        except TimeoutError:
            return AdvisorResult(
                status="timed_out",
                model=self.config.model,
                answer="",
                duration_seconds=time.monotonic() - started,
            )
        except Exception as exc:
            return AdvisorResult(
                status="failed",
                model=self.config.model,
                answer="",
                duration_seconds=time.monotonic() - started,
                error=f"{type(exc).__name__}: {exc}",
            )

        if not answer.strip():
            return AdvisorResult(
                status="failed",
                model=self.config.model,
                answer="",
                duration_seconds=time.monotonic() - started,
                error="advisor 返回空响应",
            )

        return AdvisorResult(
            status="succeeded",
            model=self.config.model,
            answer=answer.strip(),
            duration_seconds=time.monotonic() - started,
        )
