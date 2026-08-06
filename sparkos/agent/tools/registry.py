"""通用工具定义：read_file / write_file / shell / web_fetch。"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path
from typing import Any

import httpx

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
    return f"未知工具: {name}"


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
