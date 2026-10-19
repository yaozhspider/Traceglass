# Native conversation and event data

Current design: [API and frontend](design/API_AND_FRONTEND.md),
[execution and collaboration](design/EXECUTION.md), and
[data and evidence](design/DATA_MODEL.md).

The September 24 custom TraceEvent and `/runs` API have been removed. AgentScope
session IDs, reply IDs, message blocks, tool IDs and team membership are the
source of truth.

- `POST /product/workspaces/{workspace_id}/sessions`: create a workspace-bound
  native session. Direct native session creation is available only in local
  deployments; account mode closes that entry point.
- `GET /sessions/?agent_id=...`: native status, tasks and team members.
- `POST /chat/`: trigger or resume a reply; events arrive separately.
- `GET /sessions/{id}/messages?agent_id=...`: persisted, paginated message blocks.
- `GET /sessions/{id}/stream?agent_id=...`: native replay plus live SSE.
- `POST /product/sessions/{id}/interrupt?agent_id=...&scope=session|team`:
  product pause, which includes native interruption and session-owned background
  tasks. The direct native interrupt route is not exposed in account mode.

Local native requests use `X-User-ID` for identity routing; it is not
authentication. Account deployments use an HttpOnly session cookie and CSRF
headers on writes, and replace caller-supplied `X-User-ID` with the authenticated
identity. `/product/config` returns only non-secret bootstrap settings.

`web/src/features/execution/native.ts` is a display projection: it groups events
by reply/block/tool ID, deduplicates replay IDs and merges persisted snapshots
with ongoing deltas. It never schedules work or decides run completion.

The UI uses native messages for reload and process-restart history. Received
live events are expandable in the current page; the framework bus replay is
bounded and is not represented as an everlasting per-token audit store.
Child histories use each native member's agent_id and session_id.
