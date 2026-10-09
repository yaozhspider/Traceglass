import { useEffect, useRef, useState } from 'react';
import {
  ArrowLeft,
  Binary,
  ChevronDown,
  FolderOpen,
  MessageSquare,
  Plus,
  RotateCcw,
  Upload,
  X,
} from 'lucide-react';
import { workspaceApi } from './workspaceApi';
import type {
  WorkspaceDetail,
  WorkspaceSample,
  WorkspaceSummary,
  WorkspaceTask,
} from './workspaceApi';
import { formatBytes } from './workspaceData';

type SampleSelection = {
  selectedSample?: WorkspaceSample | null;
  onSelectSample: (sample: WorkspaceSample | null) => void;
};
type Props = SampleSelection & {
  user: string;
  detail: WorkspaceDetail;
  workspaces: WorkspaceSummary[];
  sessionId?: string;
  onUploaded: (sample: WorkspaceSample) => void;
  onOpenWorkspace: (id: string) => void;
  onHome: () => void;
  onOpenSession: (task: WorkspaceTask) => void;
  onNewAnalysis: () => void;
};

export function WorkspaceSidebar({
  user,
  detail,
  workspaces,
  sessionId,
  selectedSample,
  onSelectSample,
  onUploaded,
  onOpenWorkspace,
  onHome,
  onOpenSession,
  onNewAnalysis,
}: Props) {
  const fileInput = useRef<HTMLInputElement>(null);
  const uploadController = useRef<AbortController | null>(null);
  const [uploading, setUploading] = useState(false);
  const [progress, setProgress] = useState(0);
  const [file, setFile] = useState<File | null>(null);
  const [error, setError] = useState('');
  const { workspace, samples, tasks } = detail;
  useEffect(() => {
    setFile(null);
    setError('');
    setUploading(false);
    setProgress(0);
    return () => {
      uploadController.current?.abort();
      uploadController.current = null;
    };
  }, [workspace.workspace_id]);
  const upload = async (next: File) => {
    const controller = new AbortController();
    uploadController.current?.abort();
    uploadController.current = controller;
    setFile(next);
    setUploading(true);
    setProgress(0);
    setError('');
    try {
      const sample = await workspaceApi.upload(
        user,
        workspace.workspace_id,
        next,
        (percent) => {
          if (uploadController.current === controller) setProgress(percent);
        },
        controller.signal,
      );
      if (uploadController.current !== controller) return;
      setFile(null);
      onUploaded(sample);
    } catch (failure) {
      if (uploadController.current === controller)
        setError(
          controller.signal.aborted
            ? '上传已取消，可以重试。'
            : failure instanceof Error
              ? failure.message
              : String(failure),
        );
    } finally {
      if (uploadController.current === controller) {
        uploadController.current = null;
        setUploading(false);
      }
    }
  };
  const chooseFile = () => fileInput.current?.click();

  return (
    <aside className="workspace-sidebar" aria-label="工作区导航">
      <button className="workspace-back" onClick={onHome}>
        <ArrowLeft size={14} />
        所有工作区
      </button>
      <div className="workspace-switch">
        <FolderOpen size={20} />
        <label>
          <small>当前工作区</small>
          <select
            aria-label="切换工作区"
            value={workspace.workspace_id}
            onChange={(event) => onOpenWorkspace(event.target.value)}
          >
            {workspaces.map((item) => (
              <option key={item.workspace_id} value={item.workspace_id}>
                {item.name}
              </option>
            ))}
          </select>
        </label>
        <ChevronDown size={14} />
      </div>
      <button
        className="primary-button workspace-new-analysis"
        onClick={onNewAnalysis}
      >
        <Plus size={16} />
        新建分析
      </button>
      <section className="workspace-conversations">
        <div className="workspace-section-heading">
          <h2>
            分析任务 <span>{tasks.length}</span>
          </h2>
          <MessageSquare size={14} />
        </div>
        {tasks.length ? (
          <div className="workspace-task-list">
            {tasks.map((task) => (
              <button
                className={`workspace-task ${task.session_id === sessionId ? 'selected' : ''}`}
                key={task.session_id}
                onClick={() => onOpenSession(task)}
                aria-current={
                  task.session_id === sessionId ? 'page' : undefined
                }
              >
                <MessageSquare size={15} />
                <span>
                  <strong title={task.name}>{task.name || '未命名分析'}</strong>
                  <small>
                    {new Date(task.created_at).toLocaleDateString('zh-CN', {
                      month: '2-digit',
                      day: '2-digit',
                    })}
                  </small>
                </span>
              </button>
            ))}
          </div>
        ) : (
          <p className="workspace-scope-note">上传样本，开始第一次分析。</p>
        )}
      </section>
      <section className="workspace-samples">
        <div className="workspace-section-heading">
          <h2>
            样本 <span>{samples.length}</span>
          </h2>
          <button
            className="icon-button"
            title="上传样本"
            aria-label="上传样本"
            disabled={uploading}
            onClick={chooseFile}
          >
            <Upload size={16} />
          </button>
        </div>
        <input
          id="workspace-sample-upload"
          ref={fileInput}
          type="file"
          hidden
          onChange={(event) => {
            const next = event.target.files?.[0];
            event.target.value = '';
            if (next) void upload(next);
          }}
        />
        {sessionId && (
          <p className="workspace-scope-note">选择其他样本将开始新的分析。</p>
        )}
        {samples.length ? (
          <div className="workspace-sample-list">
            {samples.map((sample) => (
              <button
                className={`workspace-sample ${selectedSample?.sample_id === sample.sample_id ? 'selected' : ''}`}
                key={sample.sample_id}
                title={`${sample.name}\nSHA-256 ${sample.sha256}`}
                aria-pressed={selectedSample?.sample_id === sample.sample_id}
                onClick={() =>
                  onSelectSample(
                    selectedSample?.sample_id === sample.sample_id
                      ? null
                      : sample,
                  )
                }
              >
                <Binary size={17} />
                <span>
                  <strong>{sample.name}</strong>
                  <small>
                    {formatBytes(sample.size)} · {sample.sha256.slice(0, 8)}
                  </small>
                </span>
              </button>
            ))}
          </div>
        ) : (
          !uploading && (
            <button className="workspace-upload-empty" onClick={chooseFile}>
              <Upload size={20} />
              <strong>上传第一个样本</strong>
              <span>保存在当前工作区 · 最大 100 MiB</span>
            </button>
          )
        )}
        {uploading && (
          <div className="workspace-upload-progress" role="status">
            <div>
              <strong>{file?.name}</strong>
              <button
                className="icon-button"
                aria-label="取消上传"
                onClick={() => uploadController.current?.abort()}
              >
                <X size={14} />
              </button>
            </div>
            <progress value={progress} max={100} aria-label="样本上传进度" />
            <small>
              {progress === 100
                ? '上传完成，正在保存并校验…'
                : `正在上传 ${progress}%`}
            </small>
          </div>
        )}
        {error && (
          <div className="workspace-error" role="alert">
            <span>{error}</span>
            {file && !uploading && (
              <button onClick={() => void upload(file)}>
                <RotateCcw size={13} />
                重试上传
              </button>
            )}
          </div>
        )}
        {samples.length > 0 && !uploading && (
          <button className="workspace-upload-link" onClick={chooseFile}>
            <Upload size={13} />
            上传文件
          </button>
        )}
        {detail.samples_truncated && (
          <p className="workspace-scope-note">
            历史样本较多，此处仅列出已索引的部分；其余文件可在工作区文件中查看。
          </p>
        )}
      </section>
      <footer className="workspace-sidebar-footer">
        溯镜 <span>TRACEGLASS</span>
      </footer>
    </aside>
  );
}

export function SamplePicker({
  samples,
  selectedSample,
  onSelectSample,
  disabled,
}: SampleSelection & { samples: WorkspaceSample[]; disabled?: boolean }) {
  return (
    <div className="workspace-sample-picker">
      <label>
        <Binary size={15} />
        <select
          aria-label="选择分析样本"
          value={selectedSample?.sample_id ?? ''}
          disabled={disabled}
          onChange={(event) =>
            onSelectSample(
              samples.find(
                (sample) => sample.sample_id === event.target.value,
              ) ?? null,
            )
          }
        >
          <option value="">
            {samples.length
              ? '选择样本（可选）'
              : '先在左侧上传样本，也可直接提问'}
          </option>
          {samples.map((sample) => (
            <option key={sample.sample_id} value={sample.sample_id}>
              {sample.name}
            </option>
          ))}
        </select>
      </label>
      {selectedSample && (
        <button
          type="button"
          className="icon-button"
          disabled={disabled}
          aria-label="取消选择样本"
          onClick={() => onSelectSample(null)}
        >
          <X size={14} />
        </button>
      )}
    </div>
  );
}
