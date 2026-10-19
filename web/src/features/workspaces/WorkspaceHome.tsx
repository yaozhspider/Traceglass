import {AccountControls} from '../account/AccountControls';
import {useEffect, useRef, useState} from 'react';
import {ArrowRight, ArrowUpRight, FolderOpen, Plus, RefreshCw, Search, X} from 'lucide-react';
import type {WorkspaceSummary} from './workspaceApi';

type Props = {
  workspaces: WorkspaceSummary[]; loading: boolean; error: string | null;
  onOpenWorkspace: (id: string) => void; onCreate: (name: string, description: string) => Promise<void>;
  onRetry: () => void;
};

export function WorkspaceHome({workspaces, loading, error, onOpenWorkspace, onCreate, onRetry}: Props) {
  const [query, setQuery] = useState('');
  const [creating, setCreating] = useState(false);
  const [name, setName] = useState('');
  const [description, setDescription] = useState('');
  const [saving, setSaving] = useState(false);
  const [formError, setFormError] = useState('');
  const dialog = useRef<HTMLDialogElement>(null);
  useEffect(() => {if (creating) dialog.current?.showModal(); else dialog.current?.close();}, [creating]);
  const openCreate = () => {setName(''); setDescription(''); setFormError(''); setCreating(true);};
  const submit = async (event: React.FormEvent) => {
    event.preventDefault();
    if (!name.trim() || saving) return;
    setSaving(true); setFormError('');
    try {await onCreate(name.trim(), description.trim()); setCreating(false);}
    catch (failure) {setFormError(failure instanceof Error ? failure.message : String(failure));}
    finally {setSaving(false);}
  };
  const shown = workspaces.filter(workspace => `${workspace.name} ${workspace.description}`.toLocaleLowerCase().includes(query.trim().toLocaleLowerCase()));

  return <div className="workspace-overview">
    <nav className="workspace-home-rail" aria-label="主导航"><span className="brand-mark">TG</span><FolderOpen size={23}/><span>工作区</span></nav>
    <main className="workspace-home-body">
      <header className="workspace-home-top"><strong>溯镜 <span>TRACEGLASS</span></strong><AccountControls/></header>
      <section className="workspace-home-intro"><div><div className="eyebrow">YOUR INVESTIGATIONS</div><h1>每条线索，都有归处。</h1><p>用工作区组织样本、分析对话与证据，接着上一次发现继续。</p></div><button className="primary-button" onClick={openCreate}><Plus size={17}/>新建工作区</button></section>
      <div className="workspace-home-list-heading"><h2>工作区 <span>{workspaces.length}</span></h2><label className="workspace-search"><Search size={16}/><input aria-label="搜索工作区" placeholder="搜索工作区…" value={query} onChange={event => setQuery(event.target.value)}/></label></div>
      {error && <div className="workspace-error" role="alert">{error}<button onClick={onRetry}><RefreshCw size={14}/>重试</button></div>}
      {loading ? <div className="workspace-status" role="status">正在读取工作区…</div> : <div className="workspace-grid">
        {shown.map(workspace => <button className="workspace-card" key={workspace.workspace_id} onClick={() => onOpenWorkspace(workspace.workspace_id)} aria-label={`打开工作区 ${workspace.name}`}>
          <div className="workspace-card-top"><span><FolderOpen size={22}/></span><small>{workspace.is_legacy ? '已有分析' : 'WORKSPACE'}</small><ArrowUpRight size={19}/></div>
          <h3>{workspace.name}</h3><p>{workspace.description || (workspace.is_legacy ? '沿用已有分析会话与文件。' : '汇集样本与证据，开始新的分析。')}</p>
          <div className="workspace-card-counts"><span><b>{workspace.sample_count}</b> 样本</span><span><b>{workspace.task_count}</b> 分析任务</span></div>
          <footer>更新于 {new Date(workspace.updated_at).toLocaleDateString('zh-CN')}</footer>
        </button>)}
        {!query && <button className="workspace-create-card" onClick={openCreate}><Plus size={25}/><strong>建立新的分析工作区</strong><span>上传样本，记录每一步发现 <ArrowRight size={15}/></span></button>}
      </div>}
      {!loading && query && !shown.length && <p className="workspace-status">没有找到匹配的工作区。</p>}
      <footer className="workspace-home-footer"><span>TRACEGLASS / ANALYSIS WORKSPACE</span><span>样本 · 待办 · 证据 · 结论</span></footer>
    </main>
    <dialog ref={dialog} className="workspace-dialog" aria-labelledby="create-workspace-title" aria-describedby="create-workspace-description" onCancel={event => {if (saving) event.preventDefault(); else setCreating(false);}} onClose={() => setCreating(false)}>
      <div className="workspace-dialog-heading"><FolderOpen size={25}/><button type="button" className="icon-button" aria-label="关闭创建工作区" disabled={saving} onClick={() => setCreating(false)}><X size={18}/></button></div>
      <h2 id="create-workspace-title">新建工作区</h2><p id="create-workspace-description">样本、分析任务和产物将在这里独立保存。</p>
      <form onSubmit={event => void submit(event)}><label>工作区名称<input autoFocus required maxLength={80} placeholder="例如：固件安全分析" value={name} onChange={event => setName(event.target.value)}/></label><label>描述 <small>可选</small><textarea maxLength={500} rows={3} placeholder="记录分析目标或项目背景" value={description} onChange={event => setDescription(event.target.value)}/></label>
        {formError && <p className="workspace-error" role="alert">{formError}</p>}
        <div className="workspace-dialog-actions"><button type="button" className="secondary-button" disabled={saving} onClick={() => setCreating(false)}>取消</button><button className="primary-button" disabled={saving || !name.trim()}>{saving ? '创建中…' : '创建工作区'}<ArrowRight size={15}/></button></div>
      </form>
    </dialog>
  </div>;
}
