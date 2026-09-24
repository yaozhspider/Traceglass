"""Plan execution with a bounded, user-visible ReAct loop."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable

from traceglass.agent.planner import AnalysisPlan, PlanStep
from traceglass.application.trace_service import TraceService
from traceglass.domain.models import AgentRole, ToolCall, TraceEventType


Tool = Callable[[dict[str, Any]], Any]


class ToolRegistry:
    def __init__(self) -> None:
        self._tools: dict[str, Tool] = {}

    def register(self, name: str, tool: Tool) -> None:
        name = name.strip()
        if not name:
            raise ValueError("tool name must not be blank")
        self._tools[name] = tool

    def resolve(self, name: str) -> Tool | None:
        return self._tools.get(name)


@dataclass(frozen=True)
class ExecutionResult:
    completed_step_ids: list[str] = field(default_factory=list)
    skipped_step_ids: list[str] = field(default_factory=list)
    failed_step_ids: list[str] = field(default_factory=list)


class PlanExecutor:
    """Execute selected plan steps and persist every external action.

    The loop exposes bounded progress updates and tool calls.  It intentionally
    does not persist provider hidden reasoning tokens; users see the concise
    rationale needed to understand what is happening and can expand every
    resulting event in the UI.
    """

    def __init__(self, service: TraceService, tools: ToolRegistry, *, max_turns_per_step: int = 4):
        if max_turns_per_step < 1:
            raise ValueError("max_turns_per_step must be positive")
        self.service = service
        self.tools = tools
        self.max_turns_per_step = max_turns_per_step

    def execute(
        self,
        run_id: str,
        plan: AnalysisPlan,
        *,
        roles: set[AgentRole] | None = None,
        context: dict[str, Any] | None = None,
    ) -> ExecutionResult:
        selected = roles or {step.role for step in plan.steps}
        context = dict(context or {})
        completed: list[str] = []
        skipped: list[str] = []
        failed: list[str] = []
        available = set(completed)

        for step in plan.steps:
            if step.role not in selected:
                skipped.append(step.id)
                continue
            if any(dependency not in available for dependency in step.depends_on):
                skipped.append(step.id)
                self.service.emit(
                    run_id,
                    event_type=TraceEventType.TODO_UPDATED,
                    title=f"跳过步骤：{step.title}",
                    content="依赖步骤尚未完成。",
                    payload={"step_id": step.id, "status": "blocked"},
                )
                continue
            self.service.emit(
                run_id,
                event_type=TraceEventType.TODO_CREATED,
                title=step.title,
                content=step.objective,
                payload={"step_id": step.id, "role": step.role.value},
            )
            self.service.emit(
                run_id,
                event_type=TraceEventType.THINKING_UPDATE,
                title=f"准备执行：{step.title}",
                content="先收集可验证证据，再决定是否需要子 Agent。",
                payload={"step_id": step.id, "turn": 0, "max_turns": self.max_turns_per_step},
            )
            tool_name = step.role.value
            tool = self.tools.resolve(tool_name)
            if tool is None:
                self.service.emit(
                    run_id,
                    event_type=TraceEventType.TODO_UPDATED,
                    title=f"等待工具：{tool_name}",
                    content="当前执行器没有为该步骤配置工具。",
                    payload={"step_id": step.id, "status": "waiting_input"},
                )
                skipped.append(step.id)
                continue
            call_id = f"call_{step.id}"
            self.service.tool_started(
                run_id,
                tool_name=tool_name,
                arguments={"objective": step.objective, **context},
                call_id=call_id,
            )
            try:
                result = tool({"objective": step.objective, "step_id": step.id, **context})
            except Exception as exc:  # tool errors are visible and bounded
                failed.append(step.id)
                self.service.emit(
                    run_id,
                    event_type=TraceEventType.TOOL_FAILED,
                    title=f"工具失败：{tool_name}",
                    content=str(exc),
                    tool=ToolCall(
                        name=tool_name,
                        arguments={"objective": step.objective, **context},
                        result={"error": str(exc)},
                    ),
                    payload={"call_id": call_id, "step_id": step.id},
                )
                continue
            self.service.tool_completed(run_id, call_id=call_id, result=result)
            self.service.emit(
                run_id,
                event_type=TraceEventType.TODO_UPDATED,
                title=f"完成步骤：{step.title}",
                content="步骤输出已持久化，可展开查看工具结果。",
                payload={"step_id": step.id, "status": "completed"},
            )
            completed.append(step.id)
            available.add(step.id)
            context[f"{step.role.value}_result"] = result

        return ExecutionResult(completed, skipped, failed)


__all__ = ["ExecutionResult", "PlanExecutor", "ToolRegistry"]
