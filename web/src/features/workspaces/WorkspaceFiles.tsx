import { useEffect, useState } from 'react';
import {
  ArrowUp,
  ChevronRight,
  Download,
  File,
  FileText,
  Folder,
  FolderOpen,
  RefreshCw,
  Search,
  X,
} from 'lucide-react';
import { workspaceApi } from './workspaceApi';
import type {
  WorkspaceDirectory,
  WorkspaceEntry,
  WorkspacePreview,
} from './workspaceApi';
import { Markdown } from '../execution/Markdown';
import { formatBytes, parentDirectory, pathCrumbs } from './workspaceData';

type Props = {
  user: string;
  workspaceId: string;
  requestedFile?: { path: string; revision: number } | null;
  refreshKey?: string | number;
};

export function WorkspaceFiles({
  user,
  workspaceId,
  refreshKey,
  requestedFile,
}: Props) {
  const [path, setPath] = useState('');
  const [directory, setDirectory] = useState<WorkspaceDirectory | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [revision, setRevision] = useState(0);
  const [search, setSearch] = useState('');
  const [selected, setSelected] = useState<WorkspaceEntry | null>(null);
  const [preview, setPreview] = useState<WorkspacePreview | null>(null);
  const [previewLoading, setPreviewLoading] = useState(false);
  const [previewError, setPreviewError] = useState('');
  const [downloading, setDownloading] = useState(false);
  const [downloadError, setDownloadError] = useState('');
  useEffect(() => {
    setPath('');
    setSelected(null);
    setSearch('');
  }, [workspaceId]);
  useEffect(() => {
    if (requestedFile) {
      setPath(parentDirectory(requestedFile.path));
      setSelected({
        path: requestedFile.path,
        name: requestedFile.path.split('/').pop()!,
        is_dir: false,
        size_bytes: 0,
      });
      setSearch('');
    }
  }, [requestedFile]);
  useEffect(() => {
    const controller = new AbortController();
    setLoading(true);
    setError('');
    void workspaceApi
      .files(user, workspaceId, path, controller.signal)
      .then((value) => {
        if (!controller.signal.aborted) setDirectory(value);
      })
      .catch((failure) => {
        if (!controller.signal.aborted) {
          setDirectory(null);
          setError(
            failure instanceof Error ? failure.message : String(failure),
          );
        }
      })
      .finally(() => {
        if (!controller.signal.aborted) setLoading(false);
      });
    return () => controller.abort();
  }, [user, workspaceId, path, refreshKey, revision]);
  useEffect(() => {
    setPreview(null);
    setPreviewError('');
    setDownloadError('');
    if (!selected) {
      setPreviewLoading(false);
      return;
    }
    const controller = new AbortController();
    setPreviewLoading(true);
    void workspaceApi
      .preview(user, workspaceId, selected.path, controller.signal)
      .then((value) => {
        if (!controller.signal.aborted) setPreview(value);
      })
      .catch((failure) => {
        if (!controller.signal.aborted)
          setPreviewError(
            failure instanceof Error ? failure.message : String(failure),
          );
      })
      .finally(() => {
        if (!controller.signal.aborted) setPreviewLoading(false);
      });
    return () => controller.abort();
  }, [user, workspaceId, selected?.path, refreshKey, revision]);

  const browse = (next: string) => {
    setPath(next);
    setSelected(null);
    setSearch('');
  };
  const download = async () => {
    if (!selected || downloading) return;
    setDownloading(true);
    setDownloadError('');
    try {
      await workspaceApi.download(
        user,
        workspaceId,
        selected.path,
        selected.name,
      );
    } catch (failure) {
      setDownloadError(
        failure instanceof Error ? failure.message : String(failure),
      );
    } finally {
      setDownloading(false);
    }
  };
  const entries = (directory?.entries ?? [])
    .filter((entry) =>
      entry.name
        .toLocaleLowerCase()
        .includes(search.trim().toLocaleLowerCase()),
    )
    .slice()
    .sort(
      (a, b) =>
        Number(b.is_dir) - Number(a.is_dir) ||
        a.name.localeCompare(b.name, 'zh-CN'),
    );
  const shownPath = directory && !loading ? directory.path : path;

  return (
    <section className="workspace-files" aria-label="工作区文件浏览器">
      <div className="workspace-files-heading">
        <div>
          <h2>文件</h2>
          <p>样本、分析脚本和生成的报告</p>
        </div>
        <button
          className="icon-button"
          aria-label="刷新文件"
          title="刷新文件"
          disabled={loading}
          onClick={() => setRevision((value) => value + 1)}
        >
          <RefreshCw size={16} />
        </button>
      </div>
      <nav className="workspace-file-breadcrumb" aria-label="文件路径">
        <button onClick={() => browse('')} aria-label="工作区根目录">
          <FolderOpen size={14} />
          根目录
        </button>
        {pathCrumbs(shownPath).map((crumb) => (
          <span key={crumb.path}>
            <ChevronRight size={12} />
            <button onClick={() => browse(crumb.path)} title={crumb.path}>
              {crumb.name}
            </button>
          </span>
        ))}
      </nav>
      <div className="workspace-file-controls">
        <button
          className="icon-button"
          title="返回上级目录"
          aria-label="返回上级目录"
          disabled={!path || loading}
          onClick={() => browse(parentDirectory(shownPath))}
        >
          <ArrowUp size={16} />
        </button>
        <label className="workspace-search">
          <Search size={14} />
          <input
            aria-label="搜索当前目录"
            placeholder="搜索当前目录…"
            value={search}
            onChange={(event) => setSearch(event.target.value)}
          />
        </label>
      </div>
      {error && (
        <div className="workspace-error" role="alert">
          {error}
          <button onClick={() => setRevision((value) => value + 1)}>
            重新读取
          </button>
        </div>
      )}
      {loading ? (
        <p className="workspace-file-status" role="status">
          正在读取文件…
        </p>
      ) : (
        !error && (
          <div className="workspace-file-list">
            {entries.map((entry) => (
              <button
                key={entry.path}
                className={`workspace-file-entry ${selected?.path === entry.path ? 'selected' : ''}`}
                onClick={() =>
                  entry.is_dir ? browse(entry.path) : setSelected(entry)
                }
                title={entry.path}
                aria-label={
                  entry.is_dir
                    ? `打开目录 ${entry.name}`
                    : `预览文件 ${entry.name}`
                }
                aria-pressed={
                  !entry.is_dir ? selected?.path === entry.path : undefined
                }
              >
                {entry.is_dir ? (
                  <Folder size={16} />
                ) : /\.(md|txt|json|log|csv)$/i.test(entry.name) ? (
                  <FileText size={16} />
                ) : (
                  <File size={16} />
                )}
                <span>{entry.name}</span>
                {entry.is_dir ? (
                  <ChevronRight size={13} />
                ) : (
                  <small>{formatBytes(entry.size_bytes)}</small>
                )}
              </button>
            ))}
            {!entries.length && (
              <p className="workspace-file-status">
                {search
                  ? '当前目录没有匹配的文件。'
                  : '此目录暂无文件，上传样本或开始分析后会显示在这里。'}
              </p>
            )}
          </div>
        )
      )}
      {directory?.truncated && (
        <p className="workspace-file-note">
          目录较大，仅显示部分条目。搜索只覆盖已列出的文件。
        </p>
      )}
      {selected && (
        <section className="workspace-file-preview" aria-label="文件预览">
          <header>
            <div>
              <strong>{selected.name}</strong>
              <small>
                {formatBytes(preview?.size ?? selected.size_bytes)} ·{' '}
                {preview?.encoding === 'binary' ? '二进制' : '只读预览'}
              </small>
            </div>
            <button
              className="icon-button"
              aria-label="关闭文件预览"
              onClick={() => setSelected(null)}
            >
              <X size={16} />
            </button>
          </header>
          <p className="workspace-preview-path">{selected.path}</p>
          <div className="workspace-preview-content">
            {previewLoading ? (
              <p className="workspace-file-status" role="status">
                正在读取预览…
              </p>
            ) : previewError ? (
              <div className="workspace-error" role="alert">
                {previewError}
                <button onClick={() => setRevision((value) => value + 1)}>
                  重试预览
                </button>
              </div>
            ) : (
              preview &&
              (preview.encoding === 'binary' ? (
                preview.hex ? (
                  <pre className="workspace-hex" data-testid="file-preview-hex">
                    {preview.hex}
                  </pre>
                ) : (
                  <div className="workspace-file-status">
                    <BinaryFileIcon />
                    <p>此文件为二进制格式，可下载原始文件。</p>
                  </div>
                )
              ) : /\.md$/i.test(selected.name) ? (
                <div className="report-preview">
                  <Markdown text={preview.content} baseFile={selected.path} />
                </div>
              ) : (
                <pre data-testid="file-preview-text">
                  <code>{preview.content}</code>
                </pre>
              ))
            )}
          </div>
          <footer>
            <span>
              {preview?.truncated
                ? preview.encoding === 'binary'
                  ? '十六进制预览 · 前 4 KiB'
                  : '仅预览文件前 256 KiB'
                : '原始内容 · 只读'}
            </span>
            <button
              className="secondary-button"
              disabled={downloading}
              onClick={() => void download()}
            >
              <Download size={14} />
              {downloading ? '下载中…' : '下载原文件'}
            </button>
          </footer>
          {downloadError && (
            <div className="workspace-error" role="alert">
              {downloadError}
            </div>
          )}
        </section>
      )}
    </section>
  );
}

function BinaryFileIcon() {
  return <File size={26} aria-hidden="true" />;
}
