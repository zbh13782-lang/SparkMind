from __future__ import annotations

import csv
import json
import tempfile
import unittest
from pathlib import Path

from scripts.generate_spark_test_data import DatasetConfig, generate_dataset


class TestDemoDataGenerator(unittest.TestCase):
    def test_loader_uses_python_38_compatible_utc_timezone(self) -> None:
        source = (Path(__file__).resolve().parents[1] / "scripts/load_spark_test_data.py").read_text(encoding="utf-8")

        assert "from datetime import UTC" not in source
        assert "timezone.utc" in source

    def test_generates_relational_csv_and_nested_json_with_manifest(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "retail_demo"
            manifest = generate_dataset(
                DatasetConfig(
                    output=output,
                    seed=7,
                    start_date="2026-07-01",
                    days=2,
                    customers=20,
                    products=7,
                    orders=40,
                    events=60,
                    max_rows_per_file=25,
                )
            )

            assert manifest["dataset"] == "sparkmind_retail"
            assert manifest["logical_rows"] == {
                "customers": 20,
                "products": 7,
                "orders": 40,
                "order_items": 100,
                "events": 60,
            }
            assert (output / "csv/customers/part-00000.csv").is_file()
            assert (output / "csv/products/part-00000.csv").is_file()
            assert len(list((output / "csv/orders").glob("dt=*/part-*.csv"))) >= 2
            assert len(list((output / "json/events").glob("dt=*/part-*.jsonl"))) >= 2

            with (output / "csv/orders/dt=2026-07-01/part-00000.csv").open(encoding="utf-8") as handle:
                order = next(csv.DictReader(handle))
            assert order["order_id"].startswith("O")
            assert 1 <= int(order["customer_id"][1:]) <= 20

            event_file = next((output / "json/events").glob("dt=*/part-*.jsonl"))
            first_valid_event = next(
                json.loads(line)
                for line in event_file.read_text(encoding="utf-8").splitlines()
                if line.startswith("{") and line.endswith("}")
            )
            assert set(first_valid_event["device"]) == {"type", "os", "app_version"}
            assert set(first_valid_event["page"]) == {"name", "referrer", "duration_ms"}
            assert first_valid_event["event_id"].startswith("E")

            stored_manifest = json.loads((output / "manifest.json").read_text(encoding="utf-8"))
            assert stored_manifest == manifest
            assert stored_manifest["anomalies"]["duplicate_orders"] > 0
            assert stored_manifest["anomalies"]["corrupt_json_lines"] > 0

            events = [
                json.loads(line)
                for path in (output / "json/events").glob("dt=*/part-*.jsonl")
                for line in path.read_text(encoding="utf-8").splitlines()
                if line.startswith("{") and line.endswith("}")
            ]
            event_types_by_session: dict[str, set[str]] = {}
            for event in events:
                event_types_by_session.setdefault(event["session_id"], set()).add(event["event_type"])
            assert max(map(len, event_types_by_session.values())) >= 4
            view_sessions = {event["session_id"] for event in events if event["event_type"] == "page_view"}
            purchase_sessions = {event["session_id"] for event in events if event["event_type"] == "purchase"}
            assert 0 < len(purchase_sessions) < len(view_sessions)

    def test_same_seed_produces_identical_data(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config = {
                "seed": 23,
                "start_date": "2026-01-01",
                "days": 1,
                "customers": 10,
                "products": 5,
                "orders": 12,
                "events": 20,
                "max_rows_per_file": 100,
            }
            generate_dataset(DatasetConfig(output=root / "one", **config))
            generate_dataset(DatasetConfig(output=root / "two", **config))

            first = (root / "one/json/events/dt=2026-01-01/part-00000.jsonl").read_bytes()
            second = (root / "two/json/events/dt=2026-01-01/part-00000.jsonl").read_bytes()
            assert first == second
