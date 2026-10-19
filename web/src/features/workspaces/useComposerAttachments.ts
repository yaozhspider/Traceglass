import {useEffect, useMemo, useRef, useSyncExternalStore} from 'react';
import {AttachmentQueue} from './attachments';
import {workspaceApi} from './workspaceApi';
import type {WorkspaceSample} from './workspaceApi';

export function useComposerAttachments(user: string | undefined, workspace: string | null,
  onUploaded: (sample: WorkspaceSample, workspaceId: string) => void) {
  const uploaded = useRef(onUploaded);
  uploaded.current = onUploaded;
  const queue = useMemo(() => new AttachmentQueue(async (file, progress, signal) => {
    if (!user || !workspace) throw new Error('请先打开工作区。');
    const sample = await workspaceApi.upload(user, workspace, file, progress, signal);
    if (!signal.aborted) uploaded.current(sample, workspace);
    return sample;
  }), [user, workspace]);
  useEffect(() => () => queue.clear(), [queue]);
  const items = useSyncExternalStore(queue.subscribe, queue.snapshot);
  return {queue, items};
}
