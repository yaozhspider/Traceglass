import { useMemo, useState } from "react";
import { ChevronDown, ChevronRight, CircleAlert, CircleCheck, Clock3, FileSearch, Wrench } from "lucide-react";
import type { TraceEvent } from "./types";

type Props = {
  events: TraceEvent[];
  onSelect?: (event: TraceEvent) => void;
};

const labels: Record<string, string> = {
  "run.status_changed": "运行状态",
  "assistant.message": "主 Agent",
  "thinking.update": "分析进度",
  "todo.created": "计划步骤",
  "todo.updated": "步骤更新",
  "tool.started": "工具调用",
  "tool.completed": "工具结果",
  "tool.failed": "工具失败",
  "subagent.created": "创建子 Agent",
  "subagent.started": "子 Agent 开始",
  "subagent.message": "子 Agent 输出",
  "subagent.completed": "子 Agent 完成",
  "evidence.created": "证据",
  "artifact.created": "产物",
};

function EventIcon({ event }: { event: TraceEvent }) {
  if (event.type.includes("failed")) return <CircleAlert size={15} className="danger" />;
  if (event.type.includes("completed")) return <CircleCheck size={15} className="success" />;
  if (event.type.includes("tool")) return <Wrench size={15} />;
  if (event.type.includes("evidence")) return <FileSearch size={15} />;
  return <Clock3 size={15} />;
}

function EventRow({ event, onSelect }: { event: TraceEvent; onSelect?: Props["onSelect"] }) {
  const [open, setOpen] = useState(event.type === "assistant.message");
  const hasDetails = Boolean(event.content || event.tool || event.payload || event.evidence_ids?.length || event.artifact_ids?.length);
  return (
    <div className={`trace-row ${event.type.includes("failed") ? "trace-row-error" : ""}`}>
      <button className="trace-row-main" onClick={() => (hasDetails ? setOpen((value) => !value) : onSelect?.(event))}>
        <span className="trace-toggle">{hasDetails ? (open ? <ChevronDown size={15} /> : <ChevronRight size={15} />) : <span />}</span>
        <EventIcon event={event} />
        <span className="trace-type">{labels[event.type] ?? event.type}</span>
        <span className="trace-title">{event.title ?? event.content ?? "执行事件"}</span>
        <span className="trace-sequence">#{event.sequence}</span>
      </button>
      {open && hasDetails && (
        <div className="trace-details">
          {event.content && <p className="trace-content">{event.content}</p>}
          {event.tool && (
            <div className="tool-card">
              <div className="tool-card-head"><span>{event.tool.name}</span>{event.tool.duration_ms != null && <small>{event.tool.duration_ms} ms</small>}</div>
              <pre>{JSON.stringify({ arguments: event.tool.arguments, result: event.tool.result }, null, 2)}</pre>
            </div>
          )}
          {event.evidence_ids?.length ? <div className="chip-line"><span>证据</span>{event.evidence_ids.map((id) => <button key={id} onClick={() => onSelect?.(event)} className="evidence-chip">{id}</button>)}</div> : null}
          {event.artifact_ids?.length ? <div className="chip-line"><span>产物</span>{event.artifact_ids.map((id) => <button key={id} onClick={() => onSelect?.(event)} className="artifact-chip">{id}</button>)}</div> : null}
          {event.payload && Object.keys(event.payload).length > 0 && <details className="raw-details"><summary>查看结构化事件</summary><pre>{JSON.stringify(event.payload, null, 2)}</pre></details>}
        </div>
      )}
    </div>
  );
}

export function ExecutionTimeline({ events, onSelect }: Props) {
  const groups = useMemo(() => {
    const main: TraceEvent[] = [];
    const children = new Map<string, TraceEvent[]>();
    for (const event of events) {
      if (event.agent_run_id) {
        const current = children.get(event.agent_run_id) ?? [];
        current.push(event);
        children.set(event.agent_run_id, current);
      } else {
        main.push(event);
      }
    }
    return { main, children };
  }, [events]);

  return (
    <div className="timeline" aria-label="完整执行轨迹">
      {groups.main.map((event) => <EventRow key={event.event_id} event={event} onSelect={onSelect} />)}
      {[...groups.children.entries()].map(([agentRunId, childEvents]) => (
        <details className="subagent-card" open key={agentRunId}>
          <summary><span className="subagent-dot" />子 Agent · {childEvents[0]?.agent_type ?? agentRunId}<span className="subagent-count">{childEvents.length} 个事件</span></summary>
          <div className="subagent-body">{childEvents.map((event) => <EventRow key={event.event_id} event={event} onSelect={onSelect} />)}</div>
        </details>
      ))}
      {events.length === 0 && <div className="empty-trace">等待执行事件…</div>}
    </div>
  );
}
