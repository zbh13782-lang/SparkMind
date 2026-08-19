"""Startup orchestration for SparkMind."""

from .preflight import PreflightResult, render_preflight_report, run_preflight

__all__ = ["PreflightResult", "render_preflight_report", "run_preflight"]
