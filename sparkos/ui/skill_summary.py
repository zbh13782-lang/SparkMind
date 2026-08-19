"""Nested disclosure widget for one task's activated skills."""

from __future__ import annotations

from rich.text import Text
from textual.containers import Vertical
from textual.widgets import Collapsible, Static

from sparkos.agent.skills.loader import Skill


class SkillActivationSummary(Collapsible):
    """Group activated skills behind one task-level disclosure."""

    def __init__(self) -> None:
        self._skill_list = Vertical(classes="skill-list")
        self._count = 0
        super().__init__(
            self._skill_list,
            title="正在激活技能（0）",
            collapsed=True,
            collapsed_symbol="",
            expanded_symbol="",
            classes="skill-summary",
        )

    async def add_skill(self, skill: Skill, step_id: str | None = None, source: str = "rule") -> None:
        self._count += 1
        detail = Text("说明:", style="bold")
        detail.append(f" {skill.description or '（暂无说明）'}\n")
        if step_id:
            detail.append("步骤:", style="bold")
            detail.append(f" {step_id}\n")
        detail.append("来源:", style="bold")
        detail.append(f" {'LLM Planner' if source == 'planner' else '规则触发'}")
        title = f"{self._count}. {skill.name}"
        if step_id:
            title += f" · {step_id}"
        await self._skill_list.mount(
            Collapsible(
                Static(detail, classes="skill-detail", markup=False),
                title=title,
                collapsed=True,
                collapsed_symbol="",
                expanded_symbol="",
                classes="skill-call",
            )
        )
        self.title = f"正在使用技能（{self._count}）"

    def complete(self) -> None:
        self.title = f"激活了 {self._count} 个技能"


__all__ = ["SkillActivationSummary"]
