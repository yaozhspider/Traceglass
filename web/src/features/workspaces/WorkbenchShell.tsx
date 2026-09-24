import { useCallback, useEffect, useMemo, useState } from "react";
import { Activity, Bot, FolderOpen, PanelRight, RefreshCw, Search, ShieldCheck } from "lucide-react";
import { ExecutionTimeline } from "../execution/ExecutionTimeline";
import { readTrace } from "../execution/traceApi";
import type { TraceEvent } from "../execution/types";

type Props = { runId: string };

export function WorkbenchShell({ runId }: Props) {
  const [events, setEvents] = useState<TraceEvent[]>([]);
  const [cursor, setCursor] = useState<string | null>(null);
  const [selected, setSelected] = useState<TraceEvent | null>(null);
  const [error, setError] = useState<string | null>(null);

  const refresh = useCallback(async () => {
    try {
      const page = await readTrace(runId, cursor);
      setEvents((current) => {
        const seen = new Set(current.map((event) => event.event_id));
        return [...current, ...page.events.filter((event) => !seen.has(event.event_id))];
      });
      setCursor(page.next_cursor ?? cursor);
      setError(null);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "无法读取执行轨迹");
    }
  }, [cursor, runId]);

  useEffect(() => { void refresh(); }, [runId]);
  useEffect(() => {
    const timer = window.setInterval(() => void refresh(), 1500);
    return () => window.clearInterval(timer);
  }, [refresh]);

  const status = useMemo(() => [...events].reverse().find((event) => event.type === "run.status_changed")?.content ?? "等待执行", [events]);
  return (
    <div className="app-shell">
      <aside className="nav-rail">
        <div className="brand-mark">TG</div>
        <button className="nav-button active"><Activity size={18} /><span>工作台</span></button>
        <button className="nav-button"><FolderOpen size={18} /><span>样本库</span></button>
        <button className="nav-button"><ShieldCheck size={18} /><span>证据</span></button>
        <div className="nav-spacer" />
        <button className="nav-button"><Bot size={18} /><span>Agent</span></button>
      </aside>
      <aside className="task-sidebar">
        <div className="sidebar-heading"><span>分析任务</span><button className="icon-button" aria-label="搜索"><Search size={16} /></button></div>
        <div className="task-search">持久化机制分析</div>
        <div className="task-list-item selected"><span className="status-pulse" /><div><strong>样本画像 · run-{runId.slice(-4)}</strong><small>{status}</small></div></div>
        <div className="task-list-item"><span className="status-dot" /><div><strong>网络行为核查</strong><small>昨天 · 已完成</small></div></div>
        <div className="task-list-item"><span className="status-dot" /><div><strong>家族归因复核</strong><small>周一 · 待审阅</small></div></div>
      </aside>
      <main className="workspace-main">
        <header className="workspace-header"><div><div className="eyebrow">WORKSPACE / REVERSE ANALYSIS</div><h1>持久化机制分析</h1></div><div className="header-actions"><span className="live-status"><span className="status-pulse" />{status}</span><button className="secondary-button" onClick={() => void refresh()}><RefreshCw size={15} />刷新</button></div></header>
        <section className="objective-card"><div className="assistant-avatar"><Bot size={18} /></div><div><strong>主 Agent</strong><p>我会先建立样本画像，再按计划核查静态证据、动态行为和结论一致性。每一步都可以展开查看输入、工具结果、证据和产物。</p></div></section>
        {error && <div className="error-banner">{error} · 请确认 API 已启动</div>}
        <div className="timeline-heading"><div><h2>执行轨迹</h2><span>{events.length} 个可审计事件 · cursor {cursor ?? "—"}</span></div><button className="secondary-button"><PanelRight size={15} />轨迹视图</button></div>
        <ExecutionTimeline events={events} onSelect={setSelected} />
      </main>
      <aside className="inspector-panel">
        {selected ? <><div className="inspector-heading"><span>事件详情</span><button className="icon-button" onClick={() => setSelected(null)}>×</button></div><div className="inspector-type">{selected.type}</div><h3>{selected.title ?? "未命名事件"}</h3><p>{selected.content ?? "此事件没有文字说明。"}</p><dl><dt>事件 ID</dt><dd>{selected.event_id}</dd><dt>Sequence</dt><dd>#{selected.sequence}</dd>{selected.agent_run_id && <><dt>Agent Run</dt><dd>{selected.agent_run_id}</dd></>}</dl><pre className="inspector-json">{JSON.stringify(selected, null, 2)}</pre></> : <div className="inspector-empty"><PanelRight size={24} /><strong>证据与产物检查器</strong><p>点击轨迹中的事件、证据或产物，在这里查看完整上下文。</p></div>}
      </aside>
    </div>
  );
}
