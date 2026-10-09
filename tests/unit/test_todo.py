"""TodoWrite must share native tasks and commit batch changes atomically."""
from __future__ import annotations

import asyncio
import json

import pytest
from agentscope.message import TextBlock, ToolResultState
from agentscope.state import AgentState, Task, TaskContext
from agentscope.tool import TaskCreate, TaskUpdate, ToolChunk


def _write(state: AgentState, todos: list[dict]) -> ToolChunk:
    from traceglass.tools.todo import TodoWrite

    return asyncio.run(TodoWrite()(_agent_state=state, todos=todos))


def test_todo_write_returns_native_ids_and_allows_native_updates():
    state = AgentState(tasks_context=TaskContext(tasks=[Task(
        id="7", subject="Earlier analysis", description="Existing history", metadata={},
    )]))

    result = _write(state, [
        {"subject": "Inspect sample", "description": "Collect format and hash", "status": "in_progress", "owner": "static-expert"},
        {"subject": "Verify evidence"},
    ])

    assert result.state != ToolResultState.ERROR
    assert json.loads(result.content[0].text)["todos"] == [
        {"task_id": "8", "subject": "Inspect sample", "status": "in_progress", "owner": "static-expert"},
        {"task_id": "9", "subject": "Verify evidence", "status": "pending", "owner": None},
    ]
    asyncio.run(TaskUpdate()(_agent_state=state, task_id="8", status="completed"))
    assert [(task.id, task.state) for task in state.tasks_context.tasks] == [
        ("7", "pending"), ("8", "completed"), ("9", "pending"),
    ]
    assert state.tasks_context.tasks[1].description == "Collect format and hash"
    assert state.tasks_context.tasks[2].description == ""


def test_todo_update_preserves_native_dependencies_metadata_and_omitted_tasks():
    state = AgentState(tasks_context=TaskContext(tasks=[
        Task(id="1", subject="Inspect", description="Keep source details", metadata={"evidence": "abc"},
             owner="static-expert", blocks=["2"], created_at="2026-09-01T12:00:00"),
        Task(id="2", subject="Verify", description="Independent check", metadata={}, blocked_by=["1"]),
    ]))
    omitted = state.tasks_context.tasks[1].model_dump()

    result = _write(state, [{"task_id": "1", "subject": "Inspect format", "status": "completed"}])

    assert result.state != ToolResultState.ERROR
    first = state.tasks_context.tasks[0]
    assert (first.subject, first.state, first.description, first.owner) == (
        "Inspect format", "completed", "Keep source details", "static-expert",
    )
    assert first.metadata == {"evidence": "abc"}
    assert first.blocks == ["2"]
    assert first.created_at == "2026-09-01T12:00:00"
    assert state.tasks_context.tasks[1].model_dump() == omitted


@pytest.mark.parametrize("invalid", [
    {"task_id": "404", "subject": "Unknown task"},
    {"task_id": "1", "subject": "Duplicate update"},
    {"subject": "   "},
    {"subject": "Invalid status", "status": "deleted"},
])
def test_invalid_batch_leaves_existing_tasks_unchanged(invalid: dict):
    state = AgentState(tasks_context=TaskContext(tasks=[Task(
        id="1", subject="Original task", description="Original details", metadata={"source": "report"},
    )]))
    before = state.model_dump()

    result = _write(state, [
        {"task_id": "1", "subject": "Must not commit", "status": "completed"},
        invalid,
    ])

    assert result.state == ToolResultState.ERROR
    assert state.model_dump() == before


def test_native_tool_failure_does_not_partially_commit_batch(monkeypatch):
    state = AgentState()
    before = state.model_dump()
    native_update = TaskUpdate.call

    async def fail_second(self, **kwargs):
        if kwargs["task_id"] == "2":
            return ToolChunk(state=ToolResultState.ERROR, content=[TextBlock(text="native fixture failure")])
        return await native_update(self, **kwargs)

    monkeypatch.setattr(TaskUpdate, "call", fail_second)
    result = _write(state, [
        {"subject": "First staged task", "status": "in_progress"},
        {"subject": "Second staged task", "status": "in_progress"},
    ])

    assert result.state == ToolResultState.ERROR
    assert "native fixture failure" in result.content[0].text
    assert state.model_dump() == before


def test_empty_incremental_write_preserves_history():
    state = AgentState()
    asyncio.run(TaskCreate()(_agent_state=state, subject="Existing", description="Keep"))
    before = state.model_dump()

    result = _write(state, [])

    assert result.state != ToolResultState.ERROR
    assert json.loads(result.content[0].text) == {"todos": []}
    assert state.model_dump() == before
