# SparkMind

data分析agent，作为子agent去用。边想边写，有了新的想法继续改。

TUI的处理更好调试一些。

## 本地 Spark

启动 Spark 集群：

```bash
mkdir -p artifacts/spark-events artifacts/spark-jobs
docker compose up -d spark-master spark-worker spark-history
```

运行 Agent：

```bash
.venv/bin/python main.py
```

Agent 的 `run_spark_job` 工具会为每次调用启动临时 `spark-client` 容器。Driver 在该容器运行，Executor 在 `spark-worker` 运行，完成后临时容器自动删除。

- Master UI: <http://localhost:8080>
- History UI: <http://localhost:18080>
- 作业日志: `artifacts/spark-jobs/<job_id>/spark.log`
