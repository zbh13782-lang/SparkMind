from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock

from config.config import CatalogConfig
from sparkos.infrastructure.catalog.models import (
    CatalogQuery,
    CatalogSnapshot,
    ColumnMetadata,
    DatabaseMetadata,
    TableMetadata,
)
from sparkos.infrastructure.catalog.service import CatalogService
from sparkos.infrastructure.catalog.store import CatalogStore


def _snapshot() -> CatalogSnapshot:
    return CatalogSnapshot(
        generated_at="2026-08-18T10:00:00+00:00",
        databases=(
            DatabaseMetadata(
                name="sparkmind_demo",
                tables=(
                    TableMetadata(
                        database="sparkmind_demo",
                        name="fact_order",
                        table_type="MANAGED",
                        provider="parquet",
                        location="file:/warehouse/fact_order",
                        columns=(
                            ColumnMetadata("order_id", "string", True),
                            ColumnMetadata("dt", "date", True, True),
                        ),
                    ),
                ),
            ),
        ),
    )


def _config(tmp_path: Path) -> CatalogConfig:
    return CatalogConfig(
        enabled=True,
        default_database="sparkmind_demo",
        cache_path="catalog.json",
        cache_ttl_seconds=300,
        semantic_path="missing.yaml",
        max_tables_per_response=50,
        max_columns_per_response=200,
    )


class CatalogServiceTests(unittest.IsolatedAsyncioTestCase):
    async def test_get_catalog_uses_fresh_cache_without_spark(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            store = CatalogStore(root / "catalog.json", ttl_seconds=300)
            store.save(_snapshot())
            backend = AsyncMock()
            service = CatalogService(_config(root), store, backend)

            result = await service.get_catalog(CatalogQuery())

            self.assertEqual(result["default_database"], "sparkmind_demo")
            self.assertEqual(result["tables"][0]["name"], "fact_order")
            backend.discover.assert_not_awaited()

    async def test_specific_table_returns_columns(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            store = CatalogStore(root / "catalog.json", ttl_seconds=300)
            store.save(_snapshot())
            service = CatalogService(_config(root), store, AsyncMock())

            result = await service.get_catalog(CatalogQuery(database="sparkmind_demo", table="fact_order"))

            self.assertEqual(result["table"]["qualified_name"], "sparkmind_demo.fact_order")
            self.assertIn("order_id", [column["name"] for column in result["table"]["columns"]])
            self.assertIn("dt", result["table"]["partition_columns"])

    async def test_refresh_replaces_cache_and_returns_new_snapshot(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            store = CatalogStore(root / "catalog.json", ttl_seconds=300)
            store.save(_snapshot())
            newer = _snapshot()
            backend = AsyncMock()
            backend.discover.return_value = newer
            service = CatalogService(_config(root), store, backend)

            result = await service.get_catalog(CatalogQuery(refresh=True))

            self.assertEqual(result["tables"][0]["qualified_name"], "sparkmind_demo.fact_order")
            backend.discover.assert_awaited_once()

    async def test_semantic_overlay_adds_metric_without_replacing_schema(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            semantic_path = root / "semantic.yaml"
            semantic_path.write_text(
                "tables:\n"
                "  sparkmind_demo.fact_order:\n"
                "    description: 订单事实表\n"
                "metrics:\n"
                "  gmv:\n"
                "    description: 有效订单成交金额\n"
                "    table: sparkmind_demo.fact_order\n"
                "    expression: SUM(total_amount)\n",
                encoding="utf-8",
            )
            store = CatalogStore(root / "catalog.json", ttl_seconds=300)
            store.save(_snapshot())
            service = CatalogService(_config(root), store, AsyncMock(), semantic_path=semantic_path)

            result = await service.get_catalog(CatalogQuery(database="sparkmind_demo", table="fact_order"))

            self.assertEqual(result["table"]["description"], "订单事实表")
            self.assertEqual(result["metrics"]["gmv"]["table"], "sparkmind_demo.fact_order")
            self.assertIn("order_id", [column["name"] for column in result["table"]["columns"]])

    async def test_registration_invalidates_and_refreshes_catalog(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            store = CatalogStore(root / "catalog.json", ttl_seconds=300)
            store.save(_snapshot())
            backend = AsyncMock()
            backend.register_dataset.return_value = {"status": "succeeded", "qualified_name": "analytics.sales"}
            backend.discover.return_value = _snapshot()
            service = CatalogService(_config(root), store, backend)

            result = await service.register_dataset(object())  # type: ignore[arg-type]

            self.assertEqual(result["qualified_name"], "analytics.sales")
            backend.register_dataset.assert_awaited_once()
            backend.discover.assert_awaited_once()

    async def test_unavailable_catalog_includes_refresh_error(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            store = CatalogStore(root / "catalog.json", ttl_seconds=300)
            backend = AsyncMock()
            backend.discover.side_effect = RuntimeError("docker socket denied")
            service = CatalogService(_config(root), store, backend)

            result = await service.get_catalog(CatalogQuery(refresh=True))

            self.assertEqual(result["status"], "unavailable")
            self.assertIn("docker socket denied", result["error"])
