# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Commands

```bash
# 运行
.venv/bin/python main.py

# Lint
.venv/bin/ruff check sparkos/
```

## Architecture

**infrastructure 层** — `sparkos/infrastructure/openai_compatible.py`

- `ChatConfig`：从 `config/config.yaml` 的 `api` 节点读取 `base_url` / `api_key` / `model`
- `OpenAIChatClient`：使用 Chat Completions API `client.chat.completions.create(..., stream=True)` 流式调用
- 支持工具调用循环：模型返回 `tool_calls` → 本地执行 → 结果发回模型 → 获取最终回复
- `stream_ai_response()` 在 UI 层定义，作为统一适配层隔离底层 SDK

**agent 层** — `sparkos/agent/`

- `skills/loader.py`：扫描 `agent/skills/` 目录，解析 `SKILL.md` 的 YAML frontmatter
- `tools/registry.py`：定义通用工具（read_file / write_file / shell / web_fetch）及其 OpenAI function schema
- `memory.py`：对话持久化，`~/.sparkmind/history/{session_id}.json`，懒创建（首次发送消息时才生成文件）

**UI 层** — `sparkos/ui/chat_app.py`

- Textual TUI，`ChatApp` 继承 `App`
- 布局：Header + VerticalScroll(#chat) + Static(#status) + Input(#prompt) + Footer
- `slash-hint`：输入 `/` 时在聊天区弹出匹配的 skill 列表，通过 `self._slash_hint` 实例引用操作
- 对话历史维护在 `self._history`，传给 API 时用 `_api_messages()` 过滤并截取最近 12 条
- `@work(exclusive=True, group="ai-generation")` 新请求自动取消旧请求
- `Ctrl+C` 退出，`Esc` 取消生成

## Key Patterns

- UI 层不直接 import SDK，全部走 `stream_ai_response()`
- 修改底层接口只需改 `openai_compatible.py`
- skill 注入：匹配到 `/skill-name` 时，将该 skill 的完整 `SKILL.md` 作为 system message 插入 API 请求
- 工具调用：`stream_ai_response` 传入 `TOOL_DEFINITIONS` + `execute_tool`，自动处理调用循环
- `/skills` 列出所有 skill，`/choice` 进入历史会话选择界面，`/clear` 清除对话
- 历史会话懒创建：首次发消息时 `create_session()`，每轮结束 `save_session()`
- `slash-hint` 通过 `self._slash_hint` 实例引用操作，不用 `query_one` 查 ID（避免 `_load_session` 后 widget 失效）
