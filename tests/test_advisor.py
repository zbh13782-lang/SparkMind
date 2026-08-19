"""Tests for the isolated advisor service."""

from __future__ import annotations

import asyncio
import unittest
from unittest.mock import AsyncMock

from sparkos.infrastructure.advisor.models import AdvisorRequest
from sparkos.infrastructure.advisor.service import AdvisorService


class AdvisorServiceTests(unittest.IsolatedAsyncioTestCase):
    def _make_service(self, enabled: bool = True, timeout: int = 90, **overrides) -> AdvisorService:
        from config.config import AdvisorConfig

        kwargs = {
            "enabled": enabled,
            "base_url": "http://test/v1",
            "api_key": "key",
            "model": "advisor",
            "timeout_seconds": timeout,
            "max_question_chars": 4000,
            "max_context_chars": 16000,
            "max_attempts_chars": 8000,
        }
        kwargs.update(overrides)
        return AdvisorService(AdvisorConfig(**kwargs))

    async def test_disabled_returns_without_calling_model(self) -> None:
        fake = AsyncMock()
        service = self._make_service(enabled=False)
        result = await service.ask(AdvisorRequest(question="q", context="c", attempts="a"))
        self.assertEqual(result.status, "disabled")
        self.assertEqual(result.answer, "")
        fake.assert_not_called()

    async def test_successful_consultation(self) -> None:
        fake_client = AsyncMock()
        fake_client.chat_once.return_value = "Use full jitter."
        service = AdvisorService.__new__(AdvisorService)
        service.config = self._make_service().config
        service.client = fake_client

        request = AdvisorRequest(
            question="How should retries change?",
            context="Intermittent 503 responses.",
            attempts="Fixed delay.",
        )
        result = await service.ask(request)

        self.assertEqual(result.status, "succeeded")
        self.assertEqual(result.model, "advisor")
        self.assertEqual(result.answer, "Use full jitter.")
        self.assertEqual(len(fake_client.chat_once.call_args_list), 1)
        messages = fake_client.chat_once.call_args.args[0]
        self.assertEqual(messages[0]["role"], "system")
        self.assertEqual(messages[1]["role"], "user")
        self.assertNotIn("tools", messages[1])

    async def test_timeout_returns_timed_out(self) -> None:
        fake_client = AsyncMock()
        fake_client.chat_once.side_effect = TimeoutError()
        service = AdvisorService.__new__(AdvisorService)
        service.config = self._make_service(timeout=1).config
        service.client = fake_client

        result = await service.ask(AdvisorRequest(question="q?", context="c", attempts="a"))
        self.assertEqual(result.status, "timed_out")

    async def test_empty_response_returns_failed(self) -> None:
        fake_client = AsyncMock()
        fake_client.chat_once.return_value = "   "
        service = AdvisorService.__new__(AdvisorService)
        service.config = self._make_service().config
        service.client = fake_client

        result = await service.ask(AdvisorRequest(question="q?", context="c", attempts="a"))
        self.assertEqual(result.status, "failed")
        self.assertIn("空", result.error)

    async def test_model_exception_returns_failed(self) -> None:
        fake_client = AsyncMock()
        fake_client.chat_once.side_effect = RuntimeError("connection lost")
        service = AdvisorService.__new__(AdvisorService)
        service.config = self._make_service().config
        service.client = fake_client

        result = await service.ask(AdvisorRequest(question="q?", context="c", attempts="a"))
        self.assertEqual(result.status, "failed")
        self.assertIn("RuntimeError", result.error)

    async def test_cancelled_error_propagates(self) -> None:
        fake_client = AsyncMock()
        fake_client.chat_once.side_effect = asyncio.CancelledError()
        service = AdvisorService.__new__(AdvisorService)
        service.config = self._make_service(timeout=10).config
        service.client = fake_client

        with self.assertRaises(asyncio.CancelledError):
            await service.ask(AdvisorRequest(question="q?", context="c", attempts="a"))

    def test_request_rejects_empty_or_oversized_fields(self) -> None:
        config = self._make_service().config
        req = AdvisorRequest(question="", context="c", attempts="a")
        with self.assertRaises(ValueError):
            req.validate(config)

        req = AdvisorRequest(question="ok", context="", attempts="a")
        with self.assertRaises(ValueError):
            req.validate(config)

        req = AdvisorRequest(question="ok", context="c", attempts="")
        with self.assertRaises(ValueError):
            req.validate(config)

        req = AdvisorRequest(question="x" * 4001, context="c", attempts="a")
        with self.assertRaises(ValueError):
            req.validate(config)
