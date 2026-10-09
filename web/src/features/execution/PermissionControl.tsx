import { useEffect, useRef, useState } from 'react';
import { ShieldCheck, Zap } from 'lucide-react';
import { request, setSessionPermission } from './nativeApi';
import type { ExecutionPermission, SessionRef, SessionView } from './nativeApi';

export function PermissionPicker({ mode, onChange, status, disabled, saving, label = '执行权限' }: {
  mode: string | null;
  onChange: (mode: ExecutionPermission) => void;
  status?: string;
  disabled?: boolean;
  saving?: boolean;
  label?: string;
}) {
  const running = status === 'running' || status === 'preparing';
  const standard = mode === 'default' || mode === 'bypass';
  const hint = saving ? '正在保存…'
    : running ? '执行中 · 暂停后可切换'
    : mode === 'bypass' && status === 'awaiting_permission' ? '已发出的请求仍需批准一次'
    : mode === 'bypass' ? '自动执行 · 保留明确的禁止与审批规则'
    : '需要授权的操作执行前询问';
  return (
    <div className={`permission-control ${mode === 'bypass' ? 'permission-auto' : ''}`}>
      <label className="permission-picker">
        {mode === 'bypass' ? <Zap size={14} /> : <ShieldCheck size={14} />}
        <span>{label}</span>
        <select aria-label={label} value={mode ?? ''}
          disabled={disabled || saving || running || mode === null}
          onChange={(event) => onChange(event.target.value as ExecutionPermission)}>
          {!standard && <option value={mode ?? ''} disabled>{mode === null ? '读取中…' : `当前：${mode}`}</option>}
          <option value="default">请求批准</option>
          <option value="bypass">全部执行</option>
        </select>
      </label>
      <small role="status">{hint}</small>
    </div>
  );
}

/** Existing members own native session permission contexts independently. */
export function MemberPermission({ user, session, status, name }: {
  user: string; session: SessionRef; status?: string; name: string;
}) {
  const [snapshot, setSnapshot] = useState<SessionView | null>(null);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const revision = useRef(0);
  const mounted = useRef(false);
  useEffect(() => {
    mounted.current = true;
    const controller = new AbortController();
    let fetching = false;
    const refresh = async () => {
      if (fetching) return;
      fetching = true;
      const started = revision.current;
      try {
        const data = await request<{ sessions: SessionView[] }>(
          `/sessions/?agent_id=${encodeURIComponent(session.agent_id)}`, user, {signal: controller.signal});
        if (!controller.signal.aborted && started === revision.current) {
          const found = data.sessions.find((item) => item.session.id === session.id);
          if (!found) throw new Error('成员会话不存在或不可访问。');
          setSnapshot(found);
        }
      } catch (e) {
        if (!controller.signal.aborted) setError(String(e));
      } finally { fetching = false; }
    };
    void refresh();
    const timer = window.setInterval(() => void refresh(), 2500);
    return () => { mounted.current = false; controller.abort(); clearInterval(timer); };
  }, [user, session.id, session.agent_id]);

  const change = async (mode: ExecutionPermission) => {
    if (saving) return;
    setSaving(true);
    setError(null);
    ++revision.current;
    try {
      const updated = await setSessionPermission(user, session, mode);
      ++revision.current;
      if (mounted.current) setSnapshot((value) => value ? {...value, session: updated} : value);
    } catch (e) {
      if (mounted.current) setError(String(e));
    } finally {
      if (mounted.current) setSaving(false);
    }
  };
  return <div className="member-permissions">
    <PermissionPicker mode={snapshot?.session.state.permission_context?.mode ?? null}
      onChange={(mode) => void change(mode)} status={status ?? snapshot?.status}
      saving={saving} label={`${name} 的权限`} />
    {error && <p role="alert" className="permission-error">{error}</p>}
  </div>;
}
