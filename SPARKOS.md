# SPARKOS

**AI 数据助手 CLI — 基于 Spark 生态的智能分析工作台**

---

## 1. 项目概述

SparkOS（内部代号 sparkmind）是一个面向数据分析场景的 AI Agent CLI 应用。它以 Textual TUI 为核心交互层，将大模型（LLM）能力与 Spark SQL、Spark Job 等数据工程技能封装为可调用的 Skill，让分析师通过自然语言完成从数据查询到任务提交的全流程。

---

## 2. 核心架构

```
sparkos/
├── agent/                  # Agent 核心逻辑
│   ├── skills/             # 技能目录（动态加载）
│   │   ├── spark-sql/      # Spark SQL 生成与优化
│   │   └── spark-job/      # Spark Job 提交与管理
│   ├── tools/              # 通用工具（文件读写、Shell、Web）
│   └── memory.py           # 对话历史持久化（JSON）
├── infrastructure/
│   └── llm/                # LLM 基础设施
│       ├── client.py       # OpenAI 兼容客户端（流式 + 工具循环）
│       └── models.py       # 数据模型（ChatMessage, ChatConfig）
└── ui/
    ├── chat_app.py         # 主界面（Textual）
    └── history_screen.py   # 历史会话管理界面
```

### 2.1 设计原则

| 原则 | 说明 |
|------|------|
| **Skill 驱动** | 所有领域能力以 SKILL.md 形式注册，Agent 按需加载 |
| **流式优先** | 文本流式输出，工具调用异步执行，UI 实时反馈 |
| **分层解耦** | UI、Agent、LLM、Skills 四层独立，可单独替换 |
| **可追溯** | 所有会话持久化到 `~/.sparkmind/history/` |

---

## 3. 模块详解

### 3.1 Agent Skills 系统

Skills 是 SparkOS 的"专业能力插件"，每个 Skill 是一个包含 `SKILL.md` 的目录。

**当前内置 Skills：**

- **spark-sql** — 自然语言转 Spark SQL，支持语法校验、性能风险提示、执行计划解读
- **spark-job** — Spark Job 提交、状态查询、日志解析、失败诊断、重试建议

**加载机制：**
- 启动时扫描 `sparkos/agent/skills/` 目录
- 解析每个 `SKILL.md` 的 YAML frontmatter 提取 description
- 构建 system message 注入对话上下文
- 支持 `/skill-name` 斜杠命令激活特定 Skill

### 3.2 工具注册表（Tools Registry）

提供四个基础工具，所有 Skill 和 Agent 均可调用：

| 工具 | 功能 | 实现 |
|------|------|------|
| `read_file` | 读取本地文件 | Path.read_text |
| `write_file` | 写入本地文件 | Path.write_text（自动创建目录） |
| `shell` | 执行 Shell 命令 | subprocess.run（30s 超时） |
| `web_fetch` | 获取网页内容 | httpx.Client（15s 超时） |

### 3.3 LLM 客户端

- **协议**：OpenAI Chat Completions API（兼容 Ollama、vLLM、LocalAI 等）
- **流式输出**：逐 token yield，实时渲染 Markdown
- **工具循环**：自动处理多轮 tool_calls → 执行 → 回传结果 → 继续生成
- **配置来源**：`config/config.yaml`（base_url, api_key, model）

### 3.4 对话记忆（Memory）

- 存储位置：`~/.sparkmind/history/*.json`
- 会话 ID：`YYYYMMDD-HHMMSS-{uuid6}`
- 持久化内容：完整消息列表（含 tool_calls）、创建时间戳
- 支持会话列表、加载、删除

### 3.5 UI 层（Textual）

**主界面 `ChatApp`：**
- 消息列表（用户消息左对齐，助手 Markdown 右渲染）
- 底部输入框（支持斜杠命令自动补全）
- 工具调用折叠面板（可展开参数与结果）
- 状态栏（思考中/生成中/工具执行中）

**快捷键：**
- `Ctrl+C` 退出
- `Esc` 停止生成
- `/skills` 列出所有技能
- `/history` 查看历史会话
- `/clear` 清空当前对话

---

## 4. 数据流

```
用户输入
    │
    ▼
ChatApp.on_input_submitted
    │
    ├─ 斜杠命令 → 本地处理（/skills, /clear, /history）
    │
    └─ 自然语言 → 构造 ChatMessage
            │
            ▼
        stream_ai_response (client.py)
            │
            ├─ 发送 messages + tools 到 LLM
            │
            ├─ 流式接收文本 → 实时渲染 Markdown
            │
            ├─ 接收 tool_calls → execute_tool() → 显示工具详情
            │
            └─ 工具结果回传 → 继续生成（直到无 tool_calls）
                    │
                    ▼
                保存会话到 memory
```

---

## 5. 配置说明

`config/config.yaml`：

```yaml
api:
  base_url: "http://localhost:20128/v1"
  api_key: "sk-xxxx"
  model: "yuanshennb"

Embedding_api:
  model: "wo/text-embedding-3-large"
  base_url: "http://localhost:20128"
  api_key: "sk-xxxx"
```

> ⚠️ 注意：`api_key` 为示例值，实际使用需替换为有效凭证。

---

## 6. 开发规范

- **语言版本**：Python ≥ 3.14
- **代码检查**：ruff（已配置 `.ruff_cache`）
- **依赖管理**：uv（`uv.lock`）
- **包名**：`sparkmind`（PyPI 发布名），模块名 `sparkos`

### 快速启动

```bash
# 安装依赖
uv sync

# 运行 TUI
python -m sparkos.ui.chat_app
# 或
uv run sparkos
```

---

## 7. 技能扩展指南

如需新增 Skill，在 `sparkos/agent/skills/` 下创建目录，包含 `SKILL.md`：

```markdown
---
name: my-skill
description: 一句话描述技能用途
---

# My Skill

详细说明输入输出格式、工作流程、边界约束。
```

Agent 将自动发现并注入可用技能列表。

---

## 8. 未来规划

- [ ] DAG 诊断 Skill（dag-diagnosis）— Spark DAG 可视化与 Stage 分析
- [ ] 数据质量 Skill（data-quality）— 产出表字段级质量校验
- [ ] 多模态支持 — 图表生成与图片理解
- [ ] RAG 增强 — 表结构元数据向量化检索
- [ ] 分布式会话 — 多用户共享知识库

---

*最后更新：2025-01-*
