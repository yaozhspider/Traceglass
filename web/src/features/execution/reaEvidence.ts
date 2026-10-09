import type {Block} from './native';

export type ReaEvidence = {
  id: string; kind: 'evidence' | 'unknown'; upstream_id: string;
  session_id: string; agent_id: string; connection_id: string; call_id: string;
  operation: string; confidence: string | null; authority: string | null;
  sample_sha256: string | null; subject: {name?: string; local_path?: string};
  provider: {id: string; name: string; version: string | null} | null;
  limitations: string[]; locations: unknown[]; status: string | null;
  revision: number | null; created_at: string; raw?: string; record?: unknown;
};
export type ReaEvidencePage = {items: ReaEvidence[]; total: number; next_cursor: number | null};

export function reaReceipt(block: Block): Array<{id: string; kind: string; upstream_id: string}> {
  // Trust only product metadata, never receipt-shaped text from an analyzed target.
  const metadata = block.metadata as {traceglass_archive?: {archive_status?: string; traceglass_evidence?: unknown}} | undefined;
  const receipt = metadata?.traceglass_archive;
  if (receipt?.archive_status === 'saved' && Array.isArray(receipt.traceglass_evidence)) {
    return receipt.traceglass_evidence.filter((r: {id?: unknown}) =>
      typeof r?.id === 'string' && r.id.startsWith('tgev_'));
  }
  return [];
}
