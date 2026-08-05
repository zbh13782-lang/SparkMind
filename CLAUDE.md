# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Commands

```bash
# 运行 TUI
.venv/bin/python main.py

# 运行测试
.venv/bin/python -m pytest tests/

# Lint + format
.venv/bin/ruff check sparkos/ tests/
.venv/bin/ruff format sparkos/ tests/
```

## Architecture

**infrastructure 层** — `sparkos/infrastructure/llm/client.py` + `models.py`

- `ChatConfig`：从 `config/config.yaml` 的 `api` 节点读取 `base_url` / `api_key` / `model`，默认值指向 Ollama 本地地址
- `OpenAIChatClient`：使用 OpenAI Chat Completions API `client.chat.completions.create(..., stream=True)` 流式调用
- `models.py` 同时定义了跨层共享的数据结构：`ChatMessage`、`ChatChoice`、`ToolCall`、`FunctionCall`
- Client 只负责单次模型轮次和协议转换，不执行工具、不维护 Agent 状态

**agent 层** — `sparkos/agent/`

- `task.py`：定义一次 Runtime 调用的 `AgentTask` 及生命周期（PENDING→PLANNING→RUNNING→SUCCEEDED/FAILED/CANCELLED），Task 与 Session 分离
- `planner.py`：定义不可变 `Plan` / `PlanStep`（`id`、`description`、`depends_on` DAG、`success_criteria`）以及可注入的 `Planner` / `Replanner` 协议
- `llm_planner.py`：基于 LLM 生成初始 Plan 或在失败后生成 `version + 1` 完整替代 Plan，并校验步骤 DAG 与成功标准
- `context.py`：维护 Session history、滚动摘要（`WINDOW=12` 轮后触发压缩）、Prompt 组装和会话持久化
- `step.py`：定义独立于 Plan 的可变 `StepRun` 执行状态、尝试历史与局部 transcript，以及不可变 `StepResult` / `StepVerification` / `ArtifactRef`
- `verifier.py`：基于 LLM 的严格 JSON 判断 StepResult 是否满足 `success_criteria`；验证器自身或解析异常会记录 `error` 并兼容放行
- `retry.py`：确定性有界重试策略，默认每个 Step 最多 2 次尝试
- `scheduler.py`：纯 DAG 调度逻辑；第一阶段按 Plan 顺序串行选取依赖已完成的 Step，并传播依赖失败
- `step_executor.py`：执行单个 Step 的有界模型/工具循环（`max_tool_rounds`，默认 8）；工具成功后的最终文本为空时会基于已有结果重试一次；Step 的 assistant/tool 消息只存在于局部 transcript
- `runtime.py`：编排 Task 规划、Step 执行/验证、有界重试、最多一次 replan、快照持久化和最终汇总；Session history 只记录用户输入与最终答案
- `task_store.py` + `infrastructure/persistence/task_store.py`：Task 原子 JSON 快照，存储尝试/验证/transcript 历史；replan 时归档旧 Plan 和 StepRuns
- `events.py`：使用 frozen dataclass 定义 Task/Plan/Step 结构化事件，Runtime 以 AsyncIterator 输出，UI 消费
- `skills/loader.py`：扫描 `agent/skills/` 目录，解析每个 `SKILL.md` 的 YAML frontmatter 提取 description；`build_system_message` 将所有 skill 描述拼成 system message，匹配到 `/skill-name` 时额外注入完整 SKILL.md 内容
- `tools/registry.py`：定义四个通用工具（`read_file` / `write_file` / `shell` / `web_fetch`）及其 OpenAI function schema；`execute_tool` 根据 tool_call 的 name 分发执行
- `memory.py`：对话持久化，`~/.sparkmind/history/{session_id}.json`，懒创建（首次发送消息时才生成文件）

**UI 层** — `sparkos/ui/chat_app.py`

- Textual TUI，`ChatApp` 继承 `App`
- 布局：Header + VerticalScroll(#chat) + Static(#status) + Input(#prompt) + slash-hint + Footer
- `slash-hint`：输入 `/` 时在聊天区下方弹出匹配的内置命令和 skill 列表，通过 `self._slash_hint` 实例引用操作（不用 `query_one`，避免 `_load_session` 后 widget 失效）
- UI 为每次输入创建 `AgentTask`，消费 `AgentRuntime.run()` 输出的文本、计划和工具事件
- `@work(exclusive=True, group="ai-generation")` 新请求自动取消旧请求
- `Ctrl+C` 退出，`Esc` 取消生成
- `history_screen.py`：历史会话选择界面

## Key Patterns

- UI 层不直接 import SDK，只消费 Runtime 事件
- `runtime.py` 定义 `ModelClient` 协议抽象 LLM 客户端，方便测试时 mock 替换
- TUI 默认启用 `LLMPlanner`；规划和执行复用同一个模型客户端，简单任务使用合成的单步 `direct` Plan
- Planner 只产生不可变步骤定义；Runtime/Scheduler 通过 `StepRun` 管理执行状态
- Step 成功路径为 `RUNNING → VERIFYING → SUCCEEDED`；验证拒绝时最多重试一次，仍失败时最多 replan 一次
- 两次 Step 尝试和一次 replan 是 Runtime 硬上限；替代 Plan 必须使用新 Plan id、新版本和 `replan` source，失败步骤的替代定义必须使用新 Step id
- Runtime 事件流被取消或提前关闭时，活跃 Task/Step 会转为 `CANCELLED` 并尽力保存快照
- Tool calling 的规范消息链由 StepExecutor 局部维护：assistant(tool_calls) → tool(tool_call_id) → assistant
- Runtime 在构造模型上下文前执行必要的滚动摘要，不静默丢弃未压缩消息
- skill 注入：匹配到 `/skill-name` 时，将该 skill 的完整 `SKILL.md` 作为 system message 插入 API 请求
- 工具调用：`stream_ai_response` 传入 `TOOL_DEFINITIONS` + `execute_tool`，自动处理调用循环
- 内置斜杠命令：`/skills` 列出技能，`/choice` 或 `/history` 进入历史会话选择，`/clear` 清除对话，`/select` 打开文件浏览器
- 文件浏览器 `FileBrowserScreen`：`SPARKOS_REPO_ROOT` 环境变量控制根目录，选中文件后路径填入输入框
- 历史会话懒创建：首次发消息时 `create_session()`，每轮结束 `save_session()`
- `slash-hint` 通过 `self._slash_hint` 实例引用操作，不用 `query_one` 查 ID（避免 `_load_session` 后 widget 失效）
- 事件系统使用 union type `AgentEvent`，frozen dataclass 保证不可变性

**utils 层** — `utils/`

- `_tiktoken.py`：基于 `tiktoken` 的 token 计数工具；`count_text(text, model)` 统计纯文本 token 数，`count_messages(messages, model)` 统计 `ChatMessage` 列表的 token 数（含消息 overhead 和 tool_calls）。找不到模型编码时自动回退到 `cl100k_base`

## Runtime Environment

- `SPARKOS_REPO_ROOT`：文件浏览器的浏览根目录，默认当前工作目录
- 配置读取路径固定为 `config/config.yaml`（相对项目根目录），`ChatConfig.from_yaml()` 无参调用
- 对话历史存储在 `~/.sparkmind/history/`；Task 执行快照存储在项目 `.sparkmind/tasks/{task_id}.json`

## work principle

- 每次修改完成Py代码之后，使用ruff检查并format。
- 使用中文回复用户消息。
