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

- `task.py`：定义一次 Runtime 调用的 `AgentTask` 及生命周期（PENDING→RUNNING→SUCCEEDED/FAILED/CANCELLED），Task 与 Session 分离
- `planner.py`：定义版本化 `Plan` / `PlanStep`（含 `id`、`description`、`depends_on` DAG）以及可注入的 `Planner` 协议
- `llm_planner.py`：基于 LLM 判断是否需要规划，解析并校验结构化步骤 DAG（三色 DFS 环路检测）；无效输出降级为直接执行
- `context.py`：维护 Session history、滚动摘要（`WINDOW=12` 轮后触发压缩）、Prompt 组装和会话持久化
- `runtime.py`：Agent 编排入口，通过 `ModelClient` 协议抽象 LLM 客户端，管理 Task 生命周期、可选规划、模型/工具循环（`max_tool_rounds`，默认 8）和最大轮数
- `events.py`：使用 frozen dataclass 定义结构化事件（`TaskStarted`、`PlanCreated`、`TextDelta`、`ToolCompleted`、`TaskCompleted`、`TaskFailed`），Runtime 以 AsyncIterator 输出，UI 消费
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
- TUI 默认启用 `LLMPlanner`；规划和执行复用同一个模型客户端，简单任务可跳过 Plan
- 有效 Plan 作为 system context 注入执行轮次，Planner 不直接执行工具或修改步骤状态
- Tool calling 的规范消息链由 Runtime 记录：assistant(tool_calls) → tool(tool_call_id) → assistant
- Runtime 在构造模型上下文前执行必要的滚动摘要，不静默丢弃未压缩消息
- skill 注入：匹配到 `/skill-name` 时，将该 skill 的完整 `SKILL.md` 作为 system message 插入 API 请求
- 工具调用：`stream_ai_response` 传入 `TOOL_DEFINITIONS` + `execute_tool`，自动处理调用循环
- 内置斜杠命令：`/skills` 列出技能，`/choice` 或 `/history` 进入历史会话选择，`/clear` 清除对话，`/select` 打开文件浏览器
- 文件浏览器 `FileBrowserScreen`：`SPARKOS_REPO_ROOT` 环境变量控制根目录，选中文件后路径填入输入框
- 历史会话懒创建：首次发消息时 `create_session()`，每轮结束 `save_session()`
- `slash-hint` 通过 `self._slash_hint` 实例引用操作，不用 `query_one` 查 ID（避免 `_load_session` 后 widget 失效）
- 事件系统使用 union type `AgentEvent`，frozen dataclass 保证不可变性

## Runtime Environment

- `SPARKOS_REPO_ROOT`：文件浏览器的浏览根目录，默认当前工作目录
- 配置读取路径固定为 `config/config.yaml`（相对项目根目录），`ChatConfig.from_yaml()` 无参调用
- 对话历史存储在 `~/.sparkmind/history/`，不在项目目录内

## work principle

- 每次修改完成Py代码之后，使用ruff检查并format。
- 使用中文回复用户消息。
