"""SparkMind CLI — Textual 交互界面。"""

from __future__ import annotations

import asyncio
import sys
import time
from dataclasses import dataclass

from rich.console import Console
from rich.table import Table

from sparkos.ui.chat_app import ChatApp


@dataclass(frozen=True)
class PreflightResult:
    passed: bool
    results: list[tuple[str, bool, str, bool]]


async def run_preflight() -> PreflightResult:
    """执行启动前检查。LLM 失败硬退出；Docker/Advisor 降级继续。"""
    from config.config import get_chat_config
    from config.health import CheckResult, _check_advisor_with_fallback, check_docker, check_llm, check_spark_hive

    results: list[tuple[str, bool, str, bool]] = []

    llm_result = await check_llm()
    results.append(("LLM", llm_result.ok, llm_result.detail, False))
    if not llm_result.ok:
        return PreflightResult(passed=False, results=results)

    chat_config = get_chat_config()

    docker_result = await check_docker()
    results.append(("Docker", docker_result.ok, docker_result.detail, docker_result.degraded))

    if docker_result.ok:
        spark_hive_result = await check_spark_hive()
    else:
        spark_hive_result = CheckResult(
            ok=False,
            detail="Docker 不可用，跳过 Spark/Hive 检查",
            degraded=True,
        )
    results.append(("Spark/Hive", spark_hive_result.ok, spark_hive_result.detail, spark_hive_result.degraded))

    advisor_result = await _check_advisor_with_fallback(chat_config)
    results.append(("Advisor", advisor_result.ok, advisor_result.detail, advisor_result.degraded))

    return PreflightResult(passed=True, results=results)


def render_preflight_report(result: PreflightResult) -> str:
    """在控制台打印检查结果表。"""
    console = Console(stderr=True)
    table = Table(title="启动前检查", show_header=True, header_style="bold cyan")
    table.add_column("服务", style="cyan")
    table.add_column("状态", justify="center")
    table.add_column("详情")

    status_map = {
        (True, False): "[green]✓ 正常[/]",
        (False, False): "[red]✗ 失败[/]",
        (True, True): "[yellow]⚠ 降级[/]",
        (False, True): "[yellow]⚠ 降级[/]",
    }

    for name, ok, detail, degraded in result.results:
        status = status_map.get((ok, degraded), "[dim]?[/]")
        table.add_row(name, status, detail)

    import io

    buf = io.StringIO()
    console.file = buf
    console.print(table)
    return buf.getvalue()


def main() -> None:
    """启动入口：先跑健康检查，再启动 TUI。"""
    preflight = asyncio.run(run_preflight())
    report = render_preflight_report(preflight)

    if not preflight.passed:
        sys.stderr.write(report)
        failed = [r for r in preflight.results if not r[1]]
        sys.stderr.write(f"\n[red]启动中止：{failed[0][0]} 不可用 ({failed[0][2]})[/]\n")
        sys.exit(1)

    sys.stderr.write(report)
    degraded = [r for r in preflight.results if r[3]]
    if degraded:
        names = ", ".join(r[0] for r in degraded)
        sys.stderr.write(f"\n[yellow]注意：{names} 已降级，功能受限[/yellow]\n")

    print("Good Morning, Afternoon, And Evening")
    time.sleep(5)
    ChatApp().run()


if __name__ == "__main__":
    main()
