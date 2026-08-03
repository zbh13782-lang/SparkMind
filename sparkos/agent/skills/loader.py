"""动态加载 skills 目录下的所有 SKILL.md。"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


@dataclass
class Skill:
    name: str
    description: str
    path: Path


def load_skills(skills_dir: str = "sparkos/agent/skills") -> list[Skill]:
    """扫描 skills/ 目录，返回所有 skill 的 name、description。"""
    base = Path(skills_dir)
    result: list[Skill] = []

    if not base.is_dir():
        return result

    for entry in base.iterdir():
        if not entry.is_dir():
            continue
        skill_md = entry / "SKILL.md"
        if not skill_md.is_file():
            continue

        name = entry.name
        description = _parse_description(skill_md)
        result.append(Skill(name=name, description=description, path=skill_md))

    return result


def build_system_message(skills: list[Skill]) -> str:
    """将 skill 列表拼成 system message。"""
    if not skills:
        return ""

    lines = ["可用技能："]
    for s in skills:
        lines.append(f"- {s.name}: {s.description}")
    lines.append("")
    lines.append("根据用户需求，你可以引用上述技能来回答问题。")
    return "\n".join(lines)


def load_skill_content(name: str, skills_dir: str = "sparkos/agent/skills") -> str | None:
    """读取指定 skill 的完整 SKILL.md 内容。"""
    path = Path(skills_dir) / name / "SKILL.md"
    if not path.is_file():
        return None
    return path.read_text(encoding="utf-8")


def parse_slash_command(text: str, skills: list[Skill]) -> tuple[str | None, str]:
    """解析 /skill-name [message] 格式。

    返回 (skill_name, message)。如果没有匹配的 skill，返回 (None, text)。
    """
    if not text.startswith("/"):
        return None, text

    parts = text.split(maxsplit=1)
    skill_name = parts[0][1:]  # 去掉开头的 /
    skill_names = {s.name for s in skills}

    if skill_name not in skill_names:
        return None, text

    message = parts[1] if len(parts) > 1 else ""
    return skill_name, message


class SkillSuggester:
    """为 Input 提供 skill 名称的自动补全。"""

    def __init__(self, skills: list[Skill]) -> None:
        self._skills = skills

    def get_suggestions(self, value: str) -> list[str]:
        """返回匹配当前输入的 skill 名称列表（带 / 前缀）。"""
        if not value.startswith("/"):
            return []
        prefix = value[1:].casefold()
        return [f"/{s.name}" for s in self._skills if s.name.casefold().startswith(prefix)]


def _parse_description(path: Path) -> str:
    """从 SKILL.md 的 YAML frontmatter 提取 description。"""
    import yaml

    with open(path, encoding="utf-8") as f:
        raw = f.read()

    if raw.startswith("---"):
        end = raw.find("---", 3)
        if end != -1:
            frontmatter = yaml.safe_load(raw[3:end]) or {}
            return frontmatter.get("description", "").strip()

    return ""
