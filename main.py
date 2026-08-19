"""SparkMind CLI — Textual 交互界面。"""

from __future__ import annotations

from sparkos.startup.preflight import PreflightResult, render_preflight_report, run_preflight
from sparkos.ui.chat_app import ChatApp

__all__ = ["ChatApp", "PreflightResult", "main", "render_preflight_report", "run_preflight"]


def main() -> None:
    """Start the Textual shell; bootstrap runs inside the app."""
    ChatApp().run()


if __name__ == "__main__":
    main()
