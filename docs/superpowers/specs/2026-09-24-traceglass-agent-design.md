> Superseded on 2026-09-28 by the native AgentScope correction. Kept only as design history. See `../plans/2026-09-28-native-agentscope.md` and the current README.

> Current implementation: [system design](../../DESIGN.md) and [documentation index](../../README.md).

# Traceglass Agent and Execution Design

## Goal

Build a product-grade reverse-engineering workbench with one visible analyst assistant and a durable, fully inspectable execution tree that includes the main agent, plans, tool calls, sub-agents, artifacts, evidence, and report revisions.

## Architecture

A task-level Plan-Execute controller owns lifecycle, budgets, checkpoints, and recovery. Each executable step runs a bounded ReAct loop through an adapter, initially AgentScope. Optional sub-agents are explicit, one level deep, isolated by tool policy and work directory, and return structured evidence bundles; they cannot mutate the final report or lifecycle state.

The product persists an append-only trace event stream. The UI renders the complete parent/child trace and lets the user open any sub-agent run, prompt, tool call, result, artifact, and evidence reference. The runtime does not expose provider-internal hidden reasoning tokens; it exposes all assistant messages and structured progress events produced by the run.

## Domain contracts

- Task: durable user objective and workspace binding.
- Run: one attempt, continuation, retry, or recovery of a task.
- Step: bounded unit in a run with dependencies and completion criteria.
- TraceEvent: ordered, replayable event with parent and agent-run links.
- Evidence: immutable source-backed fact or observation.
- Finding: claim with support, contradiction, confidence, unknowns, and next action.
- Artifact: content-addressed output linked to a run, step, and evidence.

## Required behaviors

1. Repeating a command with the same idempotency key returns the original operation.
2. Trace sequences are monotonic per run and can be resumed with a cursor.
3. A sub-agent has a parent run, explicit role, input prompt, capabilities, and terminal status.
4. Tool results and raw payloads are inspectable through trace references with bounded previews.
5. Evidence IDs are validated before a finding or report references them.
6. Lifecycle state is server-owned; the UI never infers completion from a chat stream timeout.
7. Existing reverse-project archive and sandbox guarantees will be adapted later, not copied into a second unbounded execution engine.
