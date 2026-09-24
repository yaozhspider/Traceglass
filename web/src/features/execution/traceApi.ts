import type { TraceEvent, TracePage } from "./types";

const apiBase = import.meta.env.VITE_TRACEGLASS_API ?? "http://127.0.0.1:8000";

export async function readTrace(runId: string, after?: string | null): Promise<TracePage> {
  const query = after ? `?after=${encodeURIComponent(after)}` : "";
  const response = await fetch(`${apiBase}/runs/${encodeURIComponent(runId)}/trace${query}`);
  if (!response.ok) throw new Error(`trace request failed: ${response.status}`);
  return response.json() as Promise<TracePage>;
}

export async function readEvent(eventId: string): Promise<TraceEvent> {
  const response = await fetch(`${apiBase}/trace/events/${encodeURIComponent(eventId)}`);
  if (!response.ok) throw new Error(`event request failed: ${response.status}`);
  const data = (await response.json()) as { event: TraceEvent };
  return data.event;
}
