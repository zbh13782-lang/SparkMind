"""启动前健康检查：LLM（硬）、Docker（软）、Advisor（软+自动降级）。"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import yaml

from config.config import (
    ChatConfig,
    get_advisor_config,
    get_chat_config,
    load,
)


@dataclass(frozen=True)
class CheckResult:
    ok: bool
    detail: str
    degraded: bool = False


def _degrade_advisor(chat_config: ChatConfig) -> None:
    """将 advisor 降级写入 config.yaml：enabled=false，复用主 LLM 模型。"""
    cfg_path = Path(__file__).resolve().parent.parent / "config" / "config.yaml"
    if cfg_path.exists():
        cfg = load(str(cfg_path))
        adv = cfg.setdefault("advisor", {})
        adv["enabled"] = False
        adv["base_url"] = chat_config.base_url
        adv["api_key"] = chat_config.api_key
        adv["model"] = chat_config.model

        cfg_path.write_text(
            yaml.safe_dump(cfg, allow_unicode=True, sort_keys=False),
            encoding="utf-8",
        )


def get_available_tool_names(
    docker_available: bool,
    advisor_enabled: bool,
) -> set[str]:
    """根据检查结果返回不可用的工具名称集合。"""
    unavailable: set[str] = set()
    if not docker_available:
        unavailable.update({"run_spark_job", "run_code"})
    if not advisor_enabled:
        unavailable.add("ask_advisor")
    return unavailable


async def check_llm() -> CheckResult:
    """验证主 LLM 可达且 API key 有效。不可降级，失败硬退出。"""
    from sparkos.infrastructure.llm.client import OpenAIChatClient

    try:
        config = get_chat_config()
    except (KeyError, TypeError) as exc:
        return CheckResult(ok=False, detail=f"LLM 配置缺失: {exc}")

    if not all([config.base_url, config.api_key, config.model]):
        return CheckResult(ok=False, detail="LLM base_url/api_key/model 不能为空")

    client = OpenAIChatClient(config)
    try:
        reply = await client.chat_once(
            [{"role": "user", "content": "hi"}],
            json_object=False,
        )
    except Exception as exc:
        return CheckResult(ok=False, detail=f"LLM 不可达: {exc}")

    if not reply.strip():
        return CheckResult(ok=False, detail="LLM 返回空响应，API key 可能无效")

    return CheckResult(ok=True, detail=f"LLM 正常 [{config.model}]")


async def check_docker() -> CheckResult:
    """验证 Docker 可用。Spark 任务依赖，不可用则跳过降级提示。"""
    try:
        import shutil

        if not shutil.which("docker"):
            return CheckResult(ok=False, detail="docker 命令未找到", degraded=True)
        import subprocess

        result = subprocess.run(
            ["docker", "info"],
            capture_output=True,
            timeout=5,
        )
        if result.returncode != 0:
            return CheckResult(
                ok=False,
                detail=f"docker 不可用: {result.stderr.decode()[:120]}",
                degraded=True,
            )
    except FileNotFoundError:
        return CheckResult(ok=False, detail="docker 命令未找到", degraded=True)
    except subprocess.TimeoutExpired:
        return CheckResult(ok=False, detail="docker 响应超时", degraded=True)
    except Exception as exc:
        return CheckResult(ok=False, detail=f"docker 检查异常: {exc}", degraded=True)
    return CheckResult(ok=True, detail="docker 正常")


async def check_advisor() -> CheckResult:
    """验证 Advisor 可用。失败自动降级写入 config.yaml 并返回降级状态。"""
    try:
        config = get_advisor_config()
    except (KeyError, TypeError) as exc:
        return CheckResult(
            ok=False,
            detail=f"Advisor 配置缺失: {exc}",
            degraded=True,
        )

    if not config.enabled:
        return CheckResult(ok=True, detail=f"Advisor 已禁用 [{config.model}]")

    if not all([config.base_url, config.api_key, config.model]):
        return CheckResult(ok=False, detail="Advisor base_url/api_key/model 不能为空", degraded=True)

    from sparkos.infrastructure.llm.client import OpenAIChatClient

    client = OpenAIChatClient(
        ChatConfig(
            base_url=config.base_url,
            api_key=config.api_key,
            model=config.model,
        )
    )
    try:
        reply = await client.chat_once(
            [{"role": "user", "content": "hi"}],
            json_object=False,
        )
    except Exception as exc:
        return CheckResult(
            ok=False,
            detail=f"Advisor 不可达 ({config.model}): {exc}",
            degraded=True,
        )

    if not reply.strip():
        return CheckResult(
            ok=False,
            detail=f"Advisor 返回空响应，API key 可能无效 [{config.model}]",
            degraded=True,
        )

    return CheckResult(ok=True, detail=f"Advisor 正常 [{config.model}]")


async def _check_advisor_with_fallback(chat_config: ChatConfig) -> CheckResult:
    """检查 Advisor，失败则自动降级写入 config.yaml 并返回降级后的结果。"""
    result = await check_advisor()
    if result.ok:
        return result

    _degrade_advisor(chat_config)
    return CheckResult(
        ok=False,
        detail=f"Advisor 已降级为禁用（复用主 LLM [{chat_config.model}]）",
        degraded=True,
    )
