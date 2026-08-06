"""通用工具定义：read_file / write_file / shell / web_fetch / spark 相关。"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path
from typing import Any

import httpx

from sparkos.infrastructure.spark.client import SparkDockerClient

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
            "name": "shell",
            "description": "执行 shell 命令并返回输出结果。",
            "parameters": {
                "type": "object",
                "properties": {
                    "command": {
                        "type": "string",
                        "description": "要执行的 shell 命令",
                    }
                },
                "required": ["command"],
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
            "name": "submit_spark_job",
            "description": "提交 PySpark 任务到 Spark 集群。返回 job_id 用于后续查询状态和日志。",
            "parameters": {
                "type": "object",
                "properties": {
                    "job_name": {
                        "type": "string",
                        "description": "任务名称，用于标识任务",
                    },
                    "code": {
                        "type": "string",
                        "description": "PySpark 代码内容",
                    },
                    "executor_memory": {
                        "type": "string",
                        "description": "Executor 内存，如 2g、4g",
                    },
                    "executor_cores": {
                        "type": "integer",
                        "description": "每个 Executor 的核数",
                    },
                    "num_executors": {
                        "type": "integer",
                        "description": "Executor 数量",
                    },
                    "driver_memory": {
                        "type": "string",
                        "description": "Driver 内存，如 2g、4g",
                    },
                },
                "required": ["job_name", "code"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_spark_job_status",
            "description": "查询 Spark 任务状态。传入 submit_spark_job 返回的 job_id。",
            "parameters": {
                "type": "object",
                "properties": {
                    "job_id": {
                        "type": "string",
                        "description": "任务 ID（application_xxx）",
                    }
                },
                "required": ["job_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_spark_job_logs",
            "description": "获取 Spark 任务的日志输出。传入 job_id。",
            "parameters": {
                "type": "object",
                "properties": {
                    "job_id": {
                        "type": "string",
                        "description": "任务 ID（application_xxx）",
                    }
                },
                "required": ["job_id"],
            },
        },
    },
]


def execute_tool(name: str, arguments: dict[str, Any]) -> str:
    """根据工具名称和参数执行对应工具，返回字符串结果。"""
    if name == "read_file":
        return _read_file(arguments["path"])
    if name == "write_file":
        return _write_file(arguments["path"], arguments["content"])
    if name == "shell":
        return _shell(arguments["command"])
    if name == "web_fetch":
        return _web_fetch(arguments["url"])
    if name == "submit_spark_job":
        return _submit_spark_job(arguments)
    if name == "get_spark_job_status":
        return _get_spark_job_status(arguments)
    if name == "get_spark_job_logs":
        return _get_spark_job_logs(arguments)
    return f"未知工具: {name}"


_SPARK_CLIENT = SparkDockerClient()


def _submit_spark_job(arguments: dict[str, Any]) -> str:
    result = _SPARK_CLIENT.submit(
        job_name=arguments["job_name"],
        code=arguments["code"],
        job_type=arguments.get("job_type", "pyspark"),
        executor_memory=arguments.get("executor_memory", "2g"),
        executor_cores=int(arguments.get("executor_cores", 2)),
        num_executors=int(arguments.get("num_executors", 2)),
        driver_memory=arguments.get("driver_memory", "2g"),
    )
    return (
        f"job_id={result.job_id}\n"
        f"status={result.status}\n"
        f"output:\n{result.output}"
    )


def _get_spark_job_status(arguments: dict[str, Any]) -> str:
    job_id = arguments["job_id"]
    result = _SPARK_CLIENT.get_status(job_id)
    return f"job_id={result.job_id}\nstatus={result.status}\noutput:\n{result.output}"


def _get_spark_job_logs(arguments: dict[str, Any]) -> str:
    job_id = arguments["job_id"]
    result = _SPARK_CLIENT.get_logs(job_id)
    return f"job_id={result.job_id}\nlogs:\n{result.output}"


def _read_file(path: str) -> str:
    """读取本地文件。"""
    p = Path(path)
    if not p.is_absolute():
        p = Path.cwd() / p
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
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(content, encoding="utf-8")
    return f"已写入文件: {path} ({len(content)} 字节)"


def _shell(command: str) -> str:
    """执行 shell 命令。"""
    try:
        result = subprocess.run(
            command,
            shell=True,
            capture_output=True,
            text=True,
            timeout=30,
            cwd=os.getcwd(),
            check=False,
        )
        output = result.stdout or result.stderr
        if not output:
            output = "(命令无输出)"
        return f"退出码: {result.returncode}\n{output}"
    except subprocess.TimeoutExpired:
        return "命令执行超时（30 秒）"
    except Exception as e:  # noqa: BLE001
        return f"执行错误: {e}"


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
