import {parseFrames} from './native';
import type {Message, NativeEvent} from './native';

export type ModelConfig = {type: string; credential_id: string; model: string; parameters: Record<string, unknown>};
export type ProductConfig = {
  user_id: string; agent_id: string; model_configured: boolean; model_config: ModelConfig | null;
  integrations?: {model_source: string; workspace_backend: string; sandbox_image: string | null; skills: string[]; mcp_servers: string[]; legacy_tools: boolean};
  orchestration?: {max_subagents: number; max_iters?: number; todo_tool: string; dynamic_roles: boolean};
};
export type SessionRef = {id: string; agent_id: string};
export type ExecutionPermission = 'default' | 'bypass';
export type NativeTask = {
  id: string;
  subject: string;
  description: string;
  state: 'pending' | 'in_progress' | 'completed';
  owner: string | null;
  blocks: string[];
  blocked_by: string[];
  metadata: Record<string, unknown>;
  created_at: string;
};
export type SessionView = {
  session: SessionRef & {config: {name: string; workspace_id: string; chat_model_config?: ModelConfig | null}; state: {permission_context?: {mode: string}; tasks_context?: {tasks?: NativeTask[]}}};
  status: string;
  team: null | {members: Array<{session_id: string | null; agent: {id: string; data: {name: string}}}>};
};
const base = import.meta.env.VITE_TRACEGLASS_API ?? '/api';

export async function request<T>(path: string, userId?: string, init: RequestInit = {}): Promise<T> {
  const response = await fetch(base + path, {...init, headers: {
    ...(init.body ? {'Content-Type': 'application/json'} : {}),
    ...(userId ? {'X-User-ID': userId} : {}), ...init.headers,
  }});
  if (!response.ok) throw new Error(`${response.status}: ${await response.text()}`);
  return response.status === 204 ? undefined as T : await response.json() as T;
}
export const query = (ref: SessionRef) => `agent_id=${encodeURIComponent(ref.agent_id)}`;
export const sessionPath = (ref: SessionRef, suffix: string) => `/sessions/${encodeURIComponent(ref.id)}/${suffix}?${query(ref)}`;

export const setSessionPermission = (user: string, ref: SessionRef, mode: ExecutionPermission) =>
  request<SessionView['session']>('/product' + sessionPath(ref, 'permission'), user, {
    method: 'PATCH', body: JSON.stringify({permission_mode: mode}),
  });

export async function readHistory(user: string, ref: SessionRef, signal: AbortSignal): Promise<Message[]> {
  let messages: Message[] = [];
  let before = '';
  const cursors = new Set<string>();
  while (!signal.aborted) {
    const page = await request<{messages: Message[]; has_more: boolean}>(sessionPath(ref,'messages') + `&limit=200${before ? `&before=${encodeURIComponent(before)}` : ''}`, user, {signal});
    messages = [...page.messages, ...messages];
    if (!page.has_more || !page.messages.length) break;
    before = page.messages[0].id;
    if (cursors.has(before)) throw new Error('历史消息游标重复');
    cursors.add(before);
  }
  return [...new Map(messages.map(m => [m.id, m])).values()];
}

export async function streamSession(user: string, ref: SessionRef, signal: AbortSignal, receive: (event: NativeEvent) => void, connected: () => void): Promise<void> {
  const response = await fetch(base + sessionPath(ref,'stream'), {headers: {'X-User-ID': user}, signal});
  if (!response.ok || !response.body) throw new Error(`事件流连接失败：${response.status}`);
  connected();
  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = '';
  try {
    while (!signal.aborted) {
      const {value,done} = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, {stream:true});
      const parsed = parseFrames(buffer);
      buffer = parsed.rest;
      for (const frame of parsed.frames) receive(JSON.parse(frame) as NativeEvent);
    }
  } finally { reader.releaseLock(); }
}
