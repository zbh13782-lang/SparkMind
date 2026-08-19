"""Spark-backed catalog discovery and dataset operations."""

from __future__ import annotations

import json
import shutil
import textwrap
import uuid
from collections.abc import Callable
from pathlib import Path
from typing import Any, Protocol

from sparkos.infrastructure.catalog.models import (
    CatalogSnapshot,
    DatasetRegistrationRequest,
    DataSourceInspectRequest,
)
from sparkos.infrastructure.spark.client import SparkJobRunner
from sparkos.infrastructure.spark.models import SparkJobRequest, SparkJobResult


class CatalogDiscoveryError(RuntimeError):
    """Raised when Spark cannot produce a valid catalog snapshot."""


class CatalogOperationError(RuntimeError):
    """Raised when a source inspection or registration operation fails."""


class SparkRunner(Protocol):
    async def run(self, request: SparkJobRequest) -> SparkJobResult: ...


class SparkCatalogBackend:
    def __init__(self, repo_root: Path, runner: SparkRunner | None = None) -> None:
        self.repo_root = repo_root.resolve()
        self.runner = runner or SparkJobRunner()

    async def discover(self) -> CatalogSnapshot:
        payload = await self._run_artifact_job(
            job_name="sparkmind-catalog-discovery",
            code_factory=self._discovery_code,
            timeout_seconds=600,
        )
        try:
            return CatalogSnapshot.from_json(json.dumps(payload, ensure_ascii=False))
        except (KeyError, TypeError, ValueError) as exc:
            raise CatalogDiscoveryError(f"Catalog 产物格式无效: {exc}") from exc

    async def inspect_source(self, request: DataSourceInspectRequest) -> dict[str, Any]:
        data_format = self._resolve_format(request.path, request.data_format)
        payload = await self._run_artifact_job(
            job_name="sparkmind-inspect-source",
            code_factory=lambda output: self._inspect_code(request, data_format, output),
            timeout_seconds=600,
        )
        if payload.get("status", "succeeded") != "succeeded":
            raise CatalogOperationError(str(payload.get("error", "数据源检查失败")))
        payload["format"] = data_format
        return payload

    async def register_dataset(self, request: DatasetRegistrationRequest) -> dict[str, Any]:
        data_format = self._resolve_format(request.path, request.data_format)
        payload = await self._run_artifact_job(
            job_name=f"sparkmind-register-{request.table}",
            code_factory=lambda output: self._register_code(request, data_format, output),
            timeout_seconds=3600,
        )
        if payload.get("status", "succeeded") != "succeeded":
            if payload.get("reason") == "already_exists":
                raise CatalogOperationError(f"目标表已存在: {request.qualified_name}")
            raise CatalogOperationError(str(payload.get("error", "数据集注册失败")))
        return payload

    async def _run_artifact_job(
        self,
        *,
        job_name: str,
        code_factory: Callable[[str], str],
        timeout_seconds: int,
    ) -> dict:
        operation_id = uuid.uuid4().hex
        job_dir = self.repo_root / "artifacts/catalog/jobs" / operation_id
        artifact_path = job_dir / "snapshot.json"
        container_path = f"/opt/sparkos/{artifact_path.relative_to(self.repo_root).as_posix()}"
        code = code_factory(container_path)
        request = SparkJobRequest(
            job_name=job_name,
            job_type="pyspark",
            code=code,
            executor_memory="1g",
            executor_cores=1,
            num_executors=1,
            driver_memory="1g",
            timeout_seconds=timeout_seconds,
        )
        try:
            result = await self.runner.run(request)
            if result.status != "succeeded":
                raise CatalogDiscoveryError(f"Catalog Spark 作业状态为 {result.status}: {result.output[-2000:]}")
            if not artifact_path.is_file():
                raise CatalogDiscoveryError(f"Catalog 作业成功但缺少产物: {artifact_path}")
            try:
                payload = json.loads(artifact_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError) as exc:
                raise CatalogDiscoveryError(f"Catalog 产物读取失败: {artifact_path}: {exc}") from exc
            if not isinstance(payload, dict):
                raise CatalogDiscoveryError("Catalog 产物必须是 JSON 对象")
            return payload
        finally:
            shutil.rmtree(job_dir, ignore_errors=True)

    @staticmethod
    def _resolve_format(path: Path, data_format: str) -> str:
        if data_format != "auto":
            return "json" if data_format == "jsonl" else data_format
        suffixes = (
            {item.suffix.casefold() for item in path.rglob("*") if item.is_file()}
            if path.is_dir()
            else {path.suffix.casefold()}
        )
        if ".parquet" in suffixes:
            return "parquet"
        if path.is_dir() and any(item.name.startswith("part-") for item in path.iterdir()):
            return "parquet"
        if ".json" in suffixes or ".jsonl" in suffixes:
            return "json"
        if ".csv" in suffixes:
            return "csv"
        raise CatalogOperationError(f"无法从路径推断数据格式: {path}")

    @staticmethod
    def _reader_code(path: str, data_format: str, options: dict[str, str], schema_ddl: str | None = None) -> str:
        options_literal = repr(options)
        schema_line = f"reader = reader.schema({schema_ddl!r})\n" if schema_ddl else ""
        return (
            f"reader = spark.read.format({data_format!r}).options(**{options_literal})\n"
            f"{schema_line}"
            f"df = reader.load({path!r})\n"
        )

    @classmethod
    def _inspect_code(cls, request: DataSourceInspectRequest, data_format: str, output_path: str) -> str:
        source_path = f"/opt/sparkos/{request.path.relative_to(request.repo_root).as_posix()}"
        reader_code = textwrap.indent(cls._reader_code(source_path, data_format, dict(request.options)), "    ")
        return f"""from __future__ import annotations

import json
from pathlib import Path
from pyspark.sql import SparkSession

output_path = {output_path!r}
spark = SparkSession.builder.appName("sparkmind-inspect-source").enableHiveSupport().getOrCreate()
payload = {{"status": "succeeded", "format": {data_format!r}, "sample_rows": [], "columns": []}}
try:
{reader_code}
    payload["schema_json"] = df.schema.json()
    payload["columns"] = [
        {{"name": field.name, "data_type": field.dataType.simpleString(), "nullable": field.nullable}}
        for field in df.schema.fields
    ]
    payload["sample_rows"] = [json.loads(row) for row in df.limit({request.sample_rows}).toJSON().collect()]
    payload["source_path"] = {source_path!r}
except Exception as exc:
    payload = {{"status": "failed", "error": f"{{type(exc).__name__}}: {{exc}}"}}
finally:
    Path(output_path).parent.mkdir(parents=True, exist_ok=True)
    Path(output_path).write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    spark.stop()
"""

    @classmethod
    def _register_code(cls, request: DatasetRegistrationRequest, data_format: str, output_path: str) -> str:
        source_path = f"/opt/sparkos/{request.path.relative_to(request.repo_root).as_posix()}"
        partitions = repr(list(request.partition_columns))
        mode = "overwrite" if request.if_exists == "overwrite" else "errorifexists"
        reader_code = textwrap.indent(
            cls._reader_code(source_path, data_format, dict(request.options), request.schema_ddl),
            "        ",
        )
        return f"""from __future__ import annotations

import json
from pathlib import Path
from pyspark.sql import SparkSession

output_path = {output_path!r}
spark = SparkSession.builder.appName({f"sparkmind-register-{request.table}"!r}).enableHiveSupport().getOrCreate()
payload = {{"status": "succeeded", "qualified_name": {request.qualified_name!r}, "storage_format": "parquet", "partition_columns": {partitions}}}
try:
    if spark.catalog.tableExists({request.qualified_name!r}) and {request.if_exists!r} == "error":
        payload = {{"status": "failed", "reason": "already_exists", "error": "目标表已存在"}}
    else:
{reader_code}
        missing = [column for column in {partitions} if column not in df.columns]
        if missing:
            raise ValueError(f"分区字段不存在: {{missing}}")
        spark.sql("CREATE DATABASE IF NOT EXISTS `{request.database}`")
        writer = df.write.mode({mode!r}).format("parquet")
        if {partitions}:
            writer = writer.partitionBy(*{partitions})
        writer.saveAsTable({request.qualified_name!r})
        payload["columns"] = [field.name for field in df.schema.fields]
except Exception as exc:
    payload = {{"status": "failed", "error": f"{{type(exc).__name__}}: {{exc}}"}}
finally:
    Path(output_path).parent.mkdir(parents=True, exist_ok=True)
    Path(output_path).write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    spark.stop()
"""

    @staticmethod
    def _discovery_code(output_path: str) -> str:
        return f"""from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from pyspark.sql import SparkSession

output_path = {output_path!r}
spark = SparkSession.builder.appName("sparkmind-catalog-discovery").enableHiveSupport().getOrCreate()
payload = {{"generated_at": datetime.now(timezone.utc).isoformat(), "databases": [], "warnings": []}}
try:
    for database in spark.catalog.listDatabases():
        raw_database = {{"name": database.name, "tables": []}}
        for table in spark.catalog.listTables(database.name):
            if table.isTemporary:
                continue
            qualified = f"`{{database.name}}`.`{{table.name}}`"
            details = spark.sql(f"DESCRIBE TABLE EXTENDED {{qualified}}").collect()
            detail_map = {{
                str(row.col_name).strip(): str(row.data_type).strip()
                for row in details
                if row.col_name and str(row.col_name).strip()
            }}
            columns = []
            for column in spark.catalog.listColumns(table.name, database.name):
                columns.append({{
                    "name": column.name,
                    "data_type": column.dataType,
                    "nullable": bool(column.nullable),
                    "is_partition": bool(column.isPartition),
                    "description": column.description or "",
                }})
            raw_database["tables"].append({{
                "database": database.name,
                "name": table.name,
                "table_type": table.tableType or "",
                "provider": detail_map.get("Provider", ""),
                "location": detail_map.get("Location", ""),
                "columns": columns,
                "description": getattr(table, "description", None) or "",
            }})
        raw_database["tables"].sort(key=lambda item: item["name"])
        payload["databases"].append(raw_database)
    payload["databases"].sort(key=lambda item: item["name"])
    path = Path(output_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
finally:
    spark.stop()
"""


__all__ = ["CatalogDiscoveryError", "CatalogOperationError", "SparkCatalogBackend"]
