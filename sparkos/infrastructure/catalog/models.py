"""Validated contracts for data catalog discovery and dataset registration."""

from __future__ import annotations

import json
import re
from collections.abc import Mapping
from dataclasses import asdict, dataclass, field
from pathlib import Path
from types import MappingProxyType
from typing import Any, Literal

DataFormat = Literal["auto", "csv", "json", "jsonl", "parquet"]
IfExists = Literal["error", "overwrite"]

_IDENTIFIER = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_FORMATS = {"auto", "csv", "json", "jsonl", "parquet"}


def validate_identifier(value: str, field_name: str) -> str:
    value = value.strip()
    if not _IDENTIFIER.fullmatch(value):
        raise ValueError(f"{field_name} 必须是合法的 Spark SQL 标识符")
    return value


def _normalize_source(repo_root: Path, path: Path) -> tuple[Path, Path]:
    root = repo_root.resolve()
    source = path.resolve()
    if not source.is_relative_to(root):
        raise ValueError("数据源路径必须位于工作目录内")
    if not source.exists():
        raise ValueError(f"数据源不存在: {source}")
    return root, source


@dataclass(frozen=True)
class ColumnMetadata:
    name: str
    data_type: str
    nullable: bool
    is_partition: bool = False
    description: str = ""


@dataclass(frozen=True)
class TableMetadata:
    database: str
    name: str
    table_type: str
    provider: str
    location: str
    columns: tuple[ColumnMetadata, ...]
    description: str = ""

    @property
    def qualified_name(self) -> str:
        return f"{self.database}.{self.name}"


@dataclass(frozen=True)
class DatabaseMetadata:
    name: str
    tables: tuple[TableMetadata, ...]


@dataclass(frozen=True)
class CatalogSnapshot:
    generated_at: str
    databases: tuple[DatabaseMetadata, ...]
    warnings: tuple[str, ...] = ()

    def to_json(self) -> str:
        return json.dumps(asdict(self), ensure_ascii=False, sort_keys=True)

    @classmethod
    def from_json(cls, value: str) -> CatalogSnapshot:
        payload = json.loads(value)
        if not isinstance(payload, dict):
            raise TypeError("Catalog snapshot 必须是 JSON 对象")
        databases: list[DatabaseMetadata] = []
        for raw_database in payload.get("databases", []):
            tables: list[TableMetadata] = []
            for raw_table in raw_database.get("tables", []):
                columns = tuple(ColumnMetadata(**column) for column in raw_table.get("columns", []))
                tables.append(
                    TableMetadata(
                        database=raw_table["database"],
                        name=raw_table["name"],
                        table_type=raw_table.get("table_type", ""),
                        provider=raw_table.get("provider", ""),
                        location=raw_table.get("location", ""),
                        columns=columns,
                        description=raw_table.get("description", ""),
                    )
                )
            databases.append(DatabaseMetadata(name=raw_database["name"], tables=tuple(tables)))
        return cls(
            generated_at=str(payload["generated_at"]),
            databases=tuple(databases),
            warnings=tuple(str(item) for item in payload.get("warnings", [])),
        )


@dataclass(frozen=True)
class CatalogQuery:
    database: str | None = None
    table: str | None = None
    search: str | None = None
    refresh: bool = False

    def __post_init__(self) -> None:
        if self.database is not None:
            object.__setattr__(self, "database", validate_identifier(self.database, "database"))
        if self.table is not None:
            object.__setattr__(self, "table", validate_identifier(self.table, "table"))
        if self.table is not None and self.database is None:
            raise ValueError("指定 table 时必须同时指定 database")
        if self.search is not None:
            search = self.search.strip()
            object.__setattr__(self, "search", search or None)


@dataclass(frozen=True)
class DataSourceInspectRequest:
    repo_root: Path
    path: Path
    data_format: DataFormat = "auto"
    options: Mapping[str, str] = field(default_factory=dict)
    sample_rows: int = 5

    def __post_init__(self) -> None:
        root, source = _normalize_source(self.repo_root, self.path)
        if self.data_format not in _FORMATS:
            raise ValueError(f"不支持的数据格式: {self.data_format}")
        if not 1 <= self.sample_rows <= 20:
            raise ValueError("sample_rows 必须在 1 到 20 之间")
        object.__setattr__(self, "repo_root", root)
        object.__setattr__(self, "path", source)
        object.__setattr__(self, "options", MappingProxyType({str(k): str(v) for k, v in self.options.items()}))


@dataclass(frozen=True)
class DatasetRegistrationRequest:
    repo_root: Path
    path: Path
    data_format: DataFormat
    database: str
    table: str
    options: Mapping[str, str] = field(default_factory=dict)
    partition_columns: tuple[str, ...] = ()
    schema_ddl: str | None = None
    if_exists: IfExists = "error"

    def __post_init__(self) -> None:
        root, source = _normalize_source(self.repo_root, self.path)
        if self.data_format not in _FORMATS:
            raise ValueError(f"不支持的数据格式: {self.data_format}")
        if self.if_exists not in {"error", "overwrite"}:
            raise ValueError("if_exists 必须是 error 或 overwrite")
        database = validate_identifier(self.database, "database")
        table = validate_identifier(self.table, "table")
        partitions = tuple(validate_identifier(item, "partition column") for item in self.partition_columns)
        if len(partitions) != len(set(partitions)):
            raise ValueError("partition_columns 不能重复")
        object.__setattr__(self, "repo_root", root)
        object.__setattr__(self, "path", source)
        object.__setattr__(self, "database", database)
        object.__setattr__(self, "table", table)
        object.__setattr__(self, "partition_columns", partitions)
        object.__setattr__(self, "options", MappingProxyType({str(k): str(v) for k, v in self.options.items()}))
        if self.schema_ddl is not None:
            schema = self.schema_ddl.strip()
            object.__setattr__(self, "schema_ddl", schema or None)

    @property
    def qualified_name(self) -> str:
        return f"{self.database}.{self.table}"


def compact_json(value: Mapping[str, Any]) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), default=str)


__all__ = [
    "CatalogQuery",
    "CatalogSnapshot",
    "ColumnMetadata",
    "DataFormat",
    "DataSourceInspectRequest",
    "DatabaseMetadata",
    "DatasetRegistrationRequest",
    "TableMetadata",
    "compact_json",
    "validate_identifier",
]
