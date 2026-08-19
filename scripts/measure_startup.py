"""Measure the synchronous constructor cost of the Textual shell."""

from __future__ import annotations

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

started = time.perf_counter()
from sparkos.ui.chat_app import ChatApp

ChatApp()
elapsed = time.perf_counter() - started
print(f"constructor_seconds={elapsed:.3f}")
print("first_frame=observe StartupPanel when running the app")
print("ready=observe workspace reveal after real preflight/runtime completion")
