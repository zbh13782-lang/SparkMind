"""通用工具定义：read_file / write_file / web_fetch / run_spark_job / run_code。"""

from __future__ import annotations

import json
from collections.abc import Awaitable
from pathlib import Path
from typing import Any

import httpx

from config.config import get_catalog_config
from sparkos.infrastructure.advisor.service import AdvisorService
from sparkos.infrastructure.catalog.models import (
    CatalogQuery,
    DatasetRegistrationRequest,
    DataSourceInspectRequest,
)
from sparkos.infrastructure.catalog.service import CatalogService
from sparkos.infrastructure.catalog.spark_backend import SparkCatalogBackend
from sparkos.infrastructure.catalog.store import CatalogStore
from sparkos.infrastructure.code_sandbox.models import CodeRunRequest
from sparkos.infrastructure.code_sandbox.runner import CodeSandboxRunner
from sparkos.infrastructure.spark.client import SparkJobRunner
from sparkos.infrastructure.spark.models import SparkJobRequest

TOOL_DEFINITIONS: list[dict[str, Any]] = [
    {
        "type": "function",
        "function": {
            "name": "read_file",
            "description": "读取本地文件的内容。返回文件的完整文本内容。",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "要读取的文件路径（相对或绝对路径）",
                    }
                },
                "required": ["path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "write_file",
            "description": "将内容写入本地文件。如果文件已存在则覆盖。",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "要写入的文件路径",
                    },
                    "content": {
                        "type": "string",
                        "description": "要写入的文件内容",
                    },
                },
                "required": ["path", "content"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "web_fetch",
            "description": "获取指定 URL 的网页内容，返回文本。",
            "parameters": {
                "type": "object",
                "properties": {
                    "url": {
                        "type": "string",
                        "description": "要获取的网页 URL",
                    }
                },
                "required": ["url"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "run_spark_job",
            "description": (
                "在本地 Docker Spark 集群同步执行一条 Spark SQL 或一个 PySpark 作业，并返回状态、日志末尾和作业信息。"
            ),
            "parameters": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "job_name": {
                        "type": "string",
                        "description": "1 到 80 个字符的作业名称",
                    },
                    "job_type": {
                        "type": "string",
                        "enum": ["spark_sql", "pyspark"],
                    },
                    "code": {
                        "type": "string",
                        "description": "单条 Spark SQL 或完整 PySpark 脚本",
                    },
                    "executor_memory": {"type": "string", "default": "1g"},
                    "executor_cores": {"type": "integer", "default": 1},
                    "num_executors": {"type": "integer", "default": 1},
                    "driver_memory": {"type": "string", "default": "1g"},
                    "timeout_seconds": {
                        "type": "integer",
                        "minimum": 1,
                        "maximum": 3600,
                        "default": 600,
                    },
                },
                "required": ["job_name", "job_type", "code"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_data_catalog",
            "description": "查询当前 Spark Hive Catalog。问数前用它发现数据库、表、字段和分区；已有 Catalog 时不要先要求用户提供表结构。",
            "parameters": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "database": {"type": "string"},
                    "table": {"type": "string"},
                    "search": {"type": "string"},
                    "refresh": {"type": "boolean", "default": False},
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "inspect_data_source",
            "description": "检查 CSV、JSON/JSONL 或 Parquet 数据源的格式、字段、嵌套结构和有限样例；注册前先调用。",
            "parameters": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "path": {"type": "string"},
                    "format": {"type": "string", "enum": ["auto", "csv", "json", "jsonl", "parquet"]},
                    "options": {"type": "object", "additionalProperties": {"type": "string"}},
                    "sample_rows": {"type": "integer", "minimum": 1, "maximum": 20, "default": 5},
                },
                "required": ["path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "register_dataset",
            "description": "将经过 inspect_data_source 检查的数据注册为 Hive-managed Parquet 表；默认不覆盖已有表。",
            "parameters": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "path": {"type": "string"},
                    "format": {"type": "string", "enum": ["auto", "csv", "json", "jsonl", "parquet"]},
                    "database": {"type": "string"},
                    "table": {"type": "string"},
                    "options": {"type": "object", "additionalProperties": {"type": "string"}},
                    "partition_columns": {"type": "array", "items": {"type": "string"}},
                    "schema_ddl": {"type": "string"},
                    "if_exists": {"type": "string", "enum": ["error", "overwrite"], "default": "error"},
                },
                "required": ["path", "format", "database", "table"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "run_code",
            "description": (
                "在无网络、无宿主工作区访问的一次性 Docker 沙箱中运行 Python 或 Bash，"
                "返回退出码、限长输出和完整日志路径。"
            ),
            "parameters": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "language": {"type": "string", "enum": ["python", "bash"]},
                    "code": {"type": "string", "description": "要运行的完整代码"},
                    "stdin": {"type": "string", "default": ""},
                    "timeout_seconds": {
                        "type": "integer",
                        "minimum": 1,
                        "maximum": 30,
                        "default": 10,
                    },
                },
                "required": ["language", "code"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "ask_advisor",
            "description": (
                "当当前步骤经过一次具体尝试后仍存在关键技术难题或方案分歧时，"
                "向独立高级模型请求一次建议；建议必须再用当前工具或证据验证。"
            ),
            "parameters": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "question": {"type": "string", "description": "需要决策的具体问题"},
                    "context": {
                        "type": "string",
                        "description": "最小必要上下文和约束",
                    },
                    "attempts": {
                        "type": "string",
                        "description": "已经尝试的方法及结果",
                    },
                },
                "required": ["question", "context", "attempts"],
            },
        },
    },
]


_SPARK_RUNNER = SparkJobRunner()
_CODE_RUNNER = CodeSandboxRunner()
_ADVISOR: AdvisorService | None = None
_CATALOG_SERVICE: CatalogService | None = None


def _get_advisor() -> AdvisorService:
    global _ADVISOR
    if _ADVISOR is None:
        _ADVISOR = AdvisorService.from_config()
    return _ADVISOR


def _get_catalog_service() -> CatalogService:
    global _CATALOG_SERVICE
    if _CATALOG_SERVICE is None:
        config = get_catalog_config()
        repo_root = _SPARK_RUNNER.config.repo_root
        cache_path = Path(config.cache_path)
        if not cache_path.is_absolute():
            cache_path = repo_root / cache_path
        semantic_path = Path(config.semantic_path)
        if not semantic_path.is_absolute():
            semantic_path = repo_root / semantic_path
        _CATALOG_SERVICE = CatalogService(
            config=config,
            store=CatalogStore(cache_path, ttl_seconds=config.cache_ttl_seconds),
            backend=SparkCatalogBackend(repo_root=repo_root, runner=_SPARK_RUNNER),
            semantic_path=semantic_path,
        )
    return _CATALOG_SERVICE


def set_catalog_service(service: CatalogService) -> None:
    """让 runtime 注入已创建的实例，避免双实例缓存断开。"""
    global _CATALOG_SERVICE
    _CATALOG_SERVICE = service


def execute_tool(
    name: str,
    arguments: dict[str, Any],
) -> str | Awaitable[str]:
    """根据工具名称和参数执行对应工具，返回字符串结果。"""
    if name == "read_file":
        return _read_file(arguments["path"])
    if name == "write_file":
        return _write_file(arguments["path"], arguments["content"])
    if name == "web_fetch":
        return _web_fetch(arguments["url"])
    if name == "run_spark_job":
        return _run_spark_job(arguments)
    if name == "get_data_catalog":
        return _get_data_catalog(arguments)
    if name == "inspect_data_source":
        return _inspect_data_source(arguments)
    if name == "register_dataset":
        return _register_dataset(arguments)
    if name == "run_code":
        return _run_code(arguments)
    if name == "ask_advisor":
        return _ask_advisor(arguments)
    return f"未知工具: {name}"


async def _run_spark_job(arguments: dict[str, Any]) -> str:
    request = SparkJobRequest(
        job_name=arguments["job_name"],
        job_type=arguments["job_type"],
        code=arguments["code"],
        executor_memory=arguments.get("executor_memory", "1g"),
        executor_cores=int(arguments.get("executor_cores", 1)),
        num_executors=int(arguments.get("num_executors", 1)),
        driver_memory=arguments.get("driver_memory", "1g"),
        timeout_seconds=int(arguments.get("timeout_seconds", 600)),
    )
    result = await _SPARK_RUNNER.run(request)
    return result.to_json()


async def _get_data_catalog(arguments: dict[str, Any]) -> str:
    result = await _get_catalog_service().get_catalog(
        CatalogQuery(
            database=arguments.get("database"),
            table=arguments.get("table"),
            search=arguments.get("search"),
            refresh=arguments.get("refresh") in (True, "true", "1", "yes"),
        )
    )
    return json.dumps(result, ensure_ascii=False)


async def _inspect_data_source(arguments: dict[str, Any]) -> str:
    result = await _get_catalog_service().inspect_source(
        DataSourceInspectRequest(
            repo_root=_SPARK_RUNNER.config.repo_root,
            path=Path(arguments["path"]),
            data_format=arguments.get("format") or "auto",
            options=arguments.get("options") or {},
            sample_rows=int(arguments.get("sample_rows", 5)),
        )
    )
    return json.dumps(result, ensure_ascii=False)


async def _register_dataset(arguments: dict[str, Any]) -> str:
    result = await _get_catalog_service().register_dataset(
        DatasetRegistrationRequest(
            repo_root=_SPARK_RUNNER.config.repo_root,
            path=Path(arguments["path"]),
            data_format=arguments["format"],
            database=arguments["database"],
            table=arguments["table"],
            options=arguments.get("options") or {},
            partition_columns=tuple(arguments.get("partition_columns") or []),
            schema_ddl=arguments.get("schema_ddl"),
            if_exists=arguments.get("if_exists") or "error",
        )
    )
    return json.dumps(result, ensure_ascii=False)


async def _run_code(arguments: dict[str, Any]) -> str:
    request = CodeRunRequest(
        language=arguments["language"],
        code=arguments["code"],
        stdin=arguments.get("stdin", ""),
        timeout_seconds=int(arguments.get("timeout_seconds", 10)),
    )
    result = await _CODE_RUNNER.run(request)
    return result.to_json()


async def _ask_advisor(arguments: dict[str, Any]) -> str:
    from sparkos.infrastructure.advisor.models import AdvisorRequest

    request = AdvisorRequest(
        question=arguments["question"],
        context=arguments["context"],
        attempts=arguments["attempts"],
    )
    result = await _get_advisor().ask(request)
    return result.to_json()


def _read_file(path: str) -> str:
    """读取本地文件。"""
    p = Path(path)
    if not p.is_absolute():
        p = Path.cwd() / p
    try:
        p = p.resolve()
    except OSError:
        return f"无法解析文件路径: {path}"
    if not p.is_relative_to(Path.cwd().resolve()):
        return f"文件路径超出工作目录: {path}"
    if not p.exists():
        return f"文件不存在: {path}"
    try:
        content = p.read_text(encoding="utf-8")
        return f"文件内容 ({path}, {len(content)} 字节):\n{content}"
    except UnicodeDecodeError:
        return f"无法读取文件（非文本文件）: {path}"


def _write_file(path: str, content: str) -> str:
    """写入本地文件。"""
    p = Path(path)
    if not p.is_absolute():
        p = Path.cwd() / p
    try:
        p = p.resolve()
    except OSError:
        return f"无法解析文件路径: {path}"
    if not p.is_relative_to(Path.cwd().resolve()):
        return f"文件路径超出工作目录: {path}"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(content, encoding="utf-8")
    return f"已写入文件: {path} ({len(content)} 字节)"


def _web_fetch(url: str) -> str:
    """获取网页内容。"""
    try:
        with httpx.Client(timeout=15, follow_redirects=True) as client:
            resp = client.get(url)
            resp.raise_for_status()
            text = resp.text
            return f"网页内容 ({url}, {len(text)} 字符):\n{text[:5000]}"
    except Exception as e:  # noqa: BLE001
        return f"获取失败: {e}"


__all__ = [
    "TOOL_DEFINITIONS",
    "execute_tool",
]
