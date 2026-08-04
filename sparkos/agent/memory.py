"""对话历史持久化：~/.sparkmind/history/ 目录。"""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

HISTORY_DIR = Path(__file__).resolve().parent.parent.parent / ".sparkmind" / "history"


@dataclass
class Session:
    session_id: str
    messages: list[dict[str, Any]]
    created_at: str


def _ensure_dir() -> None:
    HISTORY_DIR.mkdir(parents=True, exist_ok=True)


def list_sessions() -> list[Session]:
    """列出所有历史会话。"""
    _ensure_dir()
    sessions: list[Session] = []
    for f in sorted(HISTORY_DIR.glob("*.json"), reverse=True):
        try:
            data = json.loads(f.read_text(encoding="utf-8"))
            sessions.append(
                Session(
                    session_id=f.stem,
                    messages=data.get("messages", []),
                    created_at=data.get("created_at", ""),
                )
            )
        except json.JSONDecodeError, KeyError:
            continue
    return sessions


def load_session(session_id: str) -> Session | None:
    """读取指定会话。"""
    path = HISTORY_DIR / f"{session_id}.json"
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return Session(
            session_id=session_id,
            messages=data.get("messages", []),
            created_at=data.get("created_at", ""),
        )
    except json.JSONDecodeError, KeyError:
        return None


def save_session(session_id: str, messages: list[dict[str, Any]]) -> None:
    """保存/覆盖指定会话。"""
    _ensure_dir()
    path = HISTORY_DIR / f"{session_id}.json"
    # 如果文件已存在，保留原始创建时间
    if path.is_file():
        try:
            existing = json.loads(path.read_text(encoding="utf-8"))
            created_at = existing.get("created_at", datetime.now().isoformat())  # noqa: DTZ005
        except json.JSONDecodeError, KeyError:
            created_at = datetime.now().isoformat()  # noqa: DTZ005
    else:
        created_at = datetime.now().isoformat()  # noqa: DTZ005

    path.write_text(
        json.dumps(
            {"session_id": session_id, "created_at": created_at, "messages": messages},
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )


def create_session(messages: list[dict[str, Any]]) -> Session:
    """创建新会话并保存。"""
    session_id = datetime.now().strftime("%Y%m%d-%H%M%S") + f"-{uuid.uuid4().hex[:6]}"  # noqa: DTZ005
    save_session(session_id, messages)
    return Session(
        session_id=session_id,
        messages=messages,
        created_at=datetime.now().isoformat(),  # noqa: DTZ005
    )


def get_latest_session() -> Session | None:
    """获取最近一次历史会话。"""
    sessions = list_sessions()
    return sessions[0] if sessions else None


def delete_session(session_id: str) -> None:
    """删除指定会话。"""
    path = HISTORY_DIR / f"{session_id}.json"
    if path.is_file():
        path.unlink()
