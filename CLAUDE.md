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
- `ChatConfig`：从 `config/config.yaml` 的 `api` 节点读取配置，缺失项回退到默认值
- `OpenAIChatClient`：通过 Responses API `client.responses.create(..., stream=True)` 流式调用，过滤 `response.output_text.delta`
- `stream_ai_response()` 在 UI 层定义，作为统一适配层隔离底层 SDK

**skills 层** — `sparkos/skills/loader.py`
- `load_skills()`：扫描 `skills/` 目录，解析每个 `SKILL.md` 的 YAML frontmatter（name + description）
- `parse_slash_command()`：解析 `/skill-name [message]` 格式，返回 `(skill_name, message)`
- `load_skill_content()`：读取指定 skill 的完整 `SKILL.md` 内容
- skill 动态加载，新增 skill 只需在 `skills/` 下创建目录 + `SKILL.md`，重启即生效

**UI 层** — `sparkos/ui/chat_app.py`
- Textual TUI，`ChatApp` 继承 `App`
- 布局：Header + VerticalScroll(#chat) + Static(#status) + Input(#prompt) + Footer
- 对话历史维护在 `self._history`（`list[ChatMessage]`），传给 API 时截取最近 12 条（6 轮）
- `@work(exclusive=True, group="ai-generation")` 新请求自动取消旧请求
- `Ctrl+C` 退出，`Esc` 取消生成
- 输入 `/` 时在聊天区弹出 skill 列表提示，按 Enter 发送

## Key Patterns

- UI 层不直接 import SDK，全部走 `stream_ai_response()`
- 修改底层接口只需改 `openai_compatible.py`
- skill 注入方式：匹配到 `/skill-name` 时，将该 skill 的完整 `SKILL.md` 内容作为 system message 插入到 API 请求中
- `/skills` 命令直接列出所有 skill，不走 API，不进入对话历史
- 配置只读 `config/config.yaml`
