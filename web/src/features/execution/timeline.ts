import type { Block, Message } from './native';
import {reaReceipt} from './reaEvidence.ts';

export type TimelineBlock = { block: Block; result?: Block; index: number };
/** Preserve message order; only fold a result when its actual call ID is present. */
export function timelineBlocks(blocks: Block[]): TimelineBlock[] {
  const calls = new Set(
    blocks.filter((b) => b.type === 'tool_call' && b.id).map((b) => b.id),
  );
  const results = new Map(
    blocks
      .filter((b) => b.type === 'tool_result' && b.id)
      .map((b) => [b.id, b]),
  );
  return blocks.flatMap((block, index) =>
    block.type === 'tool_result' && block.id && calls.has(block.id)
      ? []
      : [
          {
            block,
            index,
            result:
              block.type === 'tool_call' && block.id
                ? results.get(block.id)
                : undefined,
          },
        ],
  );
}
export function toolArguments(block: Block): Record<string, unknown> {
  try {
    const value =
      typeof block.input === 'string' ? JSON.parse(block.input) : block.input;
    return value && typeof value === 'object' && !Array.isArray(value)
      ? value
      : {};
  } catch {
    return {};
  }
}
export function toolStatus(
  call: Block,
  result?: Block,
  running?: boolean,
): string {
  const state = result?.state ?? call.state;
  if (state === 'error') return '失败';
  if (state === 'denied' || state === 'rejected') return '已拒绝';
  if (state === 'success') return '完成';
  if (state === 'asking') return '等待授权';
  if (state === 'finished' && !result) return '结果未记录';
  return running ? '调用中' : '未完成';
}
export function delegationAnchor(
  messages: Message[],
): { messageId: string; blockIndex: number } | null {
  for (const message of messages) {
    const blockIndex = message.content.findIndex(
      (b) =>
        b.type === 'tool_call' &&
        ['AgentCreate', 'AgentInvite'].includes(b.name ?? ''),
    );
    if (blockIndex >= 0) return { messageId: message.id, blockIndex };
  }
  return null;
}
export function evidenceTools(
  messages: Message[],
): { messageId: string; call: Block; result: Block }[] {
  return messages.flatMap((message) =>
    timelineBlocks(message.content).flatMap((row) =>
      row.result?.state === 'success' &&
      (/(?:^|__)record_evidence$/.test(row.block.name ?? '') ||
        ((row.block.name ?? '').startsWith('mcp__rea__') && reaReceipt(row.result).length > 0) ||
        (/(?:^|__)save_analysis_artifact$/.test(row.block.name ?? '') &&
          hasEvidenceRecord(row.result)))
        ? [{ messageId: message.id, call: row.block, result: row.result }]
        : [],
    ),
  );
}
export function statusLabel(status?: string): string {
  return (
    (
      {
        preparing: '准备环境',
        running: '分析中',
        idle: '本轮空闲',
        awaiting_permission: '等待授权',
        awaiting_external_result: '等待外部结果',
      } as Record<string, string>
    )[status ?? ''] ?? '状态待同步'
  );
}

function hasEvidenceRecord(result: Block): boolean {
  const output = Array.isArray(result.output)
    ? result.output
    : [{ type: 'text', text: result.output }];
  return output.some((item) => {
    if (!item || item.type !== 'text' || typeof item.text !== 'string')
      return false;
    try {
      const data = JSON.parse(item.text);
      return (
        typeof data?.id === 'string' &&
        data.id.startsWith('E-') &&
        typeof data.location === 'string'
      );
    } catch {
      return false;
    }
  });
}
