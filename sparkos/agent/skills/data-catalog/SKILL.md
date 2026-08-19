---
name: data-catalog
description: 当用户问数、需要发现 Hive 表结构、检查 CSV/JSON/Parquet 数据或注册新数据集时使用。
---

# Data Catalog Skill

## 默认问数流程

1. 调用 `get_data_catalog`，默认查询 `sparkmind_demo`。
2. 根据表名、字段、分区和业务描述选择唯一合理的表。
3. 对选中的表再次调用 `get_data_catalog` 并指定 `database`、`table`，获取完整字段。
4. 生成使用全限定表名的 Spark SQL；日期条件优先使用分区字段。
5. 调用 `run_spark_job` 执行并解释结果。

已有 Catalog 时，不先要求用户提供表结构。只有多张表语义冲突、没有数据源路径，或指标口径无法从 Catalog 推断时才提问。

## 新文件数据流程

1. 用户给出文件或目录路径后，调用 `inspect_data_source`。
2. 检查格式、字段、嵌套结构和样例。
3. 获得明确目标表名和数据库后调用 `register_dataset`。
4. 注册成功后重新调用 `get_data_catalog`，再执行 SQL。

注册默认使用 Hive-managed Parquet 且不覆盖已有表。只有用户明确要求替换时才传 `if_exists=overwrite`。

## 工具顺序

`get_data_catalog -> get_data_catalog(table=...) -> run_spark_job`

新文件：`inspect_data_source -> register_dataset -> get_data_catalog -> run_spark_job`。
