from __future__ import annotations

import unittest

from sparkos.agent.llm_planner import LLMPlanner
from sparkos.agent.runtime import AgentRuntime
from sparkos.ui.chat_app import ChatApp


class ChatAppIntegrationTests(unittest.TestCase):
    def test_chat_app_owns_runtime_facade(self) -> None:
        app = ChatApp()

        self.assertIsInstance(app.runtime, AgentRuntime)

    def test_chat_app_enables_llm_planner(self) -> None:
        app = ChatApp()

        self.assertIsInstance(app.runtime.planner, LLMPlanner)
        self.assertIs(app.runtime.planner.model, app.runtime.client)


if __name__ == "__main__":
    unittest.main()
