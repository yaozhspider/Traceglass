import type {Message} from './native';

export const BUDGET_CLOSEOUT_SOURCE = 'traceglass.iteration_closeout';

export function hasBudgetCloseout(reply: Message | undefined): boolean {
  return reply?.content.some(block => block.type === 'hint' && block.source === BUDGET_CLOSEOUT_SOURCE) ?? false;
}

export function canResumeReply(reply: Message | undefined): boolean {
  return reply?.role === 'assistant' && (
    ['interrupted', 'error', 'exceed_max_iters'].includes(reply.finished_reason ?? '') ||
    (reply.finished_reason === 'completed' && hasBudgetCloseout(reply))
  );
}

/** Parked approval/tool runs are pausable even when the leader is idle. */
export function canPauseExecution(status: string | undefined, memberStatuses: Array<string | undefined>): boolean {
  return [status, ...memberStatuses].some((value) =>
    ['preparing', 'running', 'awaiting_permission', 'awaiting_external_result'].includes(value ?? ''));
}
