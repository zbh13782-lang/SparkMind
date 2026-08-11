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

## 代码沙箱

构建隔离镜像：

```bash
docker build --pull -f docker/code-sandbox.Dockerfile -t sparkmind-code-sandbox:latest docker
```

`run_code` 工具在一次性 Docker 容器中执行 Python 或 Bash，具备以下隔离特性：

- 无网络（`--network none`）
- 只读根文件系统（`--read-only`），仅挂载当前运行目录（`artifacts/code-runs/<run_id>/`）为只读
- 无宿主机仓库或项目源码挂载、无 Docker socket、无 home 目录
- 所有 capabilities 丢弃（`--cap-drop ALL`）、`no-new-privileges`
- 资源上限：256 MiB 内存、1 CPU、64 进程、30 秒超时

完整日志保存在 `artifacts/code-runs/<run_id>/output.log`，模型可见输出上限 20,000 字节。

## Advisor

`config/config.yaml` 中 advisor 使用语义别名 `advisor`，由 OpenAI 兼容网关映射到高能力模型。默认复用主 `api.base_url` 和 `api.api_key`，可通过环境变量覆盖：

```bash
export SPARKMIND_ADVISOR_ENABLED=true
export SPARKMIND_ADVISOR_MODEL=advisor
export SPARKMIND_ADVISOR_BASE_URL=http://advisor-gateway/v1
export SPARKMIND_ADVISOR_API_KEY=advisor-key
```

每个 Plan Step 最多调用一次 `ask_advisor`。Advisor 接收无工具请求，输出是建议，必须通过工具或已有证据验证后再采用。
