export type ToolCall = {
  name: string;
  arguments: Record<string, unknown>;
  result?: unknown;
  duration_ms?: number;
};

export type TraceEvent = {
  event_id: string;
  run_id: string;
  sequence: number;
  timestamp: string;
  type: string;
  title?: string | null;
  content?: string | null;
  parent_event_id?: string | null;
  agent_run_id?: string | null;
  agent_type?: string | null;
  tool?: ToolCall | null;
  payload?: Record<string, unknown>;
  evidence_ids?: string[];
  artifact_ids?: string[];
};

export type TracePage = {
  events: TraceEvent[];
  next_cursor: string | null;
  has_more: boolean;
};
