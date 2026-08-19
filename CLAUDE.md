# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## work principle

- 每次修改完成 Py 代码之后，使用 ruff check, 不必 ruff format
- 使用中文回复用户消息。

## 架构

数据分析 Agent，CLI/TUI 形态。项目集成了CodeGraph(<https://github.com/colbymchenry/codegraph>) ，可以快速查找代码，不要看文档来确定代码状态，直接查代码即可。
