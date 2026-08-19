"""动态加载 skills 目录下的所有 SKILL.md。"""

from __future__ import annotations

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


def build_system_messages(
    skills: list[Skill],
    skill_name: str | None = None,
    skills_dir: str = "sparkos/agent/skills",
) -> list[dict]:
    """构造 API 请求所需的 system message 列表。

    包含：
    1. 全局 skill 列表（始终）
    2. 激活技能的 SKILL.md 全文（skill_name 非 None 时）

    Returns:
        list[dict]，每个元素是 {"role": "system", "content": ...}
    """
    messages: list[dict] = []
    global_msg = build_system_message(skills)
    if global_msg:
        messages.append({"role": "system", "content": global_msg})

    if skill_name:
        content = load_skill_content(skill_name, skills_dir)
        if content:
            messages.append(
                {
                    "role": "system",
                    "content": f"当前激活技能：{skill_name}\n\n{content}",
                }
            )

    return messages


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


def infer_skill_name(text: str, skills: list[Skill]) -> str | None:
    """Infer narrowly-scoped skills whose activation requires explicit intent."""
    available = {skill.name for skill in skills}
    if "data-quality-test" not in available:
        return None

    normalized = text.casefold()
    query_terms = ("查询", "查一下", "查下", "检索", "query", "select")
    quality_terms = (
        "质量测试",
        "质量检查",
        "质量检测",
        "数据质量",
        "检查质量",
        "检测质量",
        "分析质量",
        "分析一下质量",
        "quality test",
        "quality check",
        "data quality",
    )
    if any(term in normalized for term in query_terms) and any(
        term in normalized for term in quality_terms
    ):
        return "data-quality-test"
    return None


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
    try:
        import yaml
    except ImportError:
        return ""

    try:
        with open(path, encoding="utf-8") as f:
            raw = f.read()
    except OSError:
        return ""

    if raw.startswith("---"):
        end = raw.find("---", 3)
        if end != -1:
            try:
                frontmatter = yaml.safe_load(raw[3:end]) or {}
            except Exception:  # noqa: BLE001
                return ""
            return frontmatter.get("description", "").strip() if isinstance(frontmatter, dict) else ""

    return ""
