#!/usr/bin/env python3
"""Load generated CSV and JSON data into persistent Spark Hive tables."""

from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path

from pyspark.sql import SparkSession
from pyspark.sql import functions as F
from pyspark.sql import types as T

CUSTOMER_SCHEMA = T.StructType(
    [
        T.StructField("customer_id", T.StringType()),
        T.StructField("signup_date", T.DateType()),
        T.StructField("city", T.StringType()),
        T.StructField("customer_tier", T.StringType()),
        T.StructField("age", T.IntegerType()),
        T.StructField("gender", T.StringType()),
        T.StructField("is_active", T.BooleanType()),
        T.StructField("email", T.StringType()),
    ]
)

PRODUCT_SCHEMA = T.StructType(
    [
        T.StructField("product_id", T.StringType()),
        T.StructField("category", T.StringType()),
        T.StructField("brand", T.StringType()),
        T.StructField("unit_price", T.DecimalType(12, 2)),
        T.StructField("unit_cost", T.DecimalType(12, 2)),
        T.StructField("is_discontinued", T.BooleanType()),
    ]
)

ORDER_SCHEMA = T.StructType(
    [
        T.StructField("order_id", T.StringType()),
        T.StructField("customer_id", T.StringType()),
        T.StructField("order_ts", T.TimestampType()),
        T.StructField("status", T.StringType()),
        T.StructField("channel", T.StringType()),
        T.StructField("payment_method", T.StringType()),
        T.StructField("province", T.StringType()),
        T.StructField("total_amount", T.DecimalType(14, 2)),
        T.StructField("discount_amount", T.DecimalType(14, 2)),
        T.StructField("shipping_amount", T.DecimalType(14, 2)),
    ]
)

ITEM_SCHEMA = T.StructType(
    [
        T.StructField("order_id", T.StringType()),
        T.StructField("item_id", T.IntegerType()),
        T.StructField("product_id", T.StringType()),
        T.StructField("quantity", T.IntegerType()),
        T.StructField("unit_price", T.DecimalType(12, 2)),
        T.StructField("discount_amount", T.DecimalType(12, 2)),
        T.StructField("item_amount", T.DecimalType(14, 2)),
    ]
)

EVENT_SCHEMA = T.StructType(
    [
        T.StructField("event_id", T.StringType()),
        T.StructField("customer_id", T.StringType()),
        T.StructField("session_id", T.StringType()),
        T.StructField("event_time", T.TimestampType()),
        T.StructField("event_type", T.StringType()),
        T.StructField("order_id", T.StringType()),
        T.StructField(
            "device",
            T.StructType(
                [
                    T.StructField("type", T.StringType()),
                    T.StructField("os", T.StringType()),
                    T.StructField("app_version", T.StringType()),
                ]
            ),
        ),
        T.StructField(
            "page",
            T.StructType(
                [
                    T.StructField("name", T.StringType()),
                    T.StructField("referrer", T.StringType()),
                    T.StructField("duration_ms", T.LongType()),
                ]
            ),
        ),
        T.StructField(
            "attributes",
            T.StructType(
                [
                    T.StructField("campaign_id", T.StringType()),
                    T.StructField("experiment_group", T.StringType()),
                ]
            ),
        ),
        T.StructField("ingest_time", T.TimestampType()),
        T.StructField("_corrupt_record", T.StringType()),
    ]
)


def _csv(spark: SparkSession, path: str, schema: T.StructType):
    return spark.read.option("header", "true").schema(schema).csv(path)


def _catalog_payload(spark: SparkSession) -> dict:
    """Extract Hive metadata only; never scan table data while refreshing the cache."""
    databases: list[dict] = []
    for database in spark.catalog.listDatabases():
        tables: list[dict] = []
        for table in spark.catalog.listTables(database.name):
            if table.isTemporary:
                continue
            qualified = f"`{database.name}`.`{table.name}`"
            details = spark.sql(f"DESCRIBE TABLE EXTENDED {qualified}").collect()
            detail_map = {
                str(row.col_name).strip(): str(row.data_type).strip()
                for row in details
                if row.col_name and str(row.col_name).strip()
            }
            columns = [
                {
                    "name": column.name,
                    "data_type": column.dataType,
                    "nullable": bool(column.nullable),
                    "is_partition": bool(column.isPartition),
                    "description": column.description or "",
                }
                for column in spark.catalog.listColumns(table.name, database.name)
            ]
            tables.append(
                {
                    "database": database.name,
                    "name": table.name,
                    "table_type": table.tableType or "",
                    "provider": detail_map.get("Provider", ""),
                    "location": detail_map.get("Location", ""),
                    "columns": columns,
                    "description": getattr(table, "description", None) or "",
                }
            )
        tables.sort(key=lambda item: item["name"])
        databases.append({"name": database.name, "tables": tables})
    databases.sort(key=lambda item: item["name"])
    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),  # noqa: UP017 - Spark uses Python 3.8.
        "databases": databases,
        "warnings": [],
    }


def _refresh_catalog(spark: SparkSession, catalog_path: str) -> Path:
    path = Path(catalog_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(_catalog_payload(spark), ensure_ascii=False), encoding="utf-8")
    temporary.replace(path)
    return path


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-root", default="/opt/sparkos/data/sparkmind_retail")
    parser.add_argument("--database", default="sparkmind_demo")
    parser.add_argument("--catalog-path", default="/opt/sparkos/artifacts/catalog/catalog.json")
    args = parser.parse_args()

    spark = SparkSession.builder.appName("sparkmind-load-demo-data").enableHiveSupport().getOrCreate()
    spark.sql(f"CREATE DATABASE IF NOT EXISTS `{args.database}`")

    customers = _csv(spark, f"{args.data_root}/csv/customers", CUSTOMER_SCHEMA)
    products = _csv(spark, f"{args.data_root}/csv/products", PRODUCT_SCHEMA)
    orders = _csv(spark, f"{args.data_root}/csv/orders", ORDER_SCHEMA).withColumn("dt", F.to_date("order_ts"))
    items = _csv(spark, f"{args.data_root}/csv/order_items", ITEM_SCHEMA).withColumn(
        "dt", F.to_date(F.regexp_extract(F.input_file_name(), r"dt=([0-9-]+)", 1))
    )
    events = (
        spark.read.option("mode", "PERMISSIVE")
        .option("columnNameOfCorruptRecord", "_corrupt_record")
        .schema(EVENT_SCHEMA)
        .json(f"{args.data_root}/json/events")
        .withColumn("dt", F.to_date(F.coalesce("event_time", "ingest_time")))
    )

    outputs = {
        "dim_customer": customers,
        "dim_product": products,
        "fact_order": orders,
        "fact_order_item": items,
        "fact_event": events,
    }
    for table, frame in outputs.items():
        writer = frame.write.mode("overwrite").format("parquet")
        if "dt" in frame.columns:
            writer = writer.partitionBy("dt")
        writer.saveAsTable(f"`{args.database}`.`{table}`")

    print("loaded tables:")
    for table in outputs:
        count = spark.table(f"`{args.database}`.`{table}`").count()
        print(f"{args.database}.{table}: {count}")
    catalog_path = _refresh_catalog(spark, args.catalog_path)
    print(f"catalog refreshed: {catalog_path}")
    print(f"default database: {args.database}")
    print(f"tables: {len(outputs)}")
    spark.stop()


if __name__ == "__main__":
    main()
