from __future__ import annotations

import os
from datetime import UTC, datetime, timedelta
from pathlib import Path

from sparkos.infrastructure.catalog.models import CatalogSnapshot
from sparkos.infrastructure.catalog.store import CatalogStore


def test_store_saves_snapshot_atomically(tmp_path: Path) -> None:
    store = CatalogStore(tmp_path / "catalog.json", ttl_seconds=300)
    snapshot = CatalogSnapshot(generated_at="2026-08-18T10:00:00+08:00", databases=())

    store.save(snapshot)

    assert store.load() == snapshot
    assert not list(tmp_path.glob("*.tmp"))


def test_corrupt_cache_is_treated_as_miss(tmp_path: Path) -> None:
    path = tmp_path / "catalog.json"
    path.write_text("{bad", encoding="utf-8")

    assert CatalogStore(path, ttl_seconds=300).load() is None


def test_store_freshness_uses_file_modification_time(tmp_path: Path) -> None:
    path = tmp_path / "catalog.json"
    store = CatalogStore(path, ttl_seconds=60)
    store.save(CatalogSnapshot(generated_at="2026-08-18T10:00:00+08:00", databases=()))
    now = datetime.now(UTC)
    old = (now - timedelta(seconds=120)).timestamp()
    os.utime(path, (old, old))

    assert not store.is_fresh(now)
