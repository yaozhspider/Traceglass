import {useEffect, useState} from 'react';
import {workspaceApi} from '../workspaces/workspaceApi';
import type {ReaEvidence, ReaEvidencePage} from './reaEvidence';

const confidence: Record<string, string> = {observed: '观察记录', derived: '推导结果', inferred: '推断'};

export function ReaEvidencePanel({user, workspaceId, refreshKey}: {
  user: string; workspaceId: string; refreshKey: string;
}) {
  const [page, setPage] = useState<ReaEvidencePage>({items: [], total: 0, next_cursor: null});
  const [kind, setKind] = useState('');
  const [revision, setRevision] = useState(0);
  const [selected, setSelected] = useState<string | null>(null);
  const [record, setRecord] = useState<ReaEvidence | null>(null);
  const [error, setError] = useState('');
  const [loading, setLoading] = useState(false);
  const [loadingMore, setLoadingMore] = useState(false);
  const [cursor, setCursor] = useState<number | undefined>();
  useEffect(() => {setCursor(undefined); setSelected(null); setPage({items: [], total: 0, next_cursor: null});}, [kind, workspaceId]);
  useEffect(() => {
    const abort = new AbortController();
    setLoading(cursor === undefined); setLoadingMore(cursor !== undefined); setError('');
    workspaceApi.reaEvidence(user, workspaceId, {kind, cursor}, abort.signal).then(next => {
      if (abort.signal.aborted) return;
      setPage(current => cursor === undefined ? next : {...next,
        items: [...current.items, ...next.items.filter(item => !current.items.some(old => old.id === item.id))]});
    }).catch(reason => {if (!abort.signal.aborted) setError(String(reason));})
      .finally(() => {if (!abort.signal.aborted) {setLoading(false); setLoadingMore(false);}});
    return () => abort.abort();
  }, [user, workspaceId, kind, cursor, revision, refreshKey]);
  useEffect(() => {
    const abort = new AbortController();
    setRecord(null);
    if (selected) workspaceApi.reaRecord(user, workspaceId, selected, abort.signal)
      .then(value => {if (!abort.signal.aborted) setRecord(value);})
      .catch(reason => {if (!abort.signal.aborted) setError(String(reason));});
    return () => abort.abort();
  }, [user, workspaceId, selected]);
  const download = () => {
    if (!record?.raw) return;
    const url = URL.createObjectURL(new Blob([record.raw], {type: 'application/json'}));
    const link = document.createElement('a');
    link.href = url; link.download = `${record.id}.json`; link.click();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
  };
  return <section className="rea-evidence-panel">
    <div className="rea-evidence-actions">
      <strong>共享证据 · {page.total}</strong>
      <button className="secondary-button" onClick={() => {setCursor(undefined); setRevision(v => v + 1);}}>刷新</button>
    </div>
    <p className="muted">汇集本工作区主、子 Agent 的 REA 记录，重启后仍可查看。归档不代表结论已验证。</p>
    <select aria-label="证据类型" value={kind} onChange={event => setKind(event.target.value)}>
      <option value="">全部记录</option><option value="evidence">分析证据</option><option value="unknown">未解决问题的变更历史</option>
    </select>
    {error && <p role="alert">{error}</p>}
    {loading ? <p className="muted">正在读取证据…</p> : page.items.length === 0 &&
      <p className="muted">还没有归档记录。后续 REA 分析返回的证据会自动保存。</p>}
    {page.items.map(item => <button className={`rea-evidence-row ${selected === item.id ? 'selected' : ''}`}
      key={item.id} onClick={() => setSelected(item.id)}>
      <strong>{item.operation}</strong>
      <span>{item.subject?.name || (item.kind === 'unknown' ? '问题记录' : '未提供样本身份')} · {item.provider?.name ?? 'REA'}</span>
      <small>{item.agent_id} · {item.kind === 'unknown' ? `${item.status} · 修订 ${item.revision}` : confidence[item.confidence ?? ''] ?? '未标注证据类型'}</small>
    </button>)}
    {page.next_cursor !== null && <button className="secondary-button" disabled={loadingMore}
      onClick={() => setCursor(page.next_cursor ?? undefined)}>{loadingMore ? '读取中…' : '加载更多'}</button>}
    {selected && !record && !error && <p className="muted">正在读取原始记录…</p>}
    {record && <article className="rea-evidence-detail">
      <h3>{record.operation}</h3>
      <dl><dt>归档 ID</dt><dd>{record.id}</dd><dt>上游证据 ID</dt><dd>{record.upstream_id}</dd>
        <dt>来源 Agent / 会话</dt><dd>{record.agent_id} / {record.session_id}</dd>
        <dt>工具调用</dt><dd>{record.call_id}</dd><dt>样本 SHA-256</dt><dd>{record.sample_sha256 ?? '上游未提供'}</dd>
        <dt>证据类型 / 来源</dt><dd>{confidence[record.confidence ?? ''] ?? '未标注'} / {record.authority ?? '未标注'}</dd>
        <dt>位置</dt><dd>{JSON.stringify(record.locations)}</dd></dl>
      {record.limitations.length > 0 && <><h4>适用范围与限制</h4><ul>{record.limitations.map((text, i) => <li key={i}>{text}</li>)}</ul></>}
      <button className="secondary-button" onClick={download}>下载完整原始结果</button>
      <details><summary>原始结果{(record.raw?.length ?? 0) > 20000 ? '（前 20,000 字符）' : ''}</summary>
        <pre>{record.raw?.slice(0, 20000)}</pre></details>
    </article>}
  </section>;
}
