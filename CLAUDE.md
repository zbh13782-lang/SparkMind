# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Commands

```bash
# 运行 TUI
.venv/bin/python main.py

# 运行测试（单个测试：.venv/bin/python -m pytest tests/test_scheduler.py::test_xxx）
.venv/bin/python -m pytest tests/

# Lint + format
.venv/bin/ruff check sparkos/ tests/
.venv/bin/ruff format sparkos/ tests/

# 本地 Spark 集群（master + worker + history server）
docker compose up -d
# Master UI: localhost:8080，History Server: localhost:18080
# Hive Metastore 默认不启动，需要时：docker compose --profile hive up -d
# 事件日志落在宿主 ./artifacts/spark-events（History Server 从挂载卷读取）
```

## Architecture

请求路径：`ChatApp` → `AgentRuntime.run()` → `LLMPlanner` 出 Plan → `PlanScheduler` 选步
→ `StepExecutor` 跑模型/工具循环 → 汇总成最终答案。UI 只消费 Runtime 吐出的事件流。

**config 层** — `config/config.py`

- `load()` / `get_chat_config()` / `get_runtime_config()`，返回 frozen dataclass
  `ChatConfig(base_url, api_key, model)` 和 `RuntimeConfig(max_tool_rounds, max_replans, max_steps, context_window)`
- 读取路径固定 `config/config.yaml`（相对项目根）。**直接下标取键，缺键抛 `KeyError`，没有默认值兜底**
- 每次调用都重新读盘，无缓存
- `config.yaml` 里的 `Embedding_api` 节点目前无任何代码引用（死配置）

**infrastructure 层** — `sparkos/infrastructure/`

- `llm/client.py` `OpenAIChatClient`：包 `AsyncOpenAI`
  - `chat_once(messages: list[dict], *, json_object=False) -> str` — 非流式，供规划/摘要用
  - `chat_stream(messages: list[ChatMessage], tools=None) -> AsyncIterator[str | ToolCall]` —
    流式过程中 yield 文本 delta（`str`），按 `tc_delta.index` 累积 tool_calls，**流结束后**才 yield 完整 `ToolCall`
  - 模块级 `stream_ai_response()` 是遗留兼容包装，agent 层已不再使用
- `llm/models.py`：只有 `ChatMessage` 和 `ToolCall` 两个 dataclass，`to_api_dict()` 省略空字段。`ChatConfig` 不在这里
- `persistence/task_store.py` `JsonTaskStore`：Task 快照原子写入，replan 时归档旧 Plan/StepRuns，拒绝会逃逸目录的 task_id

**agent 层** — `sparkos/agent/`

- `task.py`：`AgentTask` 生命周期 `PENDING→PLANNING→RUNNING→SUCCEEDED/FAILED/CANCELLED`，
  外加 `WAITING_INPUT`（澄清分支）。Task 与 Session 分离
- `planner.py`：不可变 `Plan` / `PlanStep`（`id`、`description`、`depends_on` DAG、`success_criteria`）、
  `ClarificationRequest`，以及可注入的 `Planner` / `Replanner` 协议
- `llm_planner.py`：LLM 生成初始 Plan，或在失败后生成 `version + 1` 的完整替代 Plan；校验 DAG、步数上限、成功标准。
  信息不足时返回 `ClarificationRequest` 而非 Plan
- `context.py`：Session history、滚动摘要、Prompt 组装、会话持久化。系统提示词来自同目录 `system_prompt.md`
- `step.py`：可变 `StepRun` 执行状态与局部 transcript，不可变 `StepResult` / `ArtifactRef`。
  `StepStatus` = PENDING/RUNNING/SUCCEEDED/FAILED/BLOCKED/CANCELLED
- `scheduler.py`：纯 DAG 逻辑，无 I/O。`ready_steps()` 返回所有就绪步骤，但 Runtime 只取 `[0]` 串行执行；
  `block_failed_dependents()` 传播失败
- `step_executor.py`：单个 Step 的有界模型/工具循环（`max_tool_rounds`，Runtime 从 config 注入）；
  工具成功后最终文本为空时基于已有结果重试一次；Step 的 assistant/tool 消息只存在于局部 transcript
- `runtime.py`：编排规划、步骤执行、最多一次 replan、快照持久化、最终汇总。Session history 只记录用户输入与最终答案
- `task_store.py`：只是一个带 `save` 方法的 `Protocol`，实现在 `infrastructure/persistence/`
- `events.py`：frozen dataclass 定义的 Task/Plan/Step 事件，union type `AgentEvent`。
  注意 `ToolCompleted` 已死（定义了但 Runtime 从不 yield，实际发的是 `StepToolCompleted`）
- `tools/registry.py`：四个通用工具（`read_file` / `write_file` / `shell` / `web_fetch`）及 OpenAI function schema；
  `execute_tool` 按 name 分发
- `skills/loader.py`：`load_skills` / `build_system_message` / `load_skill_content` / `parse_slash_command` / `SkillSuggester`。
  现有 skill：`spark-job`、`spark-sql`
- `memory.py`：对话持久化 `~/.sparkmind/history/{session_id}.json`，懒创建

**UI 层** — `sparkos/ui/`

- `chat_app.py`：Textual TUI。布局 Header → `Horizontal#workspace`(对话区 + `RuntimePanel` 侧栏) →
  `Static#status` → `Input#prompt` → `Static#slash-hint` → Footer。
  宽度 < 100 时给 `#workspace` 加 `.compact` 类把侧栏改为堆叠
- `runtime_panel.py`：**`RuntimeTrace`（纯状态机，不依赖 Textual）与 `RuntimePanel(Static)`（widget）分离**，
  所以 `test_runtime_panel.py` 不用起 App 就能测。`RuntimeTrace.apply(event)` 把每个 `AgentEvent`
  折叠成任务状态、阶段条、步骤列表、`deque(maxlen=7)` 活动日志和 token 累计；
  `RuntimePanel` 构造时 `markup=False`，防止模型文本注入 Rich markup
- `tool_summary.py`：`ToolCallSummary(Collapsible)`，每个任务一个，把所有工具调用折叠进单个展开区，
  参数/结果各截断 500 字符。首次 `StepToolCompleted` 时懒创建
- `history_screen.py` / `file_browser_screen.py`：历史会话选择、文件浏览器

## Key Patterns

- UI 层不直接 import SDK，只消费 Runtime 事件
- `runtime.py` 定义 `ModelClient` 协议抽象 LLM 客户端，方便测试 mock。
  `chat_once` 带关键字参数 `json_object`（`LLMPlanner` 规划时传 `True` 走 JSON 模式），
  写 mock 时别漏——漏了会被 `LLMPlanner` 的 fail-open 吞掉，表现为规划静默返回 `None`
- `LLMPlanner` 的 `create_plan` / `revise_plan` **对所有异常 fail-open 返回 `None`**（规划是可选优化，
  不能阻断任务执行）。副作用是规划期的 bug 不会冒泡，只会退化成单步 `direct` Plan——
  调试规划问题时先把那个 `except Exception` 临时打开
- `_parse_payload` 会剥掉 markdown 代码围栏：部分模型即使被要求 `json_object` 仍会裹 ```` ```json ````
- TUI 默认 `enable_planning=True`；规划和执行复用同一个模型客户端，简单任务用合成的单步 `direct` Plan
- Planner 只产生不可变步骤定义；Runtime/Scheduler 通过 `StepRun` 管理执行状态
- **Step 失败不重试**：`run.fail()` 后直接走 replan-or-raise。`StepRun.attempt_count` 存在但恒为 1
- **replan 硬上限 1 次**，三处强制：config 校验 `max_replans > 1` 直接抛错、`replan_count < max_replans` 门控。
  `_revise_plan` 会以 5 个理由拒绝替代 Plan（task_id 不符、Plan id 重复、version 非 +1、source 非 `replan`、
  失败步骤 id 被复用）；`_reconcile_step_runs` 只在步骤定义完全相同时保留已成功的 run
- 澄清分支：Planner 返回 `ClarificationRequest` → Task 转 `WAITING_INPUT` → 发 `ClarificationRequested` 事件后**直接 return**，不执行任何步骤
- Runtime 事件流被取消或提前关闭（`GeneratorExit` / `CancelledError`）时，活跃 Task/Step 转 `CANCELLED` 并尽力保存快照
- Tool calling 的规范消息链由 StepExecutor 局部维护：assistant(tool_calls) → tool(tool_call_id) → assistant
- **`<tool_call>` 文本泄漏防御**：部分模型会把工具调用当普通文本吐出来。`_is_textual_tool_call` 检测，
  `_fallback_answer` 倒序找最后一个有效步骤输出兜底；汇总阶段还会缓冲开头的 delta，
  确认不是 `<tool_call` 前缀才开始真正 yield
- 上下文压缩：`needs_compact()` 是 `len(history) - summary_upto > WINDOW`（WINDOW 来自 config，非硬编码），
  但真正的闸门是 `messages_to_compact()`——**它把截断点前推到 `user` 角色边界，找不到就返回 `[]`**，
  所以进行中的超长轮次不会被压缩。每个 task 只在开头调一次，不是每次模型调用
- skill 注入：匹配到 `/skill-name` 时，把该 skill 完整 `SKILL.md` 作为 system message 插入请求
- 内置斜杠命令共 5 个：`/skills`、`/choice`、`/history`（`/choice` 的别名）、`/clear`、`/select`
- `slash-hint` 通过 `self._slash_hint` 实例引用操作，不用 `query_one` 查 ID（避免 `_load_session` 后 widget 失效）
- 历史会话懒创建：首次发消息 `ensure_session()`，每轮结束 `persist()`
- `@work(exclusive=True, group="ai-generation", exit_on_error=False)` 新请求自动取消旧请求；
  `Ctrl+C` 退出，`Esc` 取消生成

**utils 层** — `utils/_tiktoken.py`

- `count_text(text, model)` / `count_messages(messages, model)`，后者含消息 overhead 和 tool_calls。
  按模型缓存 encoding，找不到回退 `cl100k_base`。`tiktoken` 在函数内惰性 import

## Runtime Environment

- `SPARKOS_REPO_ROOT`：一个变量管两处——文件浏览器的浏览根目录，以及 docker-compose 把项目挂进容器 `/opt/sparkos` 的绑定挂载。默认当前工作目录
- `load_skills()` 默认扫描**相对路径** `sparkos/agent/skills`，从别的 cwd 启动会静默返回 `[]`
  （对比 `context.py` / `memory.py` 用 `Path(__file__).resolve()` 锚定）
- 对话历史存 `~/.sparkmind/history/`；Task 执行快照存项目内 `.sparkmind/tasks/{task_id}.json`
- 需要 Python ≥ 3.14（`memory.py` 用了 PEP 758 的无括号多异常 `except` 语法）

## Spark 本地集成（进行中，见 docs/superpowers/plans/2026-08-06-local-docker-spark-integration.md）

- docker-compose 四个服务：`spark-master`(7077/8080)、`spark-worker`(仅 expose 8081，故意不映射宿主端口以便扩容)、
  `spark-history`(18080)、`hive`(10000/10002，`profiles: [hive]` 默认不启)
- 通过 `spark://spark-master:7077` 提交；容器内可直接
  `docker exec sparkos-spark-master /opt/spark/bin/spark-submit ...`
- `agent/skills/spark-job` 和 `spark-sql` 负责生成提交命令、查询状态、解析日志；作业产物约定落在 `artifacts/`

## work principle

- 每次修改完成 Py 代码之后，使用 ruff 检查并 format。
- 使用中文回复用户消息。
