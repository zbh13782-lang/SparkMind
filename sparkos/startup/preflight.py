"""Startup health checks with UI-friendly progress callbacks."""

from __future__ import annotations

import io
from collections.abc import Callable
from dataclasses import dataclass

from rich.console import Console
from rich.table import Table

ProgressCallback = Callable[[str, str], None]


@dataclass(frozen=True)
class PreflightResult:
    passed: bool
    results: list[tuple[str, bool, str, bool]]


async def run_preflight(on_progress: ProgressCallback | None = None) -> PreflightResult:
    """Run startup checks and report each actual stage to the caller."""
    from config.config import get_chat_config
    from config.health import (
        CheckResult,
        _check_advisor_with_fallback,
        check_docker,
        check_llm,
        check_spark_hive,
    )

    def report(stage: str, detail: str) -> None:
        if on_progress is not None:
            on_progress(stage, detail)

    results: list[tuple[str, bool, str, bool]] = []

    report("llm", "正在连接模型服务")
    llm_result = await check_llm()
    results.append(("LLM", llm_result.ok, llm_result.detail, False))
    if not llm_result.ok:
        return PreflightResult(passed=False, results=results)

    chat_config = get_chat_config()

    report("docker", "正在检查 Docker")
    docker_result = await check_docker()
    results.append(("Docker", docker_result.ok, docker_result.detail, docker_result.degraded))

    if docker_result.ok:
        report("spark-hive", "正在刷新 Spark/Hive 目录")
        spark_hive_result = await check_spark_hive()
    else:
        spark_hive_result = CheckResult(
            ok=False,
            detail="Docker 不可用，跳过 Spark/Hive 检查",
            degraded=True,
        )
    results.append(("Spark/Hive", spark_hive_result.ok, spark_hive_result.detail, spark_hive_result.degraded))

    report("advisor", "正在检查 Advisor")
    advisor_result = await _check_advisor_with_fallback(chat_config)
    results.append(("Advisor", advisor_result.ok, advisor_result.detail, advisor_result.degraded))

    return PreflightResult(passed=True, results=results)


def render_preflight_report(result: PreflightResult) -> str:
    """Render a Rich report for callers that still want a terminal summary."""
    console = Console(stderr=True)
    table = Table(title="启动前检查", show_header=True, header_style="bold cyan")
    table.add_column("服务", style="cyan")
    table.add_column("状态", justify="center")
    table.add_column("详情")

    status_map = {
        (True, False): "[green]✓ 正常[/]",
        (False, False): "[red]✗ 失败[/]",
        (True, True): "[magenta]◆ 降级[/]",
        (False, True): "[magenta]◆ 降级[/]",
    }

    for name, ok, detail, degraded in result.results:
        status = status_map.get((ok, degraded), "[dim]?[/]")
        table.add_row(name, status, detail)

    buf = io.StringIO()
    console.file = buf
    console.print(table)
    return buf.getvalue()


__all__ = ["PreflightResult", "ProgressCallback", "render_preflight_report", "run_preflight"]
