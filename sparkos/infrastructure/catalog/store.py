"""Atomic on-disk cache for catalog snapshots."""

from __future__ import annotations

import os
import tempfile
from datetime import UTC, datetime
from pathlib import Path

from sparkos.infrastructure.catalog.models import CatalogSnapshot


class CatalogStore:
    def __init__(self, path: Path, ttl_seconds: int) -> None:
        if ttl_seconds < 0:
            raise ValueError("ttl_seconds 不能小于 0")
        self.path = path
        self.ttl_seconds = ttl_seconds

    def load(self) -> CatalogSnapshot | None:
        try:
            return CatalogSnapshot.from_json(self.path.read_text(encoding="utf-8"))
        except (OSError, KeyError, TypeError, ValueError):
            return None

    def save(self, snapshot: CatalogSnapshot) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fd, temporary_name = tempfile.mkstemp(
            prefix=f".{self.path.name}.",
            suffix=".tmp",
            dir=self.path.parent,
        )
        temporary = Path(temporary_name)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                handle.write(snapshot.to_json())
                handle.write("\n")
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, self.path)
        finally:
            temporary.unlink(missing_ok=True)

    def is_fresh(self, now: datetime | None = None) -> bool:
        try:
            modified_at = datetime.fromtimestamp(self.path.stat().st_mtime, tz=UTC)
        except OSError:
            return False
        current = now or datetime.now(UTC)
        return (current - modified_at).total_seconds() <= self.ttl_seconds


__all__ = ["CatalogStore"]
