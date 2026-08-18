# SparkMind

数据分析 Agent，边想边写。

本地 Spark 集群 + Docker 沙箱 + LLM 规划执行，以 Textual TUI 作为主交互界面。

## 快速开始

```bash
# 依赖
python >=3.14, pip install -e .

# 启动 Spark 集群
mkdir -p artifacts/spark-events artifacts/spark-jobs
docker compose up -d spark-master spark-worker spark-history

# 构建代码沙箱镜像（可选）
docker build --pull -f docker/code-sandbox.Dockerfile -t sparkmind-code-sandbox:latest docker

# 运行 Agent（自动跑 preflight 健康检查）
.venv/bin/python main.py
```

预置服务：Spark Master UI <http://localhost:8080> / History UI <http://localhost:18080>。

## 配置

`config/config.yaml` 主配置；Advisor 可通过 `SPARKMIND_ADVISOR_*` 环境变量覆盖（见 README 原有章节）。

## 架构

```
main.py
 └─ preflight 健康检查（LLM 硬退出；Docker/Advisor 降级继续）
 └─ sparkos.ui.chat_app.ChatApp（Textual TUI）
      └─ AgentRuntime.run — async generator，逐事件 yield

sparkos/
 ├─ agent/          # 核心链路
 │   ├─ context.py      # AgentContext：对话历史 + 滚动摘要压缩
 │   ├─ events.py       # AgentEvent 类型体系
 │   ├─ planner.py      # Plan（DAG）/ PlanStep / Planner(Protocol)
 │   ├─ llm_planner.py  # LLMPlanner：LLM 输出 JSON → Plan
 │   ├─ scheduler.py    # PlanScheduler：依赖图就绪判断
 │   ├─ step.py         # StepRun / StepResult / StepStatus
 │   ├─ step_executor.py # StepExecutor：assistant ↔ tool 多轮循环
 │   ├─ runtime.py      # AgentRuntime：编排 Planner/Scheduler/StepExecutor
 │   ├─ task.py         # AgentTask 生命周期
 │   ├─ task_store.py   # TaskStore(Protocol)
 │   ├─ skills/         # 内置技能（spark-job, spark-sql）
 │   └─ tools/
 │       └─ registry.py # TOOL_DEFINITIONS + execute_tool
 ├─ infrastructure/
 │   ├─ llm/            # OpenAIChatClient（OpenAI 兼容网关）
 │   ├─ spark/          # SparkJobRunner：临时 spark-client 容器
 │   ├─ code_sandbox/   # CodeSandboxRunner：无网络一次性容器
 │   ├─ advisor/        # AdvisorService：高能力模型建议（限次）
 │   └─ persistence/    # JsonTaskStore：task/plan/step_runs 落盘
 └─ ui/                # Textual 界面（chat_app, history_screen, runtime_panel...）
```

## 执行链路

`AgentRuntime.run` 是单任务的核心编排入口，产出 async event stream：

1. **规划**：`Planner.create_plan`（LLM 输出 JSON）生成 `Plan`（DAG）。Planner 失败或输出非法 → `create_direct_plan` 单步直跑。也可能返回 `ClarificationRequest` 挂起等输入。
2. **调度**：`PlanScheduler` 按依赖图挑出就绪步骤；失败步骤下游自动 block。
3. **执行**：`StepExecutor.stream` 跑单步——assistant ↔ tool 多轮循环（上限 `max_tool_rounds`），产出 `StepTranscriptUpdate` / `StepToolExecution` / 最终 `StepExecution`。
4. **重规划**：步骤失败且 `Replanner` 可用时，最多重规划 `max_replans=1` 次，已成功步骤结果保留。
5. **汇总**：全部完成后 `_synthesize_final` 汇总步骤结果生成最终回答。

## 工具

| 工具 | 说明 |
|------|------|
| `read_file` / `write_file` | 本地文件读写 |
| `web_fetch` | 获取网页文本 |
| `run_spark_job` | Docker Spark 集群同步执行 SQL / PySpark |
| `run_code` | 无网络只读容器执行 Python / Bash，日志 `artifacts/code-runs/<run_id>/` |
| `ask_advisor` | 调用高能力模型获取建议，每步限次数 |

工具注册在 `sparkos/agent/tools/registry.py`：`TOOL_DEFINITIONS`（OpenAI function-calling schema）+ `execute_tool` 分发。新增工具时定义必须是裸 dict。

## 测试

```bash
.venv/bin/python -m pytest tests/ -q
.venv/bin/python -m pytest tests/test_agent_runtime.py -q
.venv/bin/python -m pytest tests/test_scheduler.py::TestPlanScheduler::test_ready_steps -q
```

异步测试继承 `unittest.IsolatedAsyncioTestCase`；类名必须 `Test*` 开头。历史踩坑见 `docs/mistakes.md`。

## Spark 测试数据

仓库提供可扩展的零售测试数据生成器，包含分区 CSV、嵌套 JSON、数据质量异常，以及装载后的持久 Hive 表：

```bash
.venv/bin/python scripts/generate_spark_test_data.py --preset small
```

详细表结构、Hive 装载命令、规模档位和分析题见 [Spark 测试数据集](docs/spark-test-data.md)。
