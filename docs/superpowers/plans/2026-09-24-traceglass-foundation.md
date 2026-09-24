# Traceglass Foundation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development or superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Establish a tested Traceglass foundation with explicit domain contracts and a fully inspectable parent/child execution trace.

**Architecture:** A dependency-light Python domain layer defines Task, Run, Step, Evidence, Finding, Artifact, and TraceEvent contracts. An append-only JSONL trace store provides durable sequence and cursor reads; higher-level execution adapters will consume it later. No model, sample execution, or AgentScope dependency is required in this slice.

**Tech Stack:** Python 3.11+, Pydantic v2, FastAPI boundary later, pytest.

**Spec:** `docs/superpowers/specs/2026-09-24-traceglass-agent-design.md`

## Global Constraints

- Do not modify `/Users/yaozh/llms/reverse`.
- Do not read credentials, invoke external models, or execute binary samples.
- Every production behavior added in this slice gets a failing test first.
- Trace writes are append-only and preserve raw payload references without requiring them in the hot event row.
- Sub-agents are one level deep in the domain contract.

---

### Task 1: Domain contracts

**Files:**
- Create: `src/traceglass/domain/models.py`
- Create: `src/traceglass/domain/errors.py`
- Test: `tests/unit/test_domain_models.py`

**Deliverable:** Pydantic contracts with validated lifecycle states, trace event envelopes, findings, evidence references, and sub-agent metadata.

### Task 2: Durable trace store

**Files:**
- Create: `src/traceglass/infrastructure/trace_store.py`
- Test: `tests/unit/test_trace_store.py`

**Deliverable:** Append-only JSONL trace store with monotonic per-run sequence, parent/child links, cursor reads, idempotent event IDs, and safe payload serialization.

### Task 3: Execution trace service

**Files:**
- Create: `src/traceglass/application/trace_service.py`
- Test: `tests/unit/test_trace_service.py`

**Deliverable:** Service methods for run creation, agent/sub-agent registration, tool lifecycle, findings, artifacts, and trace replay. It validates parent run ownership and rejects a second-level sub-agent.

### Task 4: Minimal API and CLI health surface

**Files:**
- Create: `src/traceglass/api/app.py`
- Create: `src/traceglass/api/schemas.py`
- Test: `tests/contract/test_api_trace.py`

**Deliverable:** FastAPI endpoints to create a run, append a trace event, read a run snapshot, and replay events after a cursor. No model execution is wired yet.

### Task 5: Verification and developer documentation

**Files:**
- Modify: `README.md`
- Create: `docs/TRACE_FORMAT.md`
- Test: `tests/integration/test_foundation.py`

**Deliverable:** Run the complete foundation suite and document how the later AgentScope adapter will consume the contracts.
