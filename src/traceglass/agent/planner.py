"""Plan generation contracts for the analyst agent.

The planner is deliberately provider-neutral.  A model-backed planner can
implement the same output contract later; the deterministic fallback makes the
execution tree testable and gives the UI an immediate, honest plan preview.
"""

from __future__ import annotations

from dataclasses import dataclass
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field

from traceglass.domain.models import AgentRole


class PlanStep(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    id: str = Field(min_length=1)
    title: str = Field(min_length=1)
    role: AgentRole
    objective: str = Field(min_length=1)
    depends_on: list[str] = Field(default_factory=list)
    completion_criteria: list[str] = Field(default_factory=list)
    allow_subagent: bool = False


class AnalysisPlan(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    id: str = Field(min_length=1)
    objective: str = Field(min_length=1)
    sample_ids: list[str] = Field(default_factory=list)
    steps: list[PlanStep] = Field(min_length=1)
    strategy: str = "plan-execute-with-bounded-react"


class Planner:
    """Build a conservative five-stage reverse-analysis plan."""

    def build(self, objective: str, *, sample_ids: list[str] | None = None) -> AnalysisPlan:
        objective = objective.strip()
        if not objective:
            raise ValueError("objective must not be blank")
        prefix = uuid4().hex[:10]
        definitions = [
            ("prepare", AgentRole.PREPARE, "建立样本画像、哈希和可用输入", False),
            ("static", AgentRole.STATIC_REVERSE, "静态定位关键入口、字符串和调用关系", True),
            ("dynamic", AgentRole.DYNAMIC_EVIDENCE, "用受控动态证据验证高价值假设", True),
            ("verify", AgentRole.VERIFIER, "交叉核验支持、矛盾和未知项", False),
            ("report", AgentRole.REPORTER, "整理可回链的报告、证据和产物", False),
        ]
        steps: list[PlanStep] = []
        for index, (suffix, role, title, allow_subagent) in enumerate(definitions):
            step_id = f"step_{prefix}_{suffix}"
            steps.append(
                PlanStep(
                    id=step_id,
                    title=title,
                    role=role,
                    objective=f"{title}：{objective}",
                    depends_on=[steps[-1].id] if steps else [],
                    completion_criteria=["输出结构化事件", "保留证据或明确记录未知项"],
                    allow_subagent=allow_subagent,
                )
            )
        return AnalysisPlan(
            id=f"plan_{prefix}",
            objective=objective,
            sample_ids=list(sample_ids or []),
            steps=steps,
        )


__all__ = ["AnalysisPlan", "PlanStep", "Planner"]
