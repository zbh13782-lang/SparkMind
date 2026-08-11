"""统一配置入口：从 config/config.yaml 读取，暴露各层配置。"""

from __future__ import annotations

import os
from dataclasses import dataclass

import yaml


def _parse_bool(value: str) -> bool:
    v = value.strip().lower()
    if not v:
        return False
    if v in ("1", "true", "yes", "on"):
        return True
    if v in ("0", "false", "no", "off"):
        return False
    raise ValueError(f"无效的布尔值: {value}")


def _parse_int_env(name: str, fallback: int) -> int:
    raw = os.environ.get(name)
    if raw is None:
        return fallback
    try:
        value = int(raw)
    except ValueError:
        return fallback
    return value


@dataclass(frozen=True)
class ChatConfig:
    base_url: str
    api_key: str
    model: str


@dataclass(frozen=True)
class RuntimeConfig:
    max_tool_rounds: int
    max_replans: int
    max_steps: int
    context_window: int
    max_advisor_calls_per_step: int


@dataclass(frozen=True)
class AdvisorConfig:
    enabled: bool
    base_url: str
    api_key: str
    model: str
    timeout_seconds: int
    max_question_chars: int
    max_context_chars: int
    max_attempts_chars: int


def load(path: str | None = None) -> dict:
    """读取 YAML 文件，返回原始字典。"""
    path = path or os.path.join(os.path.dirname(__file__), "config.yaml")
    if os.path.exists(path):
        with open(path, encoding="utf-8") as f:
            return yaml.safe_load(f) or {}
    return {}


def get_chat_config() -> ChatConfig:
    """读取 LLM 服务配置。"""
    cfg = load()
    api = cfg.get("api", {})
    return ChatConfig(
        base_url=api["base_url"],
        api_key=api["api_key"],
        model=api["model"],
    )


def get_runtime_config() -> RuntimeConfig:
    """读取 Agent 运行时参数。"""
    cfg = load()
    rt = cfg.get("runtime", {})
    max_advisor = int(rt.get("max_advisor_calls_per_step", 1))
    if max_advisor < 1:
        raise ValueError("max_advisor_calls_per_step 必须为正整数")
    return RuntimeConfig(
        max_tool_rounds=rt["max_tool_rounds"],
        max_replans=rt["max_replans"],
        max_steps=rt["max_steps"],
        context_window=rt["context_window"],
        max_advisor_calls_per_step=max_advisor,
    )


def get_advisor_config(path: str | None = None) -> AdvisorConfig:
    """读取 Advisor 配置。"""
    cfg = load(path)
    adv = cfg.get("advisor", {})

    base_url = os.environ.get("SPARKMIND_ADVISOR_BASE_URL", adv.get("base_url", ""))
    api_key = os.environ.get("SPARKMIND_ADVISOR_API_KEY", adv.get("api_key", ""))
    model = os.environ.get("SPARKMIND_ADVISOR_MODEL", adv.get("model", "advisor"))
    timeout_seconds = _parse_int_env("SPARKMIND_ADVISOR_TIMEOUT_SECONDS", adv.get("timeout_seconds", 90))
    max_question_chars = _parse_int_env("SPARKMIND_ADVISOR_MAX_QUESTION_CHARS", adv.get("max_question_chars", 4000))
    max_context_chars = _parse_int_env("SPARKMIND_ADVISOR_MAX_CONTEXT_CHARS", adv.get("max_context_chars", 16000))
    max_attempts_chars = _parse_int_env("SPARKMIND_ADVISOR_MAX_ATTEMPTS_CHARS", adv.get("max_attempts_chars", 8000))

    enabled_raw = os.environ.get("SPARKMIND_ADVISOR_ENABLED")
    if enabled_raw is not None:
        enabled = _parse_bool(enabled_raw)
    else:
        enabled = bool(adv.get("enabled", False))

    if enabled and not model.strip():
        raise ValueError("advisor 已启用但 model 为空")

    if timeout_seconds < 1:
        raise ValueError("timeout_seconds 必须为正整数")

    for name, val in (
        ("max_question_chars", max_question_chars),
        ("max_context_chars", max_context_chars),
        ("max_attempts_chars", max_attempts_chars),
    ):
        if val < 1:
            raise ValueError(f"{name} 必须为正整数")

    return AdvisorConfig(
        enabled=enabled,
        base_url=base_url,
        api_key=api_key,
        model=model,
        timeout_seconds=timeout_seconds,
        max_question_chars=max_question_chars,
        max_context_chars=max_context_chars,
        max_attempts_chars=max_attempts_chars,
    )
