import {FileText, RotateCcw, X} from 'lucide-react';
import type {Attachment} from './attachments';
import {formatBytes} from './workspaceData';

export function ComposerAttachments({items, disabled, remove, retry}: {
  items: Attachment[]; disabled: boolean; remove: (id: string) => void; retry: (id: string) => void;
}) {
  if (!items.length) return null;
  return <div className="composer-attachments" aria-label="本次消息附件">
    {items.map(item => <div className={`composer-attachment ${item.status}`} key={item.id}>
      <FileText size={16} aria-hidden="true" />
      <div><strong title={item.file.name}>{item.file.name}</strong>
        <small role="status">{formatBytes(item.file.size)} · {item.status === 'ready' ? '已上传 · 发送后读取'
          : item.status === 'queued' ? '等待上传' : item.status === 'uploading' ? `上传中 ${item.progress}%` : '上传失败'}</small>
        {item.status === 'uploading' && <progress max={100} value={item.progress} aria-label={`${item.file.name} 上传进度`} />}
        {item.error && <small role="alert">{item.error}</small>}
      </div>
      {item.status === 'error' && <button type="button" className="icon-button" disabled={disabled}
        aria-label={`重试 ${item.file.name}`} onClick={() => retry(item.id)}><RotateCcw size={14} /></button>}
      <button type="button" className="icon-button" disabled={disabled} aria-label={`移除 ${item.file.name}`}
        title="从本次消息移除；已上传文件仍保留在工作区" onClick={() => remove(item.id)}><X size={14} /></button>
    </div>)}
  </div>;
}
