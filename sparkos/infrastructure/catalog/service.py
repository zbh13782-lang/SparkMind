"""Catalog cache, filtering and bounded Agent-facing responses."""

from __future__ import annotations

import asyncio
import logging
from functools import lru_cache
from pathlib import Path
from typing import Any, Protocol

import yaml

from config.config import CatalogConfig
from sparkos.infrastructure.catalog.models import (
    CatalogQuery,
    CatalogSnapshot,
    DatasetRegistrationRequest,
    DataSourceInspectRequest,
    TableMetadata,
)
from sparkos.infrastructure.catalog.store import CatalogStore

logger = logging.getLogger(__name__)


class CatalogBackend(Protocol):
    async def discover(self) -> CatalogSnapshot: ...
    async def inspect_source(self, request: DataSourceInspectRequest) -> dict[str, Any]: ...
    async def register_dataset(self, request: DatasetRegistrationRequest) -> dict[str, Any]: ...


class CatalogService:
    @classmethod
    def from_config(cls) -> CatalogService:
        from config.config import get_catalog_config
        from sparkos.infrastructure.catalog.spark_backend import SparkCatalogBackend
        from sparkos.infrastructure.spark.client import SparkJobRunner

        config = get_catalog_config()
        runner = SparkJobRunner()
        repo_root = runner.config.repo_root
        cache_path = Path(config.cache_path)
        if not cache_path.is_absolute():
            cache_path = repo_root / cache_path
        semantic_path = Path(config.semantic_path)
        if not semantic_path.is_absolute():
            semantic_path = repo_root / semantic_path
        return cls(
            config=config,
            store=CatalogStore(cache_path, ttl_seconds=config.cache_ttl_seconds),
            backend=SparkCatalogBackend(repo_root=repo_root, runner=runner),
            semantic_path=semantic_path,
        )

    def __init__(
        self,
        config: CatalogConfig,
        store: CatalogStore,
        backend: CatalogBackend,
        semantic_path: Path | None = None,
    ) -> None:
        self.config = config
        self.store = store
        self.backend = backend
        self.semantic_path = semantic_path
        self._refresh_lock = asyncio.Lock()
        self._last_refresh_error = ""

    async def get_catalog(self, query: CatalogQuery) -> dict[str, Any]:
        if not self.config.enabled:
            return {"status": "disabled", "tables": []}
        snapshot, stale = await self._get_snapshot(query.refresh)
        if snapshot is None:
            return {
                "status": "unavailable",
                "default_database": self.config.default_database,
                "tables": [],
                "stale": False,
                "error": self._last_refresh_error or "Catalog 刷新未返回可用快照",
            }
        result = self._render(snapshot, query)
        result.setdefault("status", "succeeded")
        result["stale"] = stale
        if stale and self._last_refresh_error:
            result["refresh_error"] = self._last_refresh_error
        return result

    async def inspect_source(self, request: DataSourceInspectRequest) -> dict[str, Any]:
        return await self.backend.inspect_source(request)

    async def register_dataset(self, request: DatasetRegistrationRequest) -> dict[str, Any]:
        result = await self.backend.register_dataset(request)
        try:
            refreshed = await self.backend.discover()
        except Exception as exc:  # noqa: BLE001
            logger.warning("register_dataset 后 discover 失败，保留旧缓存: %s", exc)
            return result
        self.store.save(refreshed)
        return result

    def cached_summary(self) -> dict[str, Any]:
        snapshot = self.store.load()
        if snapshot is None:
            return {"default_database": self.config.default_database, "tables": [], "stale": True}
        database = next(
            (item for item in snapshot.databases if item.name == self.config.default_database),
            None,
        )
        return {
            "default_database": self.config.default_database,
            "tables": [table.name for table in database.tables] if database else [],
            "metrics": sorted(self._load_semantics().get("metrics", {})),
            "semantic_tables": sorted(self._load_semantics().get("tables", {})),
            "stale": not self.store.is_fresh(),
        }

    def invalidate(self) -> None:
        self.store.path.unlink(missing_ok=True)

    async def _get_snapshot(self, force_refresh: bool) -> tuple[CatalogSnapshot | None, bool]:
        cached = self.store.load()
        if cached is not None and self.store.is_fresh() and not force_refresh:
            return cached, False
        async with self._refresh_lock:
            cached = self.store.load()
            if cached is not None and self.store.is_fresh() and not force_refresh:
                return cached, False
            try:
                refreshed = await self.backend.discover()
            except Exception as exc:  # noqa: BLE001
                logger.warning("目录刷新失败，使用已有缓存: %s", exc)
                self._last_refresh_error = f"{type(exc).__name__}: {exc}"
                if cached is not None:
                    return cached, True
                return None, False
            self._last_refresh_error = ""
            self.store.save(refreshed)
            return refreshed, False

    def _render(self, snapshot: CatalogSnapshot, query: CatalogQuery) -> dict[str, Any]:
        semantics = self._load_semantics()
        effective_database = query.database or self.config.default_database
        tables = [
            table for database in snapshot.databases if database.name == effective_database for table in database.tables
        ]
        if query.search:
            needle = query.search.casefold()
            metric_targets = {
                str(metric.get("table", ""))
                for name, metric in semantics.get("metrics", {}).items()
                if needle in f"{name} {metric.get('description', '')}".casefold()
            }
            tables = [
                table
                for table in tables
                if self._matches(table, needle, semantics) or table.qualified_name in metric_targets
            ]
        if query.table:
            table = next((item for item in tables if item.name == query.table), None)
            if table is None:
                result = {
                    "default_database": self.config.default_database,
                    "table": None,
                    "available_tables": [item.name for item in tables[: self.config.max_tables_per_response]],
                    "status": "not_found",
                }
            else:
                result = {
                    "default_database": self.config.default_database,
                    "table": self._table_detail(table, semantics),
                }
        else:
            result = {
                "default_database": self.config.default_database,
                "database": effective_database,
                "tables": [
                    self._table_summary(table, semantics) for table in tables[: self.config.max_tables_per_response]
                ],
            }
        result["metrics"] = self._metrics_for(semantics, effective_database, query.table)
        result["joins"] = self._joins_for(semantics, effective_database, query.table)
        result["semantic_warnings"] = self._semantic_warnings(snapshot, semantics)
        return result

    @classmethod
    def _matches(cls, table: TableMetadata, needle: str, semantics: dict[str, Any]) -> bool:
        description = cls._semantic_table(semantics, table).get("description", table.description)
        haystack = " ".join(
            [table.qualified_name, str(description), *(column.name for column in table.columns)]
        ).casefold()
        return needle in haystack

    @classmethod
    def _table_summary(cls, table: TableMetadata, semantics: dict[str, Any]) -> dict[str, Any]:
        semantic = cls._semantic_table(semantics, table)
        return {
            "qualified_name": table.qualified_name,
            "name": table.name,
            "description": semantic.get("description", table.description),
            "table_type": table.table_type,
            "provider": table.provider,
            "partition_columns": [column.name for column in table.columns if column.is_partition],
            "time_column": semantic.get("time_column", ""),
        }

    def _table_detail(self, table: TableMetadata, semantics: dict[str, Any]) -> dict[str, Any]:
        result = self._table_summary(table, semantics)
        result["location"] = table.location
        result["columns"] = [
            {
                "name": column.name,
                "data_type": column.data_type,
                "nullable": column.nullable,
                "is_partition": column.is_partition,
                "description": column.description,
            }
            for column in table.columns[: self.config.max_columns_per_response]
        ]
        result["columns_truncated"] = len(table.columns) > self.config.max_columns_per_response
        return result

    @lru_cache(maxsize=1)
    def _load_semantics(self) -> dict[str, Any]:
        if self.semantic_path is None or not self.semantic_path.is_file():
            return {}
        try:
            payload = yaml.safe_load(self.semantic_path.read_text(encoding="utf-8")) or {}
        except (OSError, yaml.YAMLError):
            return {}
        return payload if isinstance(payload, dict) else {}

    @staticmethod
    def _semantic_table(semantics: dict[str, Any], table: TableMetadata) -> dict[str, Any]:
        value = semantics.get("tables", {}).get(table.qualified_name, {})
        return value if isinstance(value, dict) else {}

    @staticmethod
    def _metrics_for(semantics: dict[str, Any], database: str, table: str | None) -> dict[str, Any]:
        target = f"{database}.{table}" if table else None
        return {
            name: metric
            for name, metric in semantics.get("metrics", {}).items()
            if isinstance(metric, dict)
            and str(metric.get("table", "")).startswith(f"{database}.")
            and (target is None or metric.get("table") == target)
        }

    @staticmethod
    def _joins_for(semantics: dict[str, Any], database: str, table: str | None) -> list[dict[str, Any]]:
        prefix = f"{database}.{table}." if table else f"{database}."
        return [
            join
            for join in semantics.get("joins", [])
            if isinstance(join, dict)
            and (str(join.get("left", "")).startswith(prefix) or str(join.get("right", "")).startswith(prefix))
        ]

    @staticmethod
    def _semantic_warnings(snapshot: CatalogSnapshot, semantics: dict[str, Any]) -> list[str]:
        known = {table.qualified_name for database in snapshot.databases for table in database.tables}
        return [f"语义 Catalog 引用了不存在的表: {name}" for name in semantics.get("tables", {}) if name not in known]


__all__ = ["CatalogService"]
