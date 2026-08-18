# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## work principle

- 每次修改完成 Py 代码之后，使用 ruff 检查并 format（`ruff check --fix . && ruff format .`，line-length=120）。
- 使用中文回复用户消息。

## 架构

数据分析 Agent，CLI/TUI 形态。分层：`sparkos/agent`（核心链路）→ `sparkos/infrastructure`（外部能力）→ `sparkos/ui`（Textual 界面）。入口 `main.py` 做 preflight（LLM 失败硬退出，Docker/Advisor 降级继续）后启动 `ChatApp`。

**单任务执行链路**（`sparkos/agent/runtime.py` 的 `AgentRuntime.run`，async generator 逐事件 yield）：

1. `Planner`（`llm_planner.py`，LLM 输出 JSON）产出 `Plan`（DAG，`PlanStep.depends_on` 声明依赖；planner 失败/输出非法 → `create_direct_plan` 单步直跑）。Planner 也可能返回 `ClarificationRequest`，任务挂起等用户输入。
2. `PlanScheduler` 按依赖图挑出就绪步骤，失败步骤的下游会被 block。
3. `StepExecutor.stream` 执行单步：assistant ↔ tool 多轮循环（上限 `max_tool_rounds`），产出 `StepTranscriptUpdate` / `StepToolExecution` / 最终 `StepExecution`。
4. 步骤失败且有 `Replanner` 时重规划（最多 `max_replans=1` 次），`_reconcile_step_runs` 保留已成功步骤的结果。
5. 全部完成后 `_synthesize_final` 汇总各步骤结果生成最终回答。

**关键契约**：

- 状态持久化：`AgentContext`（对话历史，滚动摘要压缩）+ `JsonTaskStore`（task/plan/step_runs 落盘）。每个状态变更点都 `_save_task`。
- 工具注册在 `sparkos/agent/tools/registry.py`：`TOOL_DEFINITIONS`（OpenAI function-calling schema 列表）+ `execute_tool` 分发。工具：read_file / write_file / web_fetch / run_spark_job / run_code / ask_advisor。**新增工具时定义必须是裸 dict，和其他元素保持一致**（混 tuple 会炸 `.get`，见 docs/mistakes.md）。
- `ask_advisor` 每步限调用次数（`max_advisor_calls_per_step`），Advisor 输出只是建议，须工具验证后采用。
- `run_spark_job` 每次起临时 spark-client 容器跑；`run_code` 在无网络、只读 rootfs 的一次性容器里跑，日志在 `artifacts/code-runs/<run_id>/`。
- Runtime 上限参数都在 `config/config.yaml` 的 `runtime:` 段。

## 测试原则（tests/README.md）

- 只测关键流程和回归；曾经出现过的 bug 必须补针对性测试。
- 异步测试继承 `unittest.IsolatedAsyncioTestCase`；测试类名必须 `Test*` 开头，否则 pytest 不收集。
- 历史踩坑见 `docs/mistakes.md`，改相关代码前先翻一下。
