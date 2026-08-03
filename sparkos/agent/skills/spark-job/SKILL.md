---
name: spark-job
description: 当用户需要提交 Spark Job、配置执行参数、运行 Spark SQL 或 PySpark 任务、查询任务状态、解析日志、定位失败原因或重试任务时使用。
---

# Spark Job Skill

Spark Job Skill 负责把上游 Skill 生成的 SQL、PySpark 代码或 ETL DAG 转换为可提交的 Spark Job，并管理任务提交、状态查询、日志摘要和失败诊断。

## 能力边界

本 Skill 负责：

1. 生成 Spark Job 提交配置。
2. 提交 Spark SQL 或 PySpark 任务。
3. 查询任务状态。
4. 解析任务日志。
5. 识别常见失败原因。
6. 生成重试建议。
7. 将执行计划和指标交给 DAGDiagnosisSkill。

不负责生成业务 SQL、设计清洗规则或构造特征。

## 输入

```json
{
  "job_name": "任务名称",
  "job_type": "spark_sql | pyspark | etl_pipeline",
  "code": "SQL 或 PySpark 代码",
  "runtime_config": {
    "executor_memory": "4g",
    "executor_cores": 2,
    "num_executors": 10,
    "driver_memory": "2g",
    "spark_conf": {}
  },
  "dependencies": [],
  "biz_date": "业务日期"
}
```

## 输出

```json
{
  "job_id": "Spark Job ID",
  "status": "pending | running | success | failed",
  "submit_command": "提交命令",
  "log_summary": "日志摘要",
  "failure_reason": "失败原因",
  "retry_suggestion": "重试建议",
  "metrics": {
    "duration": "执行耗时",
    "shuffle_read": "Shuffle Read",
    "shuffle_write": "Shuffle Write",
    "input_rows": "输入行数",
    "output_rows": "输出行数"
  },
  "next_skill": "dag-diagnosis | data-quality | null"
}
```

## 工作流程

1. 接收上游 Skill 生成的 SQL、PySpark 代码或 ETL DAG。
2. 检查任务名称、业务日期、依赖和运行参数。
3. 根据任务类型生成提交命令。
4. 提交任务并记录 job_id。
5. 轮询任务状态。
6. 成功时提取执行指标和输出信息。
7. 失败时解析日志，定位失败原因。
8. 如果是资源、Shuffle、Join 或倾斜问题，将指标交给 DAGDiagnosisSkill。
9. 如果任务成功且有产出表，将产出交给 DataQualitySkill。

## 参数配置原则

1. 小任务优先使用较少 executor，避免资源浪费。
2. 大表 Join 或 Shuffle 任务需要关注 executor memory 和 shuffle partition。
3. 任务失败不能盲目重试，必须先判断失败类型。
4. OOM 失败优先分析数据量、Join 和 Shuffle，而不是只加内存。
5. 数据倾斜问题应交给 DAGDiagnosisSkill 分析。
6. 产出任务成功后应触发数据质量校验。

## 常见失败类型

1. SQL 语法错误。
2. 字段不存在。
3. 分区不存在。
4. 权限不足。
5. Executor OOM。
6. Shuffle Fetch Failed。
7. 数据倾斜导致 Stage 长尾。
8. 输出路径或目标表冲突。

## 示例

输入：

```json
{
  "job_name": "user_active_daily",
  "job_type": "spark_sql",
  "code": "insert overwrite table dws_user_active_di partition(dt='${biz_date}') select ..."
}
```

输出：

```json
{
  "status": "success",
  "job_id": "application_xxx",
  "metrics": {
    "duration": "8m20s",
    "shuffle_read": "120GB",
    "shuffle_write": "45GB"
  },
  "next_skill": "data-quality"
}
```
