"""The single user-facing analyst agent."""

from __future__ import annotations

from dataclasses import dataclass

from traceglass.agent.executor import ExecutionResult, PlanExecutor, ToolRegistry
from traceglass.agent.planner import AnalysisPlan, Planner
from traceglass.application.trace_service import TraceService
from traceglass.domain.models import RunStatus, TraceEventType


@dataclass(frozen=True)
class AnalysisResult:
    plan: AnalysisPlan
    execution: ExecutionResult


class AnalystAgent:
    """Coordinate Plan-Execute and bounded ReAct behind one visible agent."""

    def __init__(
        self,
        service: TraceService,
        tools: ToolRegistry,
        *,
        planner: Planner | None = None,
        max_turns_per_step: int = 4,
    ) -> None:
        self.service = service
        self.planner = planner or Planner()
        self.executor = PlanExecutor(service, tools, max_turns_per_step=max_turns_per_step)

    def analyze(
        self,
        *,
        run_id: str,
        objective: str,
        sample_ids: list[str] | None = None,
    ) -> AnalysisResult:
        run = self.service.get_run(run_id)
        if run.status is RunStatus.CREATED:
            self.service.start_run(run_id)
        plan = self.planner.build(objective, sample_ids=sample_ids)
        self.service.emit(
            run_id,
            event_type=TraceEventType.ASSISTANT_MESSAGE,
            title="分析计划已生成",
            content="我会按阶段执行，并在每个工具和子 Agent 结果旁保留可展开轨迹。",
            payload={"plan": plan.model_dump(mode="json")},
        )
        execution = self.executor.execute(run_id, plan)
        if not execution.failed_step_ids and not execution.skipped_step_ids:
            self.service.complete_run(run_id)
        return AnalysisResult(plan=plan, execution=execution)


__all__ = ["AnalysisResult", "AnalystAgent"]
