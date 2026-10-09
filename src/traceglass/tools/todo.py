"""A bulk-writing facade over AgentScope's native task context and tools."""
from __future__ import annotations

import json
from typing import Annotated, Any, Literal

from agentscope.exception import DeveloperOrientedException
from agentscope.message import TextBlock, ToolResultState
from agentscope.permission import PermissionContext, PermissionDecision
from agentscope.state import AgentState
from agentscope.tool import ParamsBase, TaskCreate, TaskUpdate, ToolBase, ToolChunk
from pydantic import ConfigDict, Field, StringConstraints, ValidationError


NonBlank = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]


class _Todo(ParamsBase):
    model_config = ConfigDict(extra="forbid")

    task_id: NonBlank | None = Field(default=None, description="Existing native task ID; omit to create a task.")
    subject: NonBlank = Field(description="Brief, actionable task title.")
    description: str = Field(default="", description="Scope, evidence inputs, expected result, and completion criteria.")
    status: Literal["pending", "in_progress", "completed"] = "pending"
    owner: str | None = Field(default=None, description="Assigned team member name, when known.")


class _TodoWriteParams(ParamsBase):
    model_config = ConfigDict(extra="forbid")

    todos: list[_Todo] = Field(description="Tasks to create or update; omitted tasks remain unchanged.")


class TodoWrite(ToolBase):
    """Commit a validated batch using TaskCreate and TaskUpdate, without a second store."""

    name = "TodoWrite"
    description = """Create or update the visible plan before delegating work.
Use one item per concrete outcome and include evidence inputs, expected artifacts,
and completion criteria in its description. Omit task_id to create a new task;
reuse the returned task_id to update an existing task. Set owner to the actual
AgentCreate member name when assigning work, and mark completed only after
reviewing that member's evidence. This is an incremental upsert: omitted tasks,
dependencies, metadata, and task history remain unchanged. New tasks default to
pending with an empty description; fields omitted on updates retain their values.
The whole batch is rejected if any item is invalid. Use native TaskUpdate for
dependencies and TaskList/TaskGet to inspect the same shared task list."""
    input_schema = _TodoWriteParams.model_json_schema()
    is_state_injected = True
    is_concurrency_safe = False
    is_read_only = False

    async def check_permissions(
        self, tool_input: dict[str, Any], context: PermissionContext,
    ) -> PermissionDecision:
        return await TaskUpdate().check_permissions(tool_input, context)

    async def call(self, _agent_state: AgentState, todos: list[dict]) -> ToolChunk:
        if not isinstance(_agent_state, AgentState):
            raise DeveloperOrientedException("TodoWrite requires an injected AgentState.")

        try:
            items = _TodoWriteParams.model_validate({"todos": todos}).todos
            known_ids = {task.id for task in _agent_state.tasks_context.tasks}
            requested_ids = [item.task_id for item in items if item.task_id is not None]
            if len(requested_ids) != len(set(requested_ids)):
                raise ValueError("Duplicate task_id in TodoWrite batch.")
            unknown = set(requested_ids) - known_ids
            if unknown:
                raise ValueError(f"Unknown task_id: {', '.join(sorted(unknown))}")
        except (ValidationError, ValueError) as exc:
            return ToolChunk(state=ToolResultState.ERROR, content=[TextBlock(text=f"TodoWrite: {exc}")])

        # Only task state is copied: session history, permissions, and tool
        # caches stay native and are never altered by this adapter.
        staged = _agent_state.model_copy(update={
            "tasks_context": _agent_state.tasks_context.model_copy(deep=True),
        })
        create, update = TaskCreate(), TaskUpdate()
        written = []
        for item in items:
            task_id = item.task_id
            if task_id is None:
                result = await create(
                    _agent_state=staged, subject=item.subject, description=item.description,
                )
                if result.state == ToolResultState.ERROR:
                    return result
                task_id = staged.tasks_context.tasks[-1].id
            updates = item.model_dump(exclude_unset=True, exclude={"task_id"})
            result = await update(_agent_state=staged, task_id=task_id, **updates)
            if result.state == ToolResultState.ERROR:
                return result
            task = next(task for task in staged.tasks_context.tasks if task.id == task_id)
            written.append({
                "task_id": task.id, "subject": task.subject,
                "status": task.state, "owner": task.owner,
            })

        _agent_state.tasks_context = staged.tasks_context
        return ToolChunk(
            state=ToolResultState.SUCCESS,
            content=[TextBlock(text=json.dumps({"todos": written}, ensure_ascii=False))],
        )
