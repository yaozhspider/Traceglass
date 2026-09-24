"""Minimal FastAPI surface for the inspectable execution trace."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Query, status
from fastapi.responses import StreamingResponse

from traceglass.api.schemas import AppendEventRequest, CreateRunRequest, CreateSubRunRequest
from traceglass.application.trace_service import TraceService
from traceglass.domain.errors import DomainError, InvariantViolation
from traceglass.domain.models import TraceEvent
from traceglass.infrastructure.trace_store import (
    JsonlTraceStore,
    TraceStore,
    TraceStoreConflictError,
)


def _json_model(value: Any) -> dict[str, Any]:
    if hasattr(value, "model_dump"):
        return value.model_dump(mode="json")
    if hasattr(value, "to_dict"):
        return value.to_dict()
    if isinstance(value, dict):
        return value
    raise TypeError(f"unsupported API value: {type(value)!r}")


def create_app(*, store: TraceStore | None = None, service: TraceService | None = None) -> FastAPI:
    """Build an app with an injectable store for tests and deployments."""

    if service is None:
        if store is None:
            configured = os.environ.get("TRACEGLASS_TRACE_PATH", "traceglass-data/trace.jsonl")
            store = JsonlTraceStore(Path(configured))
        service = TraceService(store)

    app = FastAPI(title="Traceglass Trace API", version="0.1.0")
    app.state.trace_service = service

    @app.get("/health")
    def health() -> dict[str, str]:
        return {"status": "ok", "service": "traceglass"}

    @app.post("/runs", status_code=status.HTTP_201_CREATED)
    def create_run(request: CreateRunRequest) -> dict[str, Any]:
        try:
            run = service.create_run(**request.model_dump())
        except (DomainError, ValueError) as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        return {"run": _json_model(run)}

    @app.get("/runs/{run_id}")
    def get_run(run_id: str) -> dict[str, Any]:
        try:
            run = service.get_run(run_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        page = service.replay(run_id, limit=1)
        return {
            "run": _json_model(run),
            "event_cursor": page.next_cursor,
            "trace_count_hint": page.next_cursor,
        }

    @app.post("/runs/{run_id}/start")
    def start_run(run_id: str) -> dict[str, Any]:
        try:
            return {"run": _json_model(service.start_run(run_id))}
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except DomainError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    @app.post("/runs/{run_id}/complete")
    def complete_run(run_id: str) -> dict[str, Any]:
        try:
            return {"run": _json_model(service.complete_run(run_id))}
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except DomainError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    @app.post("/runs/{run_id}/events", status_code=status.HTTP_201_CREATED)
    def append_event(run_id: str, request: AppendEventRequest) -> dict[str, Any]:
        try:
            values = request.model_dump()
            values["event_type"] = values.pop("type")
            event = service.emit(run_id, **values)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except TraceStoreConflictError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except (DomainError, ValueError) as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        return {"event": _json_model(event)}

    @app.get("/runs/{run_id}/trace")
    def replay_trace(
        run_id: str,
        after: str | None = Query(default=None),
        limit: int | None = Query(default=None, ge=1, le=1000),
    ) -> dict[str, Any]:
        try:
            page = service.replay(run_id, after=after, limit=limit)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        return {
            "events": [_json_model(event) for event in page.events],
            "next_cursor": page.next_cursor,
            "has_more": page.has_more,
        }

    @app.get("/trace/events/{event_id}")
    def get_event(event_id: str) -> dict[str, Any]:
        stored = service.store.get(event_id)
        if stored is None:
            raise HTTPException(status_code=404, detail=f"unknown event: {event_id}")
        return {"event": _json_model(TraceEvent.model_validate(stored.to_dict()))}

    @app.post("/runs/{run_id}/subruns", status_code=status.HTTP_201_CREATED)
    def create_subrun(run_id: str, request: CreateSubRunRequest) -> dict[str, Any]:
        try:
            subrun = service.create_subrun(parent_run_id=run_id, **request.model_dump())
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except (DomainError, ValueError) as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        return {"subrun": _json_model(subrun)}

    @app.get("/subruns/{subrun_id}")
    def get_subrun(subrun_id: str) -> dict[str, Any]:
        try:
            subrun = service.get_subrun(subrun_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        parent_page = service.replay(subrun.parent_run_id)
        events = [
            _json_model(event)
            for event in parent_page.events
            if event.agent_run_id == subrun.id
        ]
        return {"subrun": _json_model(subrun), "events": events}

    @app.post("/subruns/{subrun_id}/continue")
    def continue_subrun(subrun_id: str) -> dict[str, Any]:
        try:
            subrun = service.start_subrun(subrun_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except DomainError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        return {"subrun": _json_model(subrun)}

    @app.get("/runs/{run_id}/trace/stream")
    def stream_trace(
        run_id: str,
        after: str | None = Query(default=None),
    ) -> StreamingResponse:
        """Return a replayable SSE snapshot.

        A production worker can replace this bounded snapshot with a broker
        subscription without changing the event envelope consumed by the UI.
        """

        try:
            page = service.replay(run_id, after=after)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

        def body():
            for event in page.events:
                payload = _json_model(event)
                import json

                yield f"id: {payload['sequence']}\ndata: {json.dumps(payload, ensure_ascii=False)}\n\n"

        return StreamingResponse(body(), media_type="text/event-stream")

    return app


app = create_app()


def main() -> None:
    import uvicorn

    uvicorn.run("traceglass.api.app:app", host="127.0.0.1", port=8000, reload=False)


__all__ = ["app", "create_app", "main"]
