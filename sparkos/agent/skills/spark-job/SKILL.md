---
name: spark-job
description: 当用户需要运行 Spark SQL 或 PySpark 任务、查询任务状态、解析日志或定位失败原因时使用。
---

# Spark Job Skill

本 Skill 通过 `run_spark_job` 工具在本地 Docker Spark 集群同步执行作业。工具调用返回前，作业已经成功、失败或超时；不要生成状态轮询或日志查询工具名。

## 执行工具

调用一次 `run_spark_job`，工具返回后检查终端状态和输出。

输入：

```json
{
  "job_name": "user_active_daily",
  "job_type": "spark_sql",
  "code": "select city_id, count(distinct user_id) from dwd_user_action_di group by city_id",
  "executor_memory": "1g",
  "executor_cores": 1,
  "num_executors": 1,
  "driver_memory": "1g",
  "timeout_seconds": 600
}
```

输出：

```json
{
  "job_id": "7c8f3e6a0d9b4d5eb3c1c8e3276d121a",
  "status": "succeeded | failed | timed_out",
  "application_id": "app-1722920000000-0001",
  "exit_code": 0,
  "duration_seconds": 8.42,
  "log_path": "artifacts/spark-jobs/7c8f3e6a0d9b4d5eb3c1c8e3276d121a/spark.log",
  "output": "Spark 日志末尾或 SQL 结果"
}
```

## 工作流程

1. 验证输入参数：`job_name` 1-80 字符、`job_type` 为 `spark_sql` 或 `pyspark`、`code` 非空。
2. 调用 `run_spark_job` 一次，等待返回。
3. 检查 `status` 字段：
   - `succeeded`：根据 `output` 解释结果，SQL 结果在 `output` 中可见。
   - `failed`：根据 `output` 中的错误信息解释失败原因，建议修复后重试。
   - `timed_out`：作业超时，建议增大 `timeout_seconds` 或检查数据量。
4. 如需完整日志，引用 `log_path` 用 `read_file` 读取。

## 参数配置原则

1. 小任务优先使用较少 executor，避免资源浪费。
2. 大表 Join 或 Shuffle 任务增大 `executor_memory` 和 `executor_cores`。
3. 任务失败先看 `output` 中的错误信息，不要盲目重试。
4. OOM 失败优先分析数据量和 Join 逻辑，而不是只加内存。
5. 超时任务增大 `timeout_seconds` 后重试。

## 常见失败类型

1. SQL 语法错误。
2. 字段不存在。
3. 分区不存在。
4. Executor OOM。
5. 数据倾斜导致 Stage 长尾。
6. 输出路径冲突。
