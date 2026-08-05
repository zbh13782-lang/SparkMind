from __future__ import annotations

import json
import unittest

from sparkos.agent.planner import PlanStep
from sparkos.agent.step import StepResult
from sparkos.agent.task import AgentTask
from sparkos.agent.verifier import LLMStepVerifier


class FakeVerificationModel:
    def __init__(self, response: str | Exception) -> None:
        self.response = response
        self.requests: list[list[dict]] = []

    async def chat_once(self, messages: list[dict]) -> str:
        self.requests.append(messages)
        if isinstance(self.response, Exception):
            raise self.response
        return self.response


def sample_step() -> PlanStep:
    return PlanStep(
        id="s2",
        description="summarize weather",
        depends_on=("s1",),
        success_criteria="contains temperature and humidity",
    )


class LLMStepVerifierTests(unittest.IsolatedAsyncioTestCase):
    async def test_rejection_is_parsed_and_request_contains_candidate_context(
        self,
    ) -> None:
        model = FakeVerificationModel(
            json.dumps(
                {
                    "passed": False,
                    "reason": "missing temperature",
                    "retryable": True,
                    "evidence": ["humidity present"],
                }
            )
        )
        verifier = LLMStepVerifier(model)

        verification = await verifier.verify(
            task=AgentTask(goal="weather"),
            step=sample_step(),
            result=StepResult(success=True, output="humidity is 55%"),
            dependency_results={
                "s1": StepResult(success=True, output="raw weather loaded")
            },
        )

        self.assertFalse(verification.passed)
        self.assertTrue(verification.retryable)
        self.assertEqual(verification.reason, "missing temperature")
        self.assertEqual(verification.evidence, ("humidity present",))
        payload = json.loads(model.requests[0][1]["content"])
        self.assertEqual(
            payload["step"]["success_criteria"],
            "contains temperature and humidity",
        )
        self.assertEqual(payload["candidate_result"]["output"], "humidity is 55%")
        self.assertEqual(
            payload["dependency_results"]["s1"]["output"],
            "raw weather loaded",
        )

    async def test_malformed_response_fails_open_and_records_error(self) -> None:
        verifier = LLMStepVerifier(FakeVerificationModel("not-json"))

        verification = await verifier.verify(
            task=AgentTask(goal="weather"),
            step=sample_step(),
            result=StepResult(success=True, output="complete report"),
            dependency_results={},
        )

        self.assertTrue(verification.passed)
        self.assertFalse(verification.retryable)
        self.assertIn("JSON", verification.error or "")

    async def test_wrapped_json_fails_open_instead_of_becoming_rejection(
        self,
    ) -> None:
        payload = json.dumps(
            {
                "passed": False,
                "reason": "missing temperature",
                "retryable": True,
                "evidence": [],
            }
        )
        for response in (f"result: {payload}", f"```json\n{payload}\n```"):
            with self.subTest(response=response):
                verifier = LLMStepVerifier(FakeVerificationModel(response))

                verification = await verifier.verify(
                    task=AgentTask(goal="weather"),
                    step=sample_step(),
                    result=StepResult(success=True, output="complete report"),
                    dependency_results={},
                )

                self.assertTrue(verification.passed)
                self.assertFalse(verification.retryable)
                self.assertIsNotNone(verification.error)

    async def test_model_failure_fails_open_and_records_error(self) -> None:
        verifier = LLMStepVerifier(
            FakeVerificationModel(ConnectionError("verifier offline"))
        )

        verification = await verifier.verify(
            task=AgentTask(goal="weather"),
            step=sample_step(),
            result=StepResult(success=True, output="complete report"),
            dependency_results={},
        )

        self.assertTrue(verification.passed)
        self.assertIn("verifier offline", verification.error or "")


if __name__ == "__main__":
    unittest.main()
