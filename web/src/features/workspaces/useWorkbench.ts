import { useCallback, useEffect, useRef, useState } from 'react';
import { request, setSessionPermission } from '../execution/nativeApi';
import type { ExecutionPermission, ProductConfig, SessionView } from '../execution/nativeApi';
import { workspaceApi } from './workspaceApi';
import {attachmentContext} from './attachments';
import {useComposerAttachments} from './useComposerAttachments';
import type {
  WorkspaceDetail,
  WorkspaceRuntime,
  WorkspaceSample,
  WorkspaceSummary,
} from './workspaceApi';

type Route = {
  workspace: string | null;
  session: string | null;
  sample: string | null;
};
type SessionSnapshot = SessionView & { tasksSettledAt: number };
type PendingInput = { id: string; text: string };
function readPendingInputs(): Record<string, PendingInput> {
  try {
    const stored: unknown = JSON.parse(
      sessionStorage.getItem('traceglass-pending-inputs') ?? '{}',
    );
    if (!stored || typeof stored !== 'object' || Array.isArray(stored))
      return {};
    return Object.fromEntries(
      Object.entries(stored).filter(
        ([, value]) =>
          value &&
          typeof value.id === 'string' &&
          typeof value.text === 'string',
      ),
    );
  } catch {
    return {};
  }
}
function readRoute(): Route {
  const params = new URLSearchParams(location.search);
  return {
    workspace: params.get('workspace'),
    session: params.get('session'),
    sample: params.get('sample'),
  };
}

/** Page coordination only; AgentScope owns every execution state. */
export function useWorkbench() {
  const [config, setConfig] = useState<ProductConfig | null>(null);
  const [workspaces, setWorkspaces] = useState<WorkspaceSummary[]>([]);
  const [detail, setDetail] = useState<WorkspaceDetail | null>(null);
  const [runtime, setRuntime] = useState<WorkspaceRuntime | null>(null);
  const [pendingInputs, setPendingInputs] =
    useState<Record<string, PendingInput>>(readPendingInputs);
  const [sessions, setSessions] = useState<SessionSnapshot[]>([]);
  const [route, setRoute] = useState<Route>(readRoute);
  const routeRef = useRef(route);
  routeRef.current = route;
  const [prompt, setPrompt] = useState('');
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [workspaceLoading, setWorkspaceLoading] = useState(false);
  const [busy, setBusy] = useState(false);
  const [draftPermission, setDraftPermission] = useState<ExecutionPermission>('default');
  const [permissionSaving, setPermissionSaving] = useState(false);
  const [pausing, setPausing] = useState(false);
  const lastSessionRequest = useRef(0);
  const detailRevision = useRef(0);
  const attachments = useComposerAttachments(config?.user_id, route.workspace, (sample, workspaceId) => {
    if (routeRef.current.workspace !== workspaceId) return;
    ++detailRevision.current;
    setDetail(current => current ? {...current,
      samples: [...current.samples.filter(s => s.sample_id !== sample.sample_id), sample]} : current);
  });
  useEffect(() => {
    try {
      sessionStorage.setItem(
        'traceglass-pending-inputs',
        JSON.stringify(pendingInputs),
      );
    } catch {
      /* Chat submission remains available when browser storage is full. */
    }
  }, [pendingInputs]);

  const navigate = useCallback((next: Route, replace = false) => {
    if (!replace) attachments.queue.clear();
    routeRef.current = next;
    setRoute(next);
    setError(null);
    const url = new URL(location.href);
    for (const key of ['workspace', 'session', 'sample'] as const) {
      if (next[key]) url.searchParams.set(key, next[key]!);
      else url.searchParams.delete(key);
    }
    if (replace) history.replaceState(null, '', url);
    else history.pushState(null, '', url);
  }, [attachments.queue]);

  useEffect(() => {
    const pop = () => {
      attachments.queue.clear();
      const next = readRoute();
      routeRef.current = next;
      setRoute(next);
      setPrompt('');
      setError(null);
    };
    window.addEventListener('popstate', pop);
    return () => window.removeEventListener('popstate', pop);
  }, [attachments.queue]);

  const refreshSessions = useCallback(
    async (cfg: ProductConfig, signal?: AbortSignal) => {
      const started = performance.now();
      const data = await request<{ sessions: SessionView[] }>(
        `/sessions/?agent_id=${encodeURIComponent(cfg.agent_id)}`,
        cfg.user_id,
        { signal },
      );
      if (signal?.aborted || started < lastSessionRequest.current) return;
      lastSessionRequest.current = started;
      setSessions((previous) => {
        const settled = new Map(
          previous.map((item) => [item.session.id, item.tasksSettledAt]),
        );
        return data.sessions.map((item) => ({
          ...item,
          tasksSettledAt: [
            'idle',
            'awaiting_permission',
            'awaiting_external_result',
          ].includes(item.status)
            ? started
            : (settled.get(item.session.id) ?? 0),
        }));
      });
    },
    [],
  );

  const refreshWorkspaces = useCallback(
    async (cfg: ProductConfig, signal?: AbortSignal) => {
      const data = await workspaceApi.list(cfg.user_id, signal);
      if (!signal?.aborted) setWorkspaces(data.workspaces);
    },
    [],
  );

  const refreshDetail = useCallback(
    async (cfg: ProductConfig, id: string, signal?: AbortSignal) => {
      const revision = ++detailRevision.current;
      const data = await workspaceApi.get(cfg.user_id, id, signal);
      if (
        !signal?.aborted &&
        routeRef.current.workspace === id &&
        revision === detailRevision.current
      )
        setDetail(data);
    },
    [],
  );

  useEffect(() => {
    const controller = new AbortController();
    const { signal } = controller;
    void (async () => {
      try {
        const cfg = await request<ProductConfig>('/product/config', undefined, {
          signal,
        });
        if (signal.aborted) return;
        setConfig(cfg);
        await Promise.all([
          refreshWorkspaces(cfg, signal),
          refreshSessions(cfg, signal),
        ]);
      } catch (e) {
        if (!signal.aborted) setError(String(e));
      } finally {
        if (!signal.aborted) setLoading(false);
      }
    })();
    return () => controller.abort();
  }, [refreshWorkspaces, refreshSessions]);

  useEffect(() => {
    const update = () => void request<ProductConfig>('/product/config').then(setConfig).catch(e => setError(String(e)));
    window.addEventListener('traceglass-model-changed', update);
    return () => window.removeEventListener('traceglass-model-changed', update);
  }, []);

  useEffect(() => {
    if (!config) return;
    const controller = new AbortController();
    let refreshing = false;
    const timer = window.setInterval(async () => {
      if (refreshing) return;
      refreshing = true;
      try {
        await refreshSessions(config, controller.signal);
      } catch (e) {
        if (!controller.signal.aborted) setError(String(e));
      } finally {
        refreshing = false;
      }
    }, 2500);
    return () => {
      controller.abort();
      clearInterval(timer);
    };
  }, [config, refreshSessions]);

  const snapshot = sessions.find((item) => item.session.id === route.session);
  const active = snapshot
    ? {
        ...snapshot,
        status: runtime?.sessions[snapshot.session.id] ?? snapshot.status,
      }
    : undefined;
  // Existing session-only bookmarks are resolved without moving their files.
  useEffect(() => {
    if (active && route.workspace !== active.session.config.workspace_id) {
      navigate(
        {
          ...route,
          workspace: active.session.config.workspace_id,
          sample: null,
        },
        true,
      );
    }
  }, [
    active?.session.id,
    active?.session.config.workspace_id,
    route.workspace,
    navigate,
  ]);

  useEffect(() => {
    setDetail(null);
    if (!config || !route.workspace) return;
    const id = route.workspace;
    const controller = new AbortController();
    setWorkspaceLoading(true);
    void refreshDetail(config, id, controller.signal)
      .catch((e) => !controller.signal.aborted && setError(String(e)))
      .finally(() => !controller.signal.aborted && setWorkspaceLoading(false));
    let refreshing = false;
    const timer = window.setInterval(async () => {
      if (refreshing) return;
      refreshing = true;
      try {
        await refreshDetail(config, id, controller.signal);
      } catch (e) {
        if (!controller.signal.aborted) setError(String(e));
      } finally {
        refreshing = false;
      }
    }, 5000);
    return () => {
      controller.abort();
      clearInterval(timer);
    };
  }, [config, route.workspace, refreshDetail]);

  useEffect(() => {
    setRuntime(null);
    if (!config || !route.workspace) return;
    const id = route.workspace;
    const controller = new AbortController();
    let refreshing = false;
    const update = async () => {
      if (refreshing) return;
      refreshing = true;
      try {
        const value = await workspaceApi.runtime(
          config.user_id,
          id,
          controller.signal,
        );
        if (!controller.signal.aborted && routeRef.current.workspace === id)
          setRuntime(value);
      } catch (e) {
        if (!controller.signal.aborted) setError(String(e));
      } finally {
        refreshing = false;
      }
    };
    void update();
    const timer = window.setInterval(update, 2500);
    return () => {
      controller.abort();
      clearInterval(timer);
    };
  }, [config, route.workspace]);

  const inputRecorded = (sessionId: string) =>
    setPendingInputs((current) => {
      if (!current[sessionId]) return current;
      const next = { ...current };
      delete next[sessionId];
      return next;
    });

  const boundSample = route.session
    ? detail?.tasks.find((task) => task.session_id === route.session)?.sample_id
    : null;
  const selectedSample = detail?.samples.find(sample => sample.sample_id === (route.session ? boundSample : route.sample)) ?? null;
  const selectedSamples = attachments.queue.ready();
  const selectedSampleIds = selectedSamples.map(sample => sample.sample_id);
  // Preserve old deep links, while all new file selections use the message queue.
  const importedRouteSample = useRef<string | null>(null);
  useEffect(() => {
    const key = `${route.workspace}:${route.sample}`;
    if (!route.session && route.sample && selectedSample && importedRouteSample.current !== key) {
      importedRouteSample.current = key;
      attachments.queue.addExisting([selectedSample]);
    }
    if (!route.sample) importedRouteSample.current = null;
  }, [route.workspace, route.sample, route.session, selectedSample, attachments.queue]);
  const openWorkspace = (id: string | null) => {
    setDraftPermission('default');
    setPrompt('');
    navigate({ workspace: id, session: null, sample: null });
  };
  const openSession = (id: string) => {
    setPrompt('');
    navigate({ ...route, session: id, sample: null });
  };
  const newAnalysis = () => {
    setDraftPermission('default');
    setPrompt('');
    navigate({
      ...route,
      session: null,
      sample: null,
    });
  };
  const selectSamples = (ids: string[]) => {
    for (const sample of attachments.queue.ready()) {
      if (!ids.includes(sample.sample_id)) attachments.queue.removeSample(sample.sample_id);
    }
    attachments.queue.addExisting(detail?.samples.filter(sample => ids.includes(sample.sample_id)) ?? []);
  };
  const sampleUploaded = (sample: WorkspaceSample, workspaceId: string) => {
    if (routeRef.current.workspace !== workspaceId) return;
    ++detailRevision.current; // Discard a listing requested before this upload.
    setDetail((current) =>
      current
        ? {
            ...current,
            samples: [
              ...current.samples.filter(
                (s) => s.sample_id !== sample.sample_id,
              ),
              sample,
            ],
          }
        : current,
    );
    attachments.queue.addExisting([sample]);
    if (config)
      void refreshWorkspaces(config).catch((e) => setError(String(e)));
  };
  const [deletingSample, setDeletingSample] = useState<string | null>(null);
  const deleteSample = async (sample: WorkspaceSample) => {
    if (!config || !route.workspace || deletingSample) return;
    const workspaceId = route.workspace;
    const queue = attachments.queue;
    setDeletingSample(sample.sample_id);
    try {
      await workspaceApi.deleteSample(config.user_id, workspaceId, sample.sample_id);
      queue.removeSample(sample.sample_id);
      if (routeRef.current.workspace === workspaceId) {
        ++detailRevision.current;
        setDetail(current => current ? {...current, samples: current.samples.filter(item => item.sample_id !== sample.sample_id)} : current);
        if (routeRef.current.sample === sample.sample_id) navigate({...routeRef.current, sample: null}, true);
      }
      await refreshWorkspaces(config);
    } catch (failure) {
      if (routeRef.current.workspace === workspaceId) setError(String(failure));
    } finally {setDeletingSample(null);}
  };

  const createWorkspace = async (name: string, description = '') => {
    if (!config) throw new Error('服务尚未连接，请稍后重试。');
    const workspace = await workspaceApi.create(
      config.user_id,
      name,
      description,
    );
    setWorkspaces((items) => [
      workspace,
      ...items.filter((item) => item.workspace_id !== workspace.workspace_id),
    ]);
    openWorkspace(workspace.workspace_id);
  };

  const refresh = async () => {
    setError(null);
    try {
      const cfg = config ?? (await request<ProductConfig>('/product/config'));
      if (!config) setConfig(cfg);
      await Promise.all([
        refreshWorkspaces(cfg),
        refreshSessions(cfg),
        ...(route.workspace ? [refreshDetail(cfg, route.workspace)] : []),
      ]);
    } catch (e) {
      setError(String(e));
    } finally {
      setLoading(false);
    }
  };

  const submit = async () => {
    if (
      !config ||
      !route.workspace ||
      !prompt.trim() ||
      busy ||
      !!deletingSample ||
      attachments.queue.blocked() ||
      permissionSaving ||
      pausing ||
      ['preparing', 'running', 'awaiting_permission'].includes(
        active?.status ?? '',
      )
    )
      return;
    if (!route.session && route.sample && !selectedSample) {
      setError('所选样本尚未加载或已不存在，请刷新后重新选择。');
      return;
    }
    const current = { ...route };
    const text = prompt.trim();
    const attached = attachments.queue.ready();
    const attachmentIds = attachments.items.map(item => item.id);
    const inputId = crypto.randomUUID();
    let submittedSession: string | null = null;
    let accepted = false;
    setBusy(true);
    setError(null);
    try {
      let id = current.session;
      if (!id) {
        const created = await workspaceApi.createSession(
          config.user_id,
          current.workspace!,
          {
            name: text.slice(0, 60),
            sample_id: attached[0]?.sample_id,
            chat_model_config: config.model_config,
            permission_mode: draftPermission,
          },
        );
        id = created.session_id;
        if (
          routeRef.current.workspace === current.workspace &&
          routeRef.current.session === current.session &&
          routeRef.current.sample === current.sample
        ) {
          navigate({ ...current, session: id, sample: null }, true);
          await refreshDetail(config, current.workspace!);
        }
      } else if (
        !active?.session.config.chat_model_config &&
        config.model_config
      ) {
        await request(
          `/sessions/${encodeURIComponent(id)}?agent_id=${encodeURIComponent(config.agent_id)}`,
          config.user_id,
          {
            method: 'PATCH',
            body: JSON.stringify({ chat_model_config: config.model_config }),
          },
        );
      }
      const content = [
        ...(attached.length ? [{type: 'text', text: attachmentContext(attached)}] : []),
        { type: 'text', text },
      ];
      submittedSession = id;
      setPendingInputs((items) => ({ ...items, [id!]: { id: inputId, text } }));
      await request('/chat/', config.user_id, {
        method: 'POST',
        body: JSON.stringify({
          agent_id: config.agent_id,
          session_id: id,
          input: { id: inputId, name: 'user', role: 'user', content },
        }),
      });
      accepted = true;
      attachmentIds.forEach(id => attachments.queue.remove(id));
      if (routeRef.current.workspace === current.workspace) {
        setRuntime((value) => ({
          ...value,
          sessions: { ...value?.sessions, [id!]: 'preparing' },
          integrations: value?.integrations ?? [],
        }));
      }
      if (routeRef.current.session === id)
        setPrompt((currentPrompt) =>
          currentPrompt.trim() === text ? '' : currentPrompt,
        );
      await Promise.all([refreshSessions(config), refreshWorkspaces(config)]);
    } catch (e) {
      if (submittedSession && !accepted) inputRecorded(submittedSession);
      setError(String(e));
    } finally {
      setBusy(false);
    }
  };

  const changePermission = async (mode: ExecutionPermission) => {
    if (!route.session) {
      setDraftPermission(mode);
      return;
    }
    if (!config || !active || permissionSaving || busy) return;
    setPermissionSaving(true);
    setError(null);
    try {
      const updated = await setSessionPermission(config.user_id, active.session, mode);
      // Discard polling snapshots started before the mutation completed.
      lastSessionRequest.current = performance.now();
      setSessions((items) => items.map((item) => item.session.id === updated.id
        ? {...item, session: updated} : item));
    } catch (e) {
      setError(String(e));
    } finally {
      setPermissionSaving(false);
    }
  };

  const pauseAll = async () => {
    if (!config || !active || pausing) return;
    setPausing(true);
    setError(null);
    try {
      await request(`/product/sessions/${encodeURIComponent(active.session.id)}/interrupt?` +
        new URLSearchParams({agent_id: active.session.agent_id, scope: 'team'}), config.user_id, {method: 'POST'});
      await refreshSessions(config);
    } catch (e) {
      setError(String(e));
    } finally { setPausing(false); }
  };

  return {
    config,
    workspaces,
    detail,
    runtime,
    active,
    route,
    selectedSample,
    selectedSamples,
    selectedSampleIds,
    attachments,
    prompt,
    setPrompt,
    pendingInput: route.session ? pendingInputs[route.session] : undefined,
    inputRecorded,
    error,
    loading,
    workspaceLoading,
    busy,
    permissionSaving,
    pausing,
    pauseAll,
    permissionMode: route.session ? active?.session.state.permission_context?.mode ?? null : draftPermission,
    changePermission,
    openWorkspace,
    openSession,
    newAnalysis,
    selectSamples,
    sampleUploaded,
    deleteSample,
    deletingSample,
    createWorkspace,
    refresh,
    submit,
  };
}
