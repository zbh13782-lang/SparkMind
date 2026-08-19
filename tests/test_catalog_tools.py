from __future__ import annotations

import inspect
import json
import unittest
from unittest.mock import AsyncMock, patch

from sparkos.agent.tools.registry import TOOL_DEFINITIONS, execute_tool


class CatalogToolTests(unittest.IsolatedAsyncioTestCase):
    def test_registry_exposes_catalog_lookup(self) -> None:
        functions = {item["function"]["name"]: item["function"] for item in TOOL_DEFINITIONS}
        tool = functions["get_data_catalog"]
        self.assertFalse(tool["parameters"]["additionalProperties"])
        self.assertEqual(
            set(tool["parameters"]["properties"]),
            {"database", "table", "search", "refresh"},
        )

    async def test_catalog_lookup_dispatches_json_result(self) -> None:
        fake = AsyncMock()
        fake.get_catalog.return_value = {
            "status": "succeeded",
            "database": "sparkmind_demo",
            "tables": [],
        }
        with patch("sparkos.agent.tools.registry._get_catalog_service", return_value=fake):
            pending = execute_tool("get_data_catalog", {"database": "sparkmind_demo"})
            self.assertTrue(inspect.isawaitable(pending))
            result = await pending

        self.assertEqual(json.loads(result)["status"], "succeeded")
        fake.get_catalog.assert_awaited_once()
        self.assertEqual(fake.get_catalog.await_args.args[0].database, "sparkmind_demo")

    def test_registry_exposes_source_inspection_and_registration(self) -> None:
        functions = {item["function"]["name"]: item["function"] for item in TOOL_DEFINITIONS}
        self.assertEqual(functions["inspect_data_source"]["parameters"]["required"], ["path"])
        self.assertEqual(
            functions["register_dataset"]["parameters"]["required"],
            ["path", "format", "database", "table"],
        )

    async def test_source_registration_dispatches_validated_request(self) -> None:
        fake = AsyncMock()
        fake.register_dataset.return_value = {"status": "succeeded", "qualified_name": "analytics.sales"}
        with patch("sparkos.agent.tools.registry._get_catalog_service", return_value=fake):
            result = await execute_tool(
                "register_dataset",
                {
                    "path": "data/sparkmind_retail/csv/products",
                    "format": "csv",
                    "database": "analytics",
                    "table": "sales",
                    "options": {"header": "true"},
                    "partition_columns": ["dt"],
                },
            )

        self.assertEqual(json.loads(result)["qualified_name"], "analytics.sales")
        request = fake.register_dataset.await_args.args[0]
        self.assertEqual(request.data_format, "csv")
        self.assertEqual(request.partition_columns, ("dt",))
