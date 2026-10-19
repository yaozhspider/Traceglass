import {AccountControls} from '../account/AccountControls';
import { useEffect, useRef, useState } from 'react';
import {
  ArrowDown,
  Bot,
  FileText,
  Fingerprint,
  FolderOpen,
  GitBranch,
  Info,
  Menu,
  Pause,
  RefreshCw,
  Send,
  Upload,
  X,
} from 'lucide-react';
import { SessionTimeline } from '../execution/SessionTimeline';
import { PermissionPicker } from '../execution/PermissionControl';
import { canPauseExecution } from '../execution/controls';
import { RecordInspector } from '../execution/RecordInspector';
import { ReaEvidencePanel } from '../execution/ReaEvidencePanel';
import { statusLabel } from '../execution/timeline';
import { WorkspaceHome } from './WorkspaceHome';
import { WorkspaceSidebar, SamplePicker } from './WorkspaceSidebar';
import { WorkspaceFiles } from './WorkspaceFiles';
import { useWorkbench } from './useWorkbench';
import {ComposerAttachments} from './ComposerAttachments';
import { WorkspaceFileContext } from './WorkspaceFileContext';
import './workspace.css';
import './workbench.css';

export function WorkbenchShell() {
  const workbench = useWorkbench();
  const { config, detail, active, route, selectedSample, selectedSamples } = workbench;
  const [inspected, setInspected] = useState<unknown>(null);
  const [requestedFile, setRequestedFile] = useState<{
    path: string;
    revision: number;
  } | null>(null);
  const [panel, setPanel] = useState<'files' | 'message' | 'evidence' | null>(null);
  const [sidebar, setSidebar] = useState(false);
  const [atBottom, setAtBottom] = useState(true);
  const scrollRef = useRef<HTMLDivElement>(null);
  const contentRef = useRef<HTMLDivElement>(null);
  const follow = useRef(true);
  const attachmentInput = useRef<HTMLInputElement>(null);
  const dragDepth = useRef(0);
  const [dragging, setDragging] = useState(false);
  const [dropError, setDropError] = useState('');
  useEffect(() => {dragDepth.current = 0; setDragging(false); setDropError('');}, [route.workspace, route.session]);
  useEffect(() => {
    setInspected(null);
    setPanel(null);
    setRequestedFile(null);
    setSidebar(false);
    follow.current = !!route.session;
    const content = contentRef.current,
      scroller = scrollRef.current;
    if (!content || !scroller) return;
    const observer = new ResizeObserver(() => {
      if (follow.current) scroller.scrollTop = scroller.scrollHeight;
    });
    observer.observe(content);
    return () => observer.disconnect();
  }, [route.workspace, route.session]);
  useEffect(() => {
    const close = (event: KeyboardEvent) => {
      if (event.key === 'Escape') {
        setPanel(null);
        setSidebar(false);
      }
    };
    window.addEventListener('keydown', close);
    return () => window.removeEventListener('keydown', close);
  }, []);
  const inspect = (value: unknown) => {
    setInspected(value);
    setPanel('message');
  };

  if (!route.workspace && !route.session)
    return (
      <WorkspaceHome
        workspaces={workbench.workspaces}
        loading={workbench.loading}
        error={workbench.error}
        onOpenWorkspace={workbench.openWorkspace}
        onCreate={workbench.createWorkspace}
        onRetry={() => void workbench.refresh()}
      />
    );
  const canSend = !!(
    config?.model_configured || active?.session.config.chat_model_config
  );
  const running =
    active?.status === 'running' || active?.status === 'preparing';
  const sendingDisabled =
    !canSend ||
    !detail ||
    workbench.workspaceLoading ||
    workbench.busy ||
    !!workbench.deletingSample ||
    workbench.attachments.queue.blocked() ||
    workbench.permissionSaving ||
    workbench.pausing ||
    running ||
    active?.status === 'awaiting_permission' ||
    (!route.session && !!route.sample && !selectedSample);
  const members = active?.team?.members ?? [];
  const canPause = canPauseExecution(active?.status,
    members.map((m) => m.session_id ? workbench.runtime?.members?.[m.session_id] : undefined));
  const childrenRunning = members.some(
    (m) =>
      m.session_id &&
      ['running', 'preparing'].includes(
        workbench.runtime?.members?.[m.session_id] ?? '',
      ),
  );
  const status = active
    ? active.status === 'idle' && childrenRunning
      ? '子 Agent 执行中'
      : statusLabel(active.status)
    : '准备就绪';
  const latest = () => {
    follow.current = true;
    const el = scrollRef.current;
    if (el) el.scrollTop = el.scrollHeight;
    setAtBottom(true);
  };

  return (
    <WorkspaceFileContext.Provider
      value={(path) => {
        setRequestedFile({ path, revision: Date.now() });
        setPanel('files');
      }}
    >
      <div
        className={`app-shell native-shell product-workbench ${sidebar ? 'sidebar-open' : ''} ${panel ? 'panel-open' : ''}`}
      >
        <nav className="nav-rail" aria-label="主导航">
          <button
            className="brand-mark"
            title="所有工作区"
            onClick={() => workbench.openWorkspace(null)}
          >
            溯
          </button>
          <button
            className="nav-button active"
            aria-label="所有工作区"
            onClick={() => workbench.openWorkspace(null)}
          >
            <FolderOpen size={19} />
          </button>
          <span className="rail-wordmark">TRACEGLASS</span>
        </nav>
        {sidebar && (
          <button
            className="sidebar-scrim"
            aria-label="关闭工作区导航"
            onClick={() => setSidebar(false)}
          />
        )}
        {config && detail ? (
          <WorkspaceSidebar
            user={config.user_id}
            detail={detail}
            workspaces={workbench.workspaces}
            sessionId={route.session ?? undefined}
            selectedSamples={selectedSamples}
            onSelectSamples={workbench.selectSamples}
            selectionDisabled={workbench.busy || workbench.workspaceLoading}
            deleteDisabled={workbench.busy || workbench.workspaceLoading || !!Object.keys(workbench.runtime?.sessions ?? {}).length || Object.values(workbench.runtime?.members ?? {}).some(status => status !== 'idle')}
            deletingSample={workbench.deletingSample}
            onUploaded={(sample) =>
              workbench.sampleUploaded(sample, detail.workspace.workspace_id)
            }
            onDeleteSample={workbench.deleteSample}
            onOpenWorkspace={workbench.openWorkspace}
            onHome={() => workbench.openWorkspace(null)}
            onOpenSession={(task) => workbench.openSession(task.session_id)}
            onNewAnalysis={workbench.newAnalysis}
          />
        ) : (
          <aside className="workspace-sidebar">
            <button
              className="secondary-button"
              onClick={() => workbench.openWorkspace(null)}
            >
              所有工作区
            </button>
            <p className="muted">
              {workbench.workspaceLoading || workbench.loading
                ? '正在读取工作区…'
                : '工作区暂时不可用'}
            </p>
          </aside>
        )}
        <main className="workspace-main">
          <header className="workspace-header">
            <button
              className="icon-button sidebar-toggle"
              aria-label="展开工作区导航"
              onClick={() => setSidebar(!sidebar)}
            >
              <Menu size={19} />
            </button>
            <div className="workspace-title">
              <div className="eyebrow">
                {detail?.workspace.name ?? 'TRACEGLASS'}
              </div>
              <h1>{active?.session.config.name ?? '新建分析'}</h1>
            </div>
            <div className="header-actions">
              <AccountControls/>
              <button className="secondary-button" aria-expanded={panel === 'evidence'}
                onClick={() => setPanel(panel === 'evidence' ? null : 'evidence')}>
                <Fingerprint size={15} />证据
              </button>
              <span className={`live-status ${active?.status ?? ''}`}>
                <i />
                {status}
              </span>
              <button
                className="icon-button"
                aria-label="刷新工作区"
                onClick={() => void workbench.refresh()}
              >
                <RefreshCw size={16} />
              </button>
              <button
                className="secondary-button file-toggle"
                aria-expanded={panel === 'files'}
                onClick={() => setPanel(panel === 'files' ? null : 'files')}
              >
                <FolderOpen size={15} />
                文件
              </button>
            </div>
          </header>
          <div className="workspace-context">
            {selectedSamples.length ? (
              <span
                className="sample-identity"
                title={selectedSamples.map(sample => `${sample.name}\nSHA-256 ${sample.sha256}`).join('\n\n')}
              >
                <FileText size={13} />
                <strong>{selectedSamples.length === 1 ? selectedSamples[0]?.name : `${selectedSamples.length} 个样本`}</strong>
                <code>{selectedSamples.length === 1 ? selectedSamples[0]?.sha256.slice(0, 12) : selectedSamples.map(sample => sample.name).join('、')}</code>
              </span>
            ) : (
              <span className="context-label">分析工作台</span>
            )}
            {config?.integrations && (
              <details className="integration-summary">
                <summary>
                  <Info size={13} />
                  {config.model_config?.model ?? '模型未配置'}
                </summary>
                <div>
                  <p>
                    {config.integrations.workspace_backend === 'docker'
                      ? 'Docker 分析环境'
                      : '本地工作区'}{' '}
                    · {config.integrations.skills.length} 个 Skill
                  </p>
                  <p>
                    MCP：
                    {config.integrations.mcp_servers.join('、') || '未配置'}
                  </p>
                  {detail?.workspace.is_legacy && (
                    <p>已有会话沿用原文件目录。</p>
                  )}
                  <p>Skills：{config.integrations.skills.join('、')}</p>
                </div>
              </details>
            )}
          </div>
          <div
            className="conversation-scroll"
            onClickCapture={(e) => {
              if ((e.target as HTMLElement).closest('summary'))
                follow.current = false;
            }}
            ref={scrollRef}
            onScroll={(e) => {
              const el = e.currentTarget;
              const bottom =
                el.scrollHeight - el.scrollTop - el.clientHeight < 70;
              follow.current = bottom;
              setAtBottom(bottom);
            }}
          >
            <div className="conversation-content" ref={contentRef}>
              {workbench.error && (
                <div className="error-banner" role="alert">
                  {workbench.error}
                  <button
                    className="icon-button"
                    onClick={() => void workbench.refresh()}
                  >
                    重试
                  </button>
                </div>
              )}
              {!canSend && config && (
                <div className="error-banner">
                  尚未配置模型。你可以先上传和查看文件。
                </div>
              )}
              {workbench.runtime?.integrations
                .filter((item) => item.status === 'unavailable')
                .map((item) => (
                  <details key={item.name} className="integration-warning">
                    <summary>{item.name} 暂不可用</summary>
                    <p>{item.message}</p>
                  </details>
                ))}
              {active?.status === 'preparing' && (
                <p className="workspace-preparing" role="status">
                  正在准备分析环境与工具…
                </p>
              )}
              {config && active ? (
                <SessionTimeline
                  key={active.session.id}
                  user={config.user_id}
                  session={active.session}
                  label="主 Agent"
                  onInspect={inspect}
                  running={running}
                  pendingInput={workbench.pendingInput?.text}
                  pendingInputId={workbench.pendingInput?.id}
                  onInputRecorded={() =>
                    workbench.inputRecorded(active.session.id)
                  }
                  initialTasks={active.session.state.tasks_context?.tasks ?? []}
                  tasksSettledAt={active.tasksSettledAt}
                  team={{
                    members,
                    statuses: workbench.runtime?.members,
                    limit: config.orchestration?.max_subagents ?? 8,
                  }}
                />
              ) : (
                <section className="analysis-welcome">
                  <div className="welcome-symbol">溯</div>
                  <div className="eyebrow">TRACEGLASS · 溯镜</div>
                  <h2>
                    每个结论，
                    <br />
                    都有迹可循。
                  </h2>
                  <p>
                    从样本出发，拆解问题、协作分析、复核证据。
                    <br />
                    分析过程和每位 Agent 的工作都在这里。
                  </p>
                  <div className="analysis-guides">
                    <button
                      onClick={() =>
                        workbench.setPrompt(
                          '识别选中样本的文件类型、架构与基本信息，记录哈希和可复核证据。',
                        )
                      }
                    >
                      <FileText size={18} />
                      <span>
                        样本速览<small>类型、架构、基础线索</small>
                      </span>
                    </button>
                    <button
                      onClick={() =>
                        workbench.setPrompt(
                          '帮我深入逆向分析选中样本并给出详细报告，说明关键函数、行为、证据和不确定性。',
                        )
                      }
                    >
                      <Bot size={18} />
                      <span>
                        深入分析<small>任务分解、专家协作、证据复核</small>
                      </span>
                    </button>
                  </div>
                  {!!detail?.tasks.length && (
                    <div className="resume-analysis">
                      <span>继续已有分析</span>
                      {detail.tasks.slice(0, 3).map((task) => (
                        <button
                          key={task.session_id}
                          onClick={() => workbench.openSession(task.session_id)}
                        >
                          {task.name}
                          <span>打开 →</span>
                        </button>
                      ))}
                    </div>
                  )}
                </section>
              )}
            </div>
          </div>
          <div className="composer-dock">
            {active?.status === 'awaiting_permission' && (
              <button
                className="permission-jump"
                onClick={() =>
                  document
                    .getElementById(`permission-${active.session.id}`)
                    ?.scrollIntoView({ block: 'start' })
                }
              >
                主 Agent 等待授权 · 查看待确认工具
              </button>
            )}
            {members.some(
              (m) =>
                m.session_id &&
                workbench.runtime?.members?.[m.session_id] ===
                  'awaiting_permission',
            ) && (
              <button
                className="permission-jump"
                onClick={() =>
                  document
                    .getElementById(`team-${active?.session.id}`)
                    ?.scrollIntoView({ block: 'start' })
                }
              >
                子 Agent 等待授权 · 查看协作分工
              </button>
            )}
            {!atBottom && (
              <button className="jump-latest" onClick={latest}>
                <ArrowDown size={14} />
                最新进展
              </button>
            )}
            <form
              className={`composer${dragging ? ' composer-dragging' : ''}`}
              aria-label="对话输入框"
              onDragEnter={event => {
                if (!event.dataTransfer.types.includes('Files')) return;
                event.preventDefault();
                dragDepth.current++;
                setDragging(true);
              }}
              onDragOver={event => {
                if (!event.dataTransfer.types.includes('Files')) return;
                event.preventDefault();
                event.dataTransfer.dropEffect = workbench.busy || !detail || workbench.workspaceLoading ? 'none' : 'copy';
              }}
              onDragLeave={event => {
                event.preventDefault();
                dragDepth.current = Math.max(0, dragDepth.current - 1);
                if (!dragDepth.current) setDragging(false);
              }}
              onDrop={event => {
                if (!event.dataTransfer.types.includes('Files')) return;
                event.preventDefault();
                dragDepth.current = 0; setDragging(false);
                if (workbench.busy || !detail || workbench.workspaceLoading) return;
                const items = Array.from(event.dataTransfer.items);
                const directories = items.filter(item => item.webkitGetAsEntry?.()?.isDirectory);
                const files = items.length ? items.filter(item => item.kind === 'file' && !directories.includes(item))
                  .map(item => item.getAsFile()).filter((file): file is File => !!file) : Array.from(event.dataTransfer.files);
                setDropError(directories.length ? '暂不支持拖入文件夹，请选择其中的文件。' : '');
                workbench.attachments.queue.add(files);
              }}
              onSubmit={(event) => {
                event.preventDefault();
                latest();
                void workbench.submit();
              }}
            >
              {dragging && <div className="composer-drop-hint" role="status">松开以添加文件 · 支持多选</div>}
              {dropError && <p role="alert" className="composer-drop-error">{dropError}</p>}
              <input ref={attachmentInput} type="file" multiple hidden aria-label="选择对话附件"
                disabled={!detail || workbench.busy || workbench.workspaceLoading}
                onChange={event => {
                  const files = Array.from(event.target.files ?? []);
                  event.target.value = '';
                  setDropError('');
                  workbench.attachments.queue.add(files);
                }} />
              <ComposerAttachments items={workbench.attachments.items} disabled={workbench.busy}
                remove={id => workbench.attachments.queue.remove(id)} retry={id => workbench.attachments.queue.retry(id)} />
              {workbench.pendingInput && !running && (
                <button
                  className="secondary-button pending-retry"
                  type="button"
                  onClick={() =>
                    workbench.setPrompt(workbench.pendingInput!.text)
                  }
                >
                  重新填写已提交的问题
                </button>
              )}
              <label className="sr-only" htmlFor="task-input">
                {route.session ? '继续分析' : '分析目标'}
              </label>
              <textarea
                id="task-input"
                value={workbench.prompt}
                onChange={(event) => workbench.setPrompt(event.target.value)}
                onKeyDown={(event) => {
                  if (
                    event.key === 'Enter' &&
                    (event.metaKey || event.ctrlKey) &&
                    !event.nativeEvent.isComposing
                  ) {
                    event.preventDefault();
                    if (!sendingDisabled) {
                      latest();
                      void workbench.submit();
                    }
                  }
                }}
                placeholder={
                  running
                    ? 'Agent 正在分析，可以先写下下一步问题…'
                    : selectedSample
                      ? '描述你想确认的问题，或要求一份完整分析报告…'
                      : '输入问题，或拖入文件一起分析…'
                }
                rows={2}
              />
              <div className="composer-permissions">
                <PermissionPicker
                  mode={workbench.permissionMode}
                  onChange={(mode) => void workbench.changePermission(mode)}
                  status={active?.status}
                  disabled={workbench.busy || workbench.workspaceLoading}
                  saving={workbench.permissionSaving}
                />
                {active ? (
                  <button type="button" className="pause-all" disabled={!canPause || workbench.pausing}
                    onClick={() => void workbench.pauseAll()}
                    title="暂停主 Agent、子 Agent 及其后台工具；保留已有记录，之后可继续执行。">
                    <Pause size={13} />{workbench.pausing ? '正在暂停…' : '暂停全部'}
                  </button>
                ) : <span className="permission-scope" title="新建子 Agent 继承主 Agent 的权限；已有成员可在展开面板中单独切换。">
                  主 Agent 与新建子 Agent
                </span>}
              </div>
              <div className="composer-footer">
                <div>
                  <button
                    className="icon-button"
                    type="button"
                    aria-label="添加附件"
                    title="选择或拖入多个文件，每个文件最多 100 MiB"
                    disabled={!detail || workbench.workspaceLoading || workbench.busy}
                    onClick={() => attachmentInput.current?.click()}
                  >
                    <Upload size={15} />
                  </button>
                  {detail && (
                    <SamplePicker
                      samples={detail.samples}
                      selectedSamples={selectedSamples}
                      onSelectSamples={workbench.selectSamples}
                      disabled={workbench.busy || running}
                    />
                  )}
                  {active ? (
                    <button
                      className="composer-team"
                      type="button"
                      disabled={!members.length}
                      onClick={() =>
                        document
                          .getElementById(`team-${active.session.id}`)
                          ?.scrollIntoView({ block: 'start' })
                      }
                    >
                      <GitBranch size={14} />
                      {members.length
                        ? `协作 ${members.length} / ${config?.orchestration?.max_subagents ?? 8}`
                        : '尚未委派'}
                    </button>
                  ) : (
                    <span className="composer-hint">
                      主 Agent 规划 · 最多{' '}
                      {config?.orchestration?.max_subagents ?? 8} 位专家
                    </span>
                  )}
                </div>
                <button
                  className="primary-button"
                  disabled={sendingDisabled || !workbench.prompt.trim()}
                  type="submit"
                  title="⌘ / Ctrl + Enter"
                >
                  <Send size={15} />
                  {workbench.busy ? '提交中' : '发送'}
                </button>
              </div>
            </form>
            <div className="composer-note">
              {config?.orchestration?.max_iters ? `默认每轮最多 ${config.orchestration.max_iters} 步 · ` : ''}
              结论需要证据支持
            </div>
          </div>
        </main>
        {panel && (
          <aside
            className="workspace-file-panel"
            aria-label="工作区文件与消息详情"
          >
            <div
              className="workspace-panel-tabs"
              role="tablist"
              aria-label="查看内容"
            >
              <button
                role="tab"
                aria-selected={panel === 'files'}
                onClick={() => setPanel('files')}
              >
                <FolderOpen size={15} />
                工作区文件
              </button>
              <button
                role="tab"
                aria-selected={panel === 'message'}
                onClick={() => setPanel('message')}
              >
                <Info size={15} />
                记录详情
              </button>
              <button role="tab" aria-selected={panel === 'evidence'} onClick={() => setPanel('evidence')}>
                <Fingerprint size={15} />共享证据
              </button>
              <button
                className="icon-button panel-close"
                aria-label="关闭侧面板"
                onClick={() => setPanel(null)}
              >
                <X size={17} />
              </button>
            </div>
            {panel === 'evidence' && config && route.workspace ? (
              <ReaEvidencePanel key={route.workspace} user={config.user_id} workspaceId={route.workspace}
                refreshKey={active?.status ?? 'draft'} />
            ) : panel === 'files' && config && route.workspace ? (
              <WorkspaceFiles
                key={route.workspace}
                user={config.user_id}
                workspaceId={route.workspace}
                requestedFile={requestedFile}
                refreshKey={`${active?.status ?? 'draft'}:${detail?.samples.length ?? 0}`}
              />
            ) : (
              <RecordInspector value={inspected} />
            )}
          </aside>
        )}
      </div>
    </WorkspaceFileContext.Provider>
  );
}
