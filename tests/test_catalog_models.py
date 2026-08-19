from __future__ import annotations

from pathlib import Path

import pytest

from sparkos.infrastructure.catalog.models import (
    CatalogSnapshot,
    DatabaseMetadata,
    DatasetRegistrationRequest,
)


def test_registration_rejects_path_outside_repo(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    outside = tmp_path / "outside.csv"
    outside.touch()

    with pytest.raises(ValueError, match="工作目录"):
        DatasetRegistrationRequest(
            repo_root=repo,
            path=outside,
            data_format="csv",
            database="analytics",
            table="sales",
        )


def test_registration_rejects_invalid_identifier(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    source = repo / "data/sales.csv"
    source.parent.mkdir(parents=True)
    source.touch()

    with pytest.raises(ValueError, match="table"):
        DatasetRegistrationRequest(
            repo_root=repo,
            path=source,
            data_format="csv",
            database="analytics",
            table="sales; drop table x",
        )


def test_catalog_snapshot_round_trips_json() -> None:
    snapshot = CatalogSnapshot(
        generated_at="2026-08-18T10:00:00+08:00",
        databases=(DatabaseMetadata(name="sparkmind_demo", tables=()),),
    )

    assert CatalogSnapshot.from_json(snapshot.to_json()) == snapshot


def test_registration_copies_options_and_normalizes_path(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    source = repo / "data/sales.csv"
    source.parent.mkdir(parents=True)
    source.touch()
    options = {"header": "true"}

    request = DatasetRegistrationRequest(
        repo_root=repo,
        path=source,
        data_format="csv",
        database="analytics",
        table="sales",
        options=options,
        partition_columns=("dt",),
    )
    options["header"] = "false"

    assert request.options["header"] == "true"
    assert request.path == source.resolve()
