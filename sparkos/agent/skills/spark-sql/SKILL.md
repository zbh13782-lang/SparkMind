---
name: spark-sql
description: 当用户需要使用自然语言查询数据、生成 Spark SQL、优化 SQL、解释 SQL 执行计划、排查 SQL 错误或将分析需求转成可执行 SQL 时使用。
---

# Spark SQL Skill

Spark SQL Skill 负责把用户的数据分析意图转换为可执行、可解释、可优化的 Spark SQL，并在执行前后提供 SQL 校验、风险提示和结果解释。

## 能力边界

本 Skill 只负责 Spark SQL 相关任务，包括：

1. 根据自然语言生成 Spark SQL。
2. 根据表结构、字段说明和分区信息补全 SQL。
3. 检查 SQL 语法、字段引用、分区条件和潜在性能风险。
4. 解释已有 SQL 的业务含义和执行逻辑。
5. 基于执行计划识别全表扫描、Shuffle、Join 放大、数据倾斜等问题。
6. 输出可交给 SparkJobSkill 执行的 SQL 任务描述。

不负责复杂 ETL 编排、数据清洗规则生成、特征工程流水线或 Spark Job 提交。

## 输入

调用本 Skill 时，Agent 应尽量提供以下信息：

```json
{
  "user_intent": "用户原始分析需求",
  "tables": [
    {
      "name": "表名",
      "description": "表说明",
      "columns": [
        {
          "name": "字段名",
          "type": "字段类型",
          "description": "字段含义"
        }
      ],
      "partition_columns": ["分区字段"],
      "sample_rows": []
    }
  ],
  "constraints": {
    "date_range": "时间范围",
    "filters": ["过滤条件"],
    "metric_definition": "指标口径",
    "performance_preference": "性能优先 / 可读性优先"
  }
}
```

## 输出

本 Skill 输出结构如下：

```json
{
  "sql": "生成的 Spark SQL",
  "explanation": "SQL 逻辑解释",
  "assumptions": ["生成 SQL 时采用的假设"],
  "risk_warnings": ["潜在风险"],
  "optimization_tips": ["优化建议"],
  "next_skill": "spark-job | dag-diagnosis | null"
}
```

## 工作流程

1. 识别用户意图，判断是查询、统计、明细拉取、SQL 优化还是错误排查。
2. 检查输入中是否包含必要表结构、字段说明和分区信息。
3. 如果缺少关键字段，向 Runtime 返回缺失信息，而不是编造字段。
4. 根据指标口径生成 SQL，优先保证语义正确。
5. 添加必要分区过滤，避免无条件全表扫描。
6. 检查 Join 条件、聚合粒度、窗口函数和排序逻辑。
7. 输出 SQL、解释、假设和风险提示。
8. 如果用户需要执行 SQL，将结果交给 SparkJobSkill。
9. 如果用户需要性能分析，将 SQL 或执行计划交给 DAGDiagnosisSkill。

## SQL 生成原则

1. 优先使用显式字段名，不使用 `select *`。
2. 聚合查询必须明确 group by 粒度。
3. 时间条件必须优先使用分区字段。
4. Join 必须明确 Join Key 和 Join 类型。
5. 对大表 Join，要提示 Broadcast Join、预聚合或分桶优化可能性。
6. 对窗口函数，要说明 partition by 和 order by 的业务含义。
7. 对指标计算，要说明分子、分母和过滤条件。

## 示例

用户需求：

```text
统计最近 7 天每个城市的活跃用户数。
```

输入表：

```text
dwd_user_action_di
字段：user_id, city_id, event_time, dt
分区字段：dt
```

输出 SQL：

```sql
select
  city_id,
  count(distinct user_id) as active_user_cnt
from dwd_user_action_di
where dt between '${start_dt}' and '${end_dt}'
group by city_id;
```

输出说明：

```text
该 SQL 使用 dt 分区过滤最近 7 天数据，并按 city_id 聚合，统计去重后的活跃用户数。
```
