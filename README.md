# Traceglass

Traceglass is an evidence-driven reverse-engineering workbench. It presents one analyst assistant with a durable, inspectable execution tree: plans, thoughts, tool calls, sub-agents, evidence, artifacts, and report revisions.

## Current slice

The foundation now includes:

- explicit `Task`, `Run`, `Step`, `SubRun`, `Evidence`, `Artifact`, `Finding` and `TraceEvent` contracts;
- append-only JSONL and SQLite trace stores with per-run sequences, cursor replay, idempotency and safe payload serialization;
- a `TraceService` that validates parent/child ownership and exposes tool, evidence and artifact events;
- a provider-neutral `Planner → PlanExecutor (bounded ReAct) → SubagentManager` runtime;
- a FastAPI trace API and a React workbench with an expandable parent/child execution timeline.

This slice deliberately does not call an LLM or execute binary samples. The next adapter can connect AgentScope, pi, Hermes or another model provider to the same planner/executor and trace contracts without creating a second execution engine. Provider-internal hidden reasoning tokens are not persisted; concise, user-readable progress plus every tool input/output and child-agent event is retained.

## Development

```bash
python3 -m venv .venv
. .venv/bin/activate
pip install -e '.[test]'
pytest
```

Start the API:

```bash
traceglass
```

Start the workbench in another terminal:

```bash
cd web
npm install
npm run dev
```

The UI reads `/runs/{run_id}/trace`, resumes from its cursor after reconnects,
and lets users expand child-agent runs, tool payloads, evidence links and raw
structured event data. Set `VITE_TRACEGLASS_API` when the API is not on
`http://127.0.0.1:8000`.

The design and implementation plan live under `docs/superpowers/`.
