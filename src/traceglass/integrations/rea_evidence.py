"""Archive completed native tool responses after AgentScope's permission gate."""
import asyncio
import json
import logging

from agentscope.message import TextBlock
from agentscope.middleware import MiddlewareBase
from agentscope.tool import ToolResponse


class ReaEvidenceMiddleware(MiddlewareBase):
    def __init__(self, store, user_id, workspace_id, session_id, agent_id, connection_id):
        self.store = store
        self.scope = dict(user_id=user_id, workspace_id=workspace_id, session_id=session_id,
                          agent_id=agent_id, connection_id=connection_id)

    async def on_acting(self, agent, input_kwargs, next_handler):
        call = input_kwargs['tool_call']
        async for item in next_handler(**input_kwargs):
            if (call.name.startswith('mcp__rea__') and isinstance(item, ToolResponse) and item.state == 'success'):
                try:
                    receipts = []
                    for block in item.content:
                        if isinstance(block, TextBlock):
                            receipts.extend(await asyncio.to_thread(self.store.capture, **self.scope,
                                call_id=call.id, tool_name=call.name, arguments=json.loads(call.input or '{}'), raw=block.text))
                    if receipts:
                        self._receipt(item, {'traceglass_evidence': receipts,
                            'archive_status': 'saved', 'note': 'Durable copies, not verified conclusions. Use ReadReaEvidence for full records.'})
                except Exception:
                    logging.getLogger(__name__).warning('REA evidence archive failed for session %s', self.scope['session_id'])
                    self._receipt(item, {'archive_status': 'archive_failed',
                        'note': 'Original result is preserved in this message. Save it before closing the REA session.'})
            yield item

    @staticmethod
    def _receipt(response, receipt):
        # Native ToolResultEndEvent persists metadata; modifying final content
        # alone only reaches model context, not the streamed/stored message.
        response.metadata = {**(response.metadata or {}), 'traceglass_archive': receipt}
        response.content.append(TextBlock(text=json.dumps(receipt)))
