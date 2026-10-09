# Native conversation and event data

The September 24 custom TraceEvent and `/runs` API have been removed. AgentScope
session IDs, reply IDs, message blocks, tool IDs and team membership are the
source of truth.

- `POST /sessions/`: create a native session.
- `GET /sessions/?agent_id=...`: native status, tasks and team members.
- `POST /chat/`: trigger or resume a reply; events arrive separately.
- `GET /sessions/{id}/messages?agent_id=...`: persisted, paginated message blocks.
- `GET /sessions/{id}/stream?agent_id=...`: native replay plus live SSE.
- `POST /sessions/{id}/interrupt?agent_id=...`: native interrupt.

Native requests include `X-User-ID`. `/product/config` returns only non-secret
bootstrap settings to the local workbench.

`web/src/features/execution/native.ts` is a display projection: it groups events
by reply/block/tool ID, deduplicates replay IDs and merges persisted snapshots
with ongoing deltas. It never schedules work or decides run completion.

The UI uses native messages for reload and process-restart history. Received
live events are expandable in the current page; the framework bus replay is
bounded and is not represented as an everlasting per-token audit store.
Child histories use each native member's agent_id and session_id.
