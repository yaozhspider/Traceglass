# Traceglass trace format

Traceglass stores one durable event envelope for every user-visible execution
action. The stream is append-only and ordered independently for each `run_id`.
The JSONL backend is intentionally inspectable; the SQLite backend implements
the same interface for indexed deployments.

```json
{
  "event_id": "evt_123",
  "run_id": "run_001",
  "sequence": 42,
  "timestamp": "2026-09-24T10:00:00+00:00",
  "type": "tool.completed",
  "parent_event_id": "evt_100",
  "agent_run_id": "subrun_002",
  "agent_type": "static-reverse",
  "title": "反编译关键函数",
  "content": "发现服务创建调用",
  "tool": {
    "name": "decompile_function",
    "arguments": {"address": "0x401000"},
    "result": {"function": "install_service"},
    "duration_ms": 1840
  },
  "evidence_ids": ["E-031"],
  "artifact_ids": []
}
```

The API returns `next_cursor` as the last sequence in a page. Requesting
`GET /runs/{run_id}/trace?after={cursor}` resumes strictly after that event.
Clients should persist the cursor alongside the UI snapshot and deduplicate by
`event_id` after reconnecting.

The `agent_run_id` link makes a child execution expandable in the same timeline
as the parent. Child agents are one level deep and return structured evidence
and artifacts. Provider-internal hidden reasoning tokens are not persisted;
the stream contains the assistant messages, bounded progress updates, tool
arguments/results and evidence needed to audit the run.

Raw tool payloads may be stored separately and referenced with
`raw_payload_ref`. The hot event row keeps bounded JSON so the UI can render
the timeline quickly without exposing credentials or unbounded logs.

