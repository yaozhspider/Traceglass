import {authFetch, csrfHeaders, authenticationExpired} from '../account/transport';
import type {ExecutionPermission, ModelConfig} from '../execution/nativeApi';
import {fileQuery} from './workspaceData';
import type {ReaEvidence, ReaEvidencePage} from '../execution/reaEvidence';
export {sampleContext} from './workspaceData';

export type WorkspaceSummary = {
  workspace_id: string; name: string; description: string; created_at: string;
  updated_at: string; sample_count: number; task_count: number; is_legacy: boolean;
};
export type WorkspaceSample = {
  sample_id: string; name: string; sample_path: string; sandbox_path: string;
  reverse_sample_path?: string; sha256: string; size: number; created_at: string;
};
export type WorkspaceTask = {
  session_id: string; agent_id: string; name: string; created_at: string;
  updated_at?: string; sample_id?: string | null;
};
export type WorkspaceDetail = {workspace: WorkspaceSummary; samples: WorkspaceSample[]; tasks: WorkspaceTask[]; samples_truncated?: boolean};
export type WorkspaceEntry = {name: string; path: string; is_dir: boolean; size_bytes: number};
export type WorkspacePreview = {name: string; path: string; content: string; encoding: string; hex?: string; truncated: boolean; size: number};
export type WorkspaceDirectory = {path: string; entries: WorkspaceEntry[]; truncated: boolean};
export type WorkspaceRuntime = {
  sessions: Record<string, 'preparing' | 'running'>;
  members?: Record<string, string>;
  integrations: Array<{name: string; optional: boolean; status: 'not_checked' | 'connected' | 'unavailable'; message: string; checked_at: string | null}>;
};
export const MAX_UPLOAD_BYTES = 100 * 1024 * 1024;

const base = import.meta.env.VITE_TRACEGLASS_API ?? '/api';
const root = '/product/workspaces';
const resource = (id: string) => `${root}/${encodeURIComponent(id)}`;

function errorMessage(body: unknown, status: number): string {
  if (body && typeof body === 'object' && 'detail' in body) {
    const detail = (body as {detail: unknown}).detail;
    return typeof detail === 'string' ? detail : JSON.stringify(detail);
  }
  return typeof body === 'string' && body ? body : `请求失败（${status}）`;
}

async function response(user: string, path: string, init: RequestInit = {}): Promise<Response> {
  const result = await authFetch(base + path, {...init, headers: {
    'X-User-ID': user, ...(init.body ? {'Content-Type': 'application/json'} : {}), ...init.headers,
  }});
  if (!result.ok) {
    const text = await result.text();
    let body: unknown = text;
    try {body = JSON.parse(text);} catch { /* The server may return plain text. */ }
    throw new Error(errorMessage(body, result.status));
  }
  return result;
}

async function json<T>(user: string, path: string, init?: RequestInit): Promise<T> {
  return (await response(user, path, init)).json() as Promise<T>;
}

function upload(user: string, id: string, file: File, onProgress?: (percent: number) => void, signal?: AbortSignal): Promise<WorkspaceSample> {
  if (file.size > MAX_UPLOAD_BYTES) return Promise.reject(new Error('文件超过 100 MiB 上传上限。'));
  if (signal?.aborted) return Promise.reject(new DOMException('上传已取消', 'AbortError'));
  return new Promise((resolve, reject) => {
    const xhr = new XMLHttpRequest();
    const cleanup = () => signal?.removeEventListener('abort', abort);
    const abort = () => xhr.abort();
    xhr.open('POST', base + resource(id) + '/samples');
    xhr.setRequestHeader('X-User-ID', user);
    for (const [key,value] of Object.entries(csrfHeaders())) xhr.setRequestHeader(key,value);
    xhr.responseType = 'json';
    xhr.upload.onprogress = event => {
      if (event.lengthComputable) onProgress?.(Math.min(100, Math.round(event.loaded / event.total * 100)));
    };
    xhr.onload = () => {
      cleanup();
      if (xhr.status === 401) authenticationExpired();
      if (xhr.status >= 200 && xhr.status < 300) resolve(xhr.response as WorkspaceSample);
      else reject(new Error(errorMessage(xhr.response, xhr.status)));
    };
    xhr.onerror = () => {cleanup(); reject(new Error('上传连接中断，请重试。'));};
    xhr.onabort = () => {cleanup(); reject(new DOMException('上传已取消', 'AbortError'));};
    signal?.addEventListener('abort', abort, {once: true});
    const body = new FormData();
    body.append('file', file);
    xhr.send(body);
  });
}

export const workspaceApi = {
  reaEvidence: (user: string, id: string, filters: {kind?: string; sample_sha256?: string; cursor?: number} = {}, signal?: AbortSignal) => {
    const query = new URLSearchParams();
    for (const [key, value] of Object.entries(filters)) if (value !== undefined && value !== '') query.set(key, String(value));
    return json<ReaEvidencePage>(user, `${resource(id)}/rea-evidence?${query}`, {signal});
  },
  reaRecord: (user: string, id: string, recordId: string, signal?: AbortSignal) =>
    json<ReaEvidence>(user, `${resource(id)}/rea-evidence/${encodeURIComponent(recordId)}`, {signal}),
  list: (user: string, signal?: AbortSignal) => json<{workspaces: WorkspaceSummary[]}>(user, root, {signal}),
  get: (user: string, id: string, signal?: AbortSignal) => json<WorkspaceDetail>(user, resource(id), {signal}),
  runtime: (user: string, id: string, signal?: AbortSignal) => json<WorkspaceRuntime>(user, `/product/runtime?${new URLSearchParams({workspace_id: id})}`, {signal}),
  create: (user: string, name: string, description = '') => json<WorkspaceSummary>(user, root, {method: 'POST', body: JSON.stringify({name, description})}),
  createSession: (user: string, id: string, input: {name: string; sample_id?: string; chat_model_config?: ModelConfig | null; permission_mode?: ExecutionPermission}) =>
    json<{session_id: string; agent_id: string; workspace_id: string}>(user, resource(id) + '/sessions', {method: 'POST', body: JSON.stringify(input)}),
  upload,
  deleteSample: (user: string, id: string, sampleId: string) =>
    response(user, `${resource(id)}/samples/${encodeURIComponent(sampleId)}`, {method: 'DELETE'}).then(() => undefined),
  files: (user: string, id: string, path = '', signal?: AbortSignal) => json<WorkspaceDirectory>(user, `${resource(id)}/files?${fileQuery(path)}`, {signal}),
  preview: (user: string, id: string, path: string, signal?: AbortSignal) => json<WorkspacePreview>(user, `${resource(id)}/preview?${fileQuery(path)}`, {signal}),
  download: async (user: string, id: string, path: string, name: string) => {
    const result = await response(user, `${resource(id)}/download?${fileQuery(path)}`);
    const url = URL.createObjectURL(await result.blob());
    const link = document.createElement('a');
    link.href = url; link.download = name; document.body.appendChild(link); link.click(); link.remove();
    window.setTimeout(() => URL.revokeObjectURL(url), 1000);
  },
};
