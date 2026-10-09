"""Read-only workspace-bound access to evidence from all team members."""
import asyncio

from agentscope.tool import FunctionTool, ToolChunk

from traceglass.integrations.rea_workflows import response


def evidence_tools(store, user_id, workspace_id):
    async def query(sample_sha256: str | None = None, kind: str | None = None,
                    cursor: int | None = None, limit: int = 30) -> ToolChunk:
        """List saved REA evidence or unknown revisions from all agents in this workspace. Filter kind by evidence/unknown and sample_sha256 when comparing a sample. Returns summaries and a pagination cursor, not native REA handles. Unknown entries are revision history, not a global current-state judgment."""
        try:
            if kind not in (None, 'evidence', 'unknown'):
                raise ValueError('Invalid kind')
            result = await asyncio.to_thread(store.list, user_id, workspace_id,
                sample_sha256=sample_sha256, kind=kind, cursor=cursor, limit=limit)
            return response(result)
        except ValueError:
            return response({'error': 'Invalid archive filter or pagination.'}, error=True)

    async def read(record_id: str, offset: int = 0, limit: int = 16000) -> ToolChunk:
        """Read a durable Traceglass evidence ID returned by QueryReaEvidence or a tool receipt. Returns an explicitly paged original JSON result (character offsets). Copies survive restarts but upstream IDs are only usable within their original live REA connection. Reopen the sample to perform new analysis."""
        if offset < 0 or not 1 <= limit <= 64000:
            return response({'error': 'Invalid page: offset >= 0, limit 1..64000.'}, error=True)
        record = await asyncio.to_thread(store.get, user_id, workspace_id, record_id)
        if record is None:
            return response({'error': 'Evidence not found in this workspace.'}, error=True)
        raw = record.pop('raw')
        record.pop('record')
        end = offset + limit
        return response({**record, 'raw_page': raw[offset:end], 'offset': offset,
                         'total_chars': len(raw), 'next_offset': end if end < len(raw) else None})

    return [FunctionTool(query, name='QueryReaEvidence', is_read_only=True),
            FunctionTool(read, name='ReadReaEvidence', is_read_only=True)]
