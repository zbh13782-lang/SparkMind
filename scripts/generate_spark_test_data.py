#!/usr/bin/env python3
"""Generate scalable, deterministic retail data for local Spark exercises."""

from __future__ import annotations

import argparse
import csv
import json
import random
import shutil
from dataclasses import asdict, dataclass
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any, TextIO


@dataclass(frozen=True)
class DatasetConfig:
    output: Path
    seed: int = 20260813
    start_date: str = "2026-01-01"
    days: int = 30
    customers: int = 100_000
    products: int = 20_000
    orders: int = 5_000_000
    events: int = 20_000_000
    max_rows_per_file: int = 250_000

    def __post_init__(self) -> None:
        date.fromisoformat(self.start_date)
        for name in ("days", "customers", "products", "orders", "events", "max_rows_per_file"):
            if getattr(self, name) < 1:
                raise ValueError(f"{name} must be greater than zero")


PRESETS: dict[str, dict[str, int]] = {
    "tiny": {"days": 2, "customers": 100, "products": 30, "orders": 1_000, "events": 5_000},
    "small": {"days": 7, "customers": 10_000, "products": 1_000, "orders": 100_000, "events": 500_000},
    "medium": {"days": 30, "customers": 100_000, "products": 20_000, "orders": 5_000_000, "events": 20_000_000},
    "large": {"days": 90, "customers": 1_000_000, "products": 100_000, "orders": 50_000_000, "events": 200_000_000},
}

CUSTOMER_COLUMNS = [
    "customer_id",
    "signup_date",
    "city",
    "customer_tier",
    "age",
    "gender",
    "is_active",
    "email",
]
PRODUCT_COLUMNS = [
    "product_id",
    "category",
    "brand",
    "unit_price",
    "unit_cost",
    "is_discontinued",
]
ORDER_COLUMNS = [
    "order_id",
    "customer_id",
    "order_ts",
    "status",
    "channel",
    "payment_method",
    "province",
    "total_amount",
    "discount_amount",
    "shipping_amount",
]
ITEM_COLUMNS = [
    "order_id",
    "item_id",
    "product_id",
    "quantity",
    "unit_price",
    "discount_amount",
    "item_amount",
]


class CsvSink:
    def __init__(self, directory: Path, columns: list[str], max_rows: int) -> None:
        self.directory = directory
        self.columns = columns
        self.max_rows = max_rows
        self.file: TextIO | None = None
        self.writer: csv.DictWriter[str] | None = None
        self.part = -1
        self.rows_in_part = 0

    def write(self, row: dict[str, Any]) -> None:
        if self.file is None or self.rows_in_part >= self.max_rows:
            self.close()
            self.part += 1
            self.directory.mkdir(parents=True, exist_ok=True)
            self.file = (self.directory / f"part-{self.part:05d}.csv").open("w", encoding="utf-8", newline="")
            self.writer = csv.DictWriter(self.file, fieldnames=self.columns, lineterminator="\n")
            self.writer.writeheader()
            self.rows_in_part = 0
        assert self.writer is not None
        self.writer.writerow(row)
        self.rows_in_part += 1

    def close(self) -> None:
        if self.file is not None:
            self.file.close()
        self.file = None
        self.writer = None


class JsonSink:
    def __init__(self, directory: Path, max_rows: int) -> None:
        self.directory = directory
        self.max_rows = max_rows
        self.file: TextIO | None = None
        self.part = -1
        self.rows_in_part = 0

    def write(self, row: dict[str, Any] | str) -> None:
        if self.file is None or self.rows_in_part >= self.max_rows:
            self.close()
            self.part += 1
            self.directory.mkdir(parents=True, exist_ok=True)
            self.file = (self.directory / f"part-{self.part:05d}.jsonl").open("w", encoding="utf-8")
            self.rows_in_part = 0
        assert self.file is not None
        line = row if isinstance(row, str) else json.dumps(row, ensure_ascii=False, separators=(",", ":"))
        self.file.write(line + "\n")
        self.rows_in_part += 1

    def close(self) -> None:
        if self.file is not None:
            self.file.close()
        self.file = None


def _weighted_customer(rng: random.Random, count: int) -> int:
    # Ten percent of activity lands on the first one percent of customers.
    if rng.random() < 0.10:
        return rng.randint(1, max(1, count // 100))
    return rng.randint(1, count)


def _weighted_product(rng: random.Random, count: int) -> int:
    # Twenty percent of line items lands on the first ten products.
    if rng.random() < 0.20:
        return rng.randint(1, min(10, count))
    return rng.randint(1, count)


def _timestamp(day: date, seconds: int) -> str:
    value = datetime.combine(day, datetime.min.time(), tzinfo=UTC) + timedelta(seconds=seconds)
    return value.isoformat().replace("+00:00", "Z")


def _money(value: Decimal) -> str:
    return str(value.quantize(Decimal("0.01")))


def generate_dataset(config: DatasetConfig) -> dict[str, Any]:
    rng = random.Random(config.seed)
    root = config.output.resolve()
    if root.exists():
        shutil.rmtree(root)
    root.mkdir(parents=True)

    start = date.fromisoformat(config.start_date)
    cities = ["Shanghai", "Beijing", "Shenzhen", "Hangzhou", "Chengdu", "Wuhan", "Xian", "Nanjing"]
    provinces = ["Shanghai", "Beijing", "Guangdong", "Zhejiang", "Sichuan", "Hubei", "Shaanxi", "Jiangsu"]
    categories = ["electronics", "home", "sports", "beauty", "food", "books", "apparel", "toys"]
    brands = [f"brand_{index:03d}" for index in range(1, 101)]
    statuses = ["paid", "shipped", "completed", "cancelled", "refunded"]
    channels = ["app", "web", "mini_program", "store"]
    payments = ["wallet", "bank_card", "credit", "cash"]
    funnel_paths = [
        ["page_view", "search", "add_to_cart", "favorite", "checkout", "purchase"],
        ["page_view", "search", "add_to_cart", "favorite", "add_to_cart", "checkout"],
        ["page_view", "search", "page_view", "search", "page_view", "search"],
        ["page_view", "page_view", "page_view", "page_view", "page_view", "page_view"],
    ]

    customer_sink = CsvSink(root / "csv/customers", CUSTOMER_COLUMNS, config.max_rows_per_file)
    product_sink = CsvSink(root / "csv/products", PRODUCT_COLUMNS, config.max_rows_per_file)
    order_sinks: dict[str, CsvSink] = {}
    item_sinks: dict[str, CsvSink] = {}
    event_sinks: dict[str, JsonSink] = {}

    for customer_number in range(1, config.customers + 1):
        signup = start - timedelta(days=rng.randint(1, 730))
        email = "" if customer_number % 211 == 0 else f"user{customer_number}@example.test"
        customer_sink.write(
            {
                "customer_id": f"C{customer_number:010d}",
                "signup_date": signup.isoformat(),
                "city": cities[(customer_number - 1) % len(cities)],
                "customer_tier": ["bronze", "silver", "gold", "platinum"][customer_number % 4],
                "age": "" if customer_number % 307 == 0 else 18 + customer_number % 53,
                "gender": ["F", "M", "unknown"][customer_number % 3],
                "is_active": str(customer_number % 17 != 0).lower(),
                "email": email,
            }
        )
    customer_sink.close()

    for product_number in range(1, config.products + 1):
        price = Decimal(500 + product_number % 50_000) / Decimal(100)
        cost = price * Decimal("0.62")
        product_sink.write(
            {
                "product_id": f"P{product_number:08d}",
                "category": categories[(product_number - 1) % len(categories)],
                "brand": brands[(product_number - 1) % len(brands)],
                "unit_price": _money(price),
                "unit_cost": _money(cost),
                "is_discontinued": str(product_number % 97 == 0).lower(),
            }
        )
    product_sink.close()

    duplicate_orders = 0
    negative_order_amounts = 0
    order_items = 0
    for order_number in range(1, config.orders + 1):
        day = start + timedelta(days=(order_number - 1) % config.days)
        partition = day.isoformat()
        order_sink = order_sinks.setdefault(
            partition,
            CsvSink(root / f"csv/orders/dt={partition}", ORDER_COLUMNS, config.max_rows_per_file),
        )
        item_sink = item_sinks.setdefault(
            partition,
            CsvSink(root / f"csv/order_items/dt={partition}", ITEM_COLUMNS, config.max_rows_per_file),
        )
        order_id = f"O{order_number:012d}"
        customer_id = f"C{_weighted_customer(rng, config.customers):010d}"
        item_count = 1 + (order_number - 1) % 4
        subtotal = Decimal(0)
        total_discount = Decimal(0)
        for item_number in range(1, item_count + 1):
            product_number = _weighted_product(rng, config.products)
            quantity = 1 + rng.randrange(4)
            price = Decimal(500 + product_number % 50_000) / Decimal(100)
            discount = (price * quantity * Decimal(rng.choice([0, 0, 0, 5, 10])) / Decimal(100)).quantize(
                Decimal("0.01")
            )
            item_amount = price * quantity - discount
            subtotal += item_amount
            total_discount += discount
            item_sink.write(
                {
                    "order_id": order_id,
                    "item_id": item_number,
                    "product_id": f"P{product_number:08d}",
                    "quantity": quantity,
                    "unit_price": _money(price),
                    "discount_amount": _money(discount),
                    "item_amount": _money(item_amount),
                }
            )
            order_items += 1
        shipping = Decimal(0) if subtotal >= 99 else Decimal(8)
        total = subtotal + shipping
        if order_number % 2003 == 0:
            total = -total
            negative_order_amounts += 1
        row = {
            "order_id": order_id,
            "customer_id": customer_id,
            "order_ts": _timestamp(day, rng.randrange(86_400)),
            "status": statuses[order_number % len(statuses)],
            "channel": channels[order_number % len(channels)],
            "payment_method": payments[order_number % len(payments)],
            "province": provinces[order_number % len(provinces)],
            "total_amount": _money(total),
            "discount_amount": _money(total_discount),
            "shipping_amount": _money(shipping),
        }
        order_sink.write(row)
        if order_number % 997 == 0 or (config.orders < 997 and order_number == config.orders):
            order_sink.write(row)
            duplicate_orders += 1

    for sink in [*order_sinks.values(), *item_sinks.values()]:
        sink.close()

    corrupt_json_lines = 0
    late_events = 0
    for event_number in range(1, config.events + 1):
        session_number = (event_number - 1) // 6 + 1
        session_step = (event_number - 1) % 6
        event_day = start + timedelta(days=(session_number - 1) % config.days)
        partition = event_day.isoformat()
        event_sink = event_sinks.setdefault(
            partition,
            JsonSink(root / f"json/events/dt={partition}", config.max_rows_per_file),
        )
        if event_number % 5003 == 0 or (config.events < 5003 and event_number == config.events):
            event_sink.write('{"event_id":"CORRUPT","device":')
            corrupt_json_lines += 1
            continue
        session_start_seconds = (session_number * 37) % 82_800
        event_time = datetime.fromisoformat(_timestamp(event_day, session_start_seconds + session_step * 120))
        ingest_delay = 86_400 + rng.randrange(172_800) if event_number % 53 == 0 else rng.randrange(600)
        if ingest_delay >= 86_400:
            late_events += 1
        if session_number % 10 == 0:
            customer_number = 1 + session_number % max(1, config.customers // 100)
        else:
            customer_number = 1 + (session_number * 7919) % config.customers
        event_type = funnel_paths[session_number % len(funnel_paths)][session_step]
        event_sink.write(
            {
                "event_id": f"E{event_number:014d}",
                "customer_id": f"C{customer_number:010d}",
                "session_id": f"S{session_number:012d}",
                "event_time": event_time.isoformat().replace("+00:00", "Z"),
                "event_type": event_type,
                "order_id": f"O{1 + (event_number - 1) % config.orders:012d}" if event_type == "purchase" else None,
                "device": {
                    "type": ["mobile", "desktop", "tablet"][event_number % 3],
                    "os": ["Android", "iOS", "Windows", "macOS"][event_number % 4],
                    "app_version": f"{1 + event_number % 4}.{event_number % 10}.{event_number % 17}",
                },
                "page": {
                    "name": ["home", "search", "detail", "cart", "checkout"][event_number % 5],
                    "referrer": ["direct", "search", "campaign", "recommendation"][event_number % 4],
                    "duration_ms": rng.randrange(100, 120_000),
                },
                "attributes": {
                    "campaign_id": None if event_number % 4 else f"campaign_{event_number % 20:02d}",
                    "experiment_group": ["control", "variant_a", "variant_b"][event_number % 3],
                },
                "ingest_time": (event_time + timedelta(seconds=ingest_delay)).isoformat().replace("+00:00", "Z"),
            }
        )
    for sink in event_sinks.values():
        sink.close()

    physical_rows = {
        "customers": config.customers,
        "products": config.products,
        "orders": config.orders + duplicate_orders,
        "order_items": order_items,
        "events": config.events,
    }
    manifest: dict[str, Any] = {
        "dataset": "sparkmind_retail",
        "version": 1,
        "generated_at": "deterministic",
        "config": {**asdict(config), "output": str(root)},
        "logical_rows": {
            "customers": config.customers,
            "products": config.products,
            "orders": config.orders,
            "order_items": order_items,
            "events": config.events,
        },
        "physical_rows": physical_rows,
        "anomalies": {
            "duplicate_orders": duplicate_orders,
            "negative_order_amounts": negative_order_amounts,
            "corrupt_json_lines": corrupt_json_lines,
            "late_events": late_events,
            "blank_customer_emails": config.customers // 211,
        },
        "partitions": {"orders": config.days, "order_items": config.days, "events": config.days},
    }
    (root / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return manifest


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=Path("data/sparkmind_retail"))
    parser.add_argument("--preset", choices=PRESETS, default="small")
    parser.add_argument("--seed", type=int, default=20260813)
    parser.add_argument("--start-date", default="2026-01-01")
    parser.add_argument("--days", type=int)
    parser.add_argument("--customers", type=int)
    parser.add_argument("--products", type=int)
    parser.add_argument("--orders", type=int)
    parser.add_argument("--events", type=int)
    parser.add_argument("--max-rows-per-file", type=int, default=250_000)
    return parser.parse_args()


def main() -> None:
    args = _parse_args()
    values = PRESETS[args.preset] | {
        key: value
        for key, value in {
            "days": args.days,
            "customers": args.customers,
            "products": args.products,
            "orders": args.orders,
            "events": args.events,
        }.items()
        if value is not None
    }
    config = DatasetConfig(
        output=args.output,
        seed=args.seed,
        start_date=args.start_date,
        max_rows_per_file=args.max_rows_per_file,
        **values,
    )
    manifest = generate_dataset(config)
    print(json.dumps(manifest, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
