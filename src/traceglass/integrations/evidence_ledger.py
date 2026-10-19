"""Archive final analysis responses after permission checking; never re-execute."""
import asyncio
import base64
import hashlib
import json
import logging
from pathlib import Path
import re

from agentscope.message import Msg, TextBlock, ToolResultBlock
from agentscope.middleware import MiddlewareBase
from agentscope.tool import ToolResponse

from .rea_evidence import ReaEvidenceMiddleware
from traceglass.evidence.store import observations

logger = logging.getLogger(__name__)
EXCLUDED = {'QueryReaEvidence', 'ReadReaEvidence', 'QueryAnalysisEvidence', 'ReadAnalysisEvidence',
            'AnalysisReview', 'SearchAnalysisTools', 'SearchReaTools', 'reset_tools',
            'TodoWrite', 'TaskCreate', 'TaskUpdate', 'TaskGet', 'TaskList',
            'TeamCreate', 'TeamDelete', 'TeamSay', 'AgentCreate', 'AgentInvite'}


class EvidenceLedgerMiddleware(MiddlewareBase):
    def __init__(self, store, user_id, workspace_id, session_id, agent_id, connection_id=None, *, workspace_root=None,
                 windows_service=None):
        self.store = store
        self.scope = dict(user_id=user_id, workspace_id=workspace_id, session_id=session_id, agent_id=agent_id)
        self.connection_id = connection_id
        self.workspace_root = Path(workspace_root).resolve() if workspace_root is not None else None
        self.windows_service = windows_service

    def _scope(self):
        connection = self.connection_id() if callable(self.connection_id) else self.connection_id
        return {**self.scope, 'connection_id': connection or 'native'}

    @staticmethod
    def _compact_receipt(receipt):
        references = [{key: item[key] for key in ('id', 'kind', 'call_state', 'truncated') if key in item}
                      for item in receipt['traceglass_evidence'][:20]]
        return TextBlock(text=json.dumps(dict(archive_status=receipt['archive_status'], traceglass_evidence=references,
            note='Large original output omitted from model context. Use ReadAnalysisEvidence(record_id, offset, limit) '
                 'to inspect scoped original pages before drawing conclusions. Partial archives are unverified.')))

    async def on_model_call(self, agent, input_kwargs, next_handler):
        # Keep native streamed/persisted originals. Only the model-facing copy
        # replaces large durably archived results with scoped read receipts.
        messages = []
        for message in input_kwargs.get('messages', []):
            if not isinstance(message, Msg):
                messages.append(message)
                continue
            content = []
            changed = False
            for block in message.content:
                receipt = block.metadata.get('traceglass_archive', {}) if isinstance(block, ToolResultBlock) else {}
                if (receipt.get('archive_status') in {'saved', 'partial'}
                        and receipt.get('traceglass_evidence')
                        and len(json.dumps(block.output if isinstance(block.output, str) else
                            [b.model_dump(mode='json') for b in block.output], ensure_ascii=False)) > 16_000):
                    content.append(block.model_copy(update={'output': [self._compact_receipt(receipt)]}))
                    changed = True
                else:
                    content.append(block)
            messages.append(message.model_copy(update={'content': content}) if changed else message)
        return await next_handler(**{**input_kwargs, 'messages': messages})

    @staticmethod
    def _bindings(agent):
        if agent is None:
            return {}
        return agent.state.middle_context.setdefault('traceglass.sample_bindings.v1', {})

    def _file_association(self, name, arguments, agent):
        digests = set()
        bindings = self._bindings(agent)
        for field in ('path', 'file_path', 'sample_path', 'input_path', 'input_file', 'filename'):
            raw = arguments.get(field)
            if not isinstance(raw, str) or not raw:
                continue
            path = Path(raw)
            # Prepared immutable IDA copies are registered by the native input
            # tool, never by arbitrary model arguments or output hash strings.
            trusted_copy = bindings.get('files', {}).get(raw)
            if self.workspace_root is None and not trusted_copy:
                continue
            if trusted_copy:
                candidates = [path]
            elif path.is_absolute():
                if path.is_relative_to('/workspace'):
                    candidates = [self.workspace_root / path.relative_to('/workspace')]
                else:
                    candidates = [path]
            elif field == 'sample_path' and name.startswith('mcp__reverse__'):
                candidates = [self.workspace_root / 'samples' / path]
            else:
                candidates = [self.workspace_root / path]
            for candidate in candidates:
                candidate = candidate.resolve()
                if not trusted_copy and not candidate.is_relative_to(self.workspace_root):
                    continue
                try:
                    if not candidate.is_file() or candidate.stat().st_size > 512 * 1024 * 1024:
                        continue
                    with candidate.open('rb') as stream:
                        digest = hashlib.file_digest(stream, 'sha256').hexdigest()
                    if trusted_copy and digest != trusted_copy:
                        continue
                    digests.add(digest)
                except OSError:
                    continue
        if not digests and name.startswith('mcp__idapro__'):
            handle = arguments.get('session_id')
            digest = bindings.get('ida_sessions', {}).get(str(handle)) if handle is not None else None
            if digest:
                digests.add(digest)
        if not digests and name.startswith('Windows') and arguments.get('job_id'):
            job_id = arguments['job_id']
            digest = bindings.get('windows_jobs', {}).get(job_id)
            if self.windows_service is not None:
                try:
                    job = self.windows_service._load(self.scope['user_id'], self.scope['workspace_id'], job_id)
                    digest = job['sample_sha256']
                except (LookupError, PermissionError):
                    digest = None
            if digest:
                digests.add(digest)
        return next(iter(digests)) if len(digests) == 1 else None

    def _register_association(self, name, texts, arguments, sha, agent):
        if not sha or agent is None:
            return
        bindings = self._bindings(agent)
        if name == 'mcp__idapro__session_close':
            bindings.get('ida_sessions', {}).pop(str(arguments.get('session_id')), None)
            return
        for text in texts:
            try:
                payload = json.loads(text)
            except (ValueError, TypeError):
                continue
            if not isinstance(payload, dict):
                continue
            if name == 'PrepareAnalysisInput':
                files = bindings.setdefault('files', {})
                for field in ('ida_file_path',):
                    if payload.get(field):
                        files[payload[field]] = sha
            if name == 'WindowsRunTool' and payload.get('job_id') and payload.get('sample_sha256') == sha:
                bindings.setdefault('windows_jobs', {})[payload['job_id']] = sha
            if name == 'mcp__idapro__session_create':
                result = payload.get('result', payload)
                if not isinstance(result, dict):
                    continue
                handle = result.get('session_id', result.get('id'))
                # Native IDA responds with id/file_path; never adopt its global
                # active session or a handle returned for a different file.
                if (handle is not None and result.get('file_path') == arguments.get('file_path')
                        and result.get('file_path') and result.get('status') != 'error'):
                    bindings.setdefault('ida_sessions', {})[str(handle)] = sha

    async def on_acting(self, agent, input_kwargs, next_handler):
        call = input_kwargs['tool_call']
        seen = False
        try:
            args = json.loads(call.input or '{}')
            if not isinstance(args, dict):
                args = {}
        except (ValueError, TypeError):
            args = {}
        before = await asyncio.to_thread(self._file_association, call.name, args, agent)
        try:
            async for item in self._responses(agent, input_kwargs, next_handler, args, before):
                if isinstance(item, ToolResponse):
                    seen = True
                yield item
        except (Exception, asyncio.CancelledError) as exc:
            if not seen and call.name not in EXCLUDED:
                state = 'cancelled' if isinstance(exc, asyncio.CancelledError) else 'timeout' if isinstance(exc, TimeoutError) else 'error'
                try:
                    await asyncio.shield(asyncio.to_thread(self.store.capture_call, **self._scope(),
                        call_id=call.id, tool_name=call.name, arguments=args,
                        raw=json.dumps({'error_type': type(exc).__name__}), state=state, sample_sha256=before))
                except Exception:
                    logger.warning('Evidence terminal-state archive failed for session %s', self.scope['session_id'])
            raise

    async def _responses(self, agent, input_kwargs, next_handler, args, before):
        call = input_kwargs['tool_call']
        seen = False
        async for item in next_handler(**input_kwargs):
            if isinstance(item, ToolResponse) and call.name not in EXCLUDED and not seen:
                seen = True
                try:
                    scope = self._scope()
                    texts = [b.text for b in item.content if isinstance(b, TextBlock)]
                    # Preserve all observable block types; textual calls remain directly readable.
                    raw = texts[0] if len(texts) == len(item.content) == 1 else json.dumps(
                        [b.model_dump(mode='json') for b in item.content], ensure_ascii=False)
                    attachments = []
                    for block in item.content:
                        source = getattr(block, 'source', None)
                        if source is not None and getattr(source, 'type', None) == 'base64':
                            if len(source.data) > 11_184_812 or len(attachments) >= 32:
                                raise ValueError('Attachment archive budget exceeded')
                            attachments.append(await asyncio.to_thread(self.store.capture_attachment,
                                self.scope['user_id'], self.scope['workspace_id'],
                                base64.b64decode(source.data, validate=True), source.media_type))
                    sha, locations = self._association(call.name, texts)
                    after = await asyncio.to_thread(self._file_association, call.name, args, agent)
                    if before and before == after:
                        sha = before if sha in (None, before) else None
                    elif before:
                        sha = None
                    if item.state == 'success':
                        self._register_association(call.name, texts, args, sha, agent)
                    receipt = await asyncio.to_thread(self.store.capture_call, **scope,
                        call_id=call.id, tool_name=call.name, arguments=args, raw=raw,
                        state=item.state.value if hasattr(item.state, 'value') else item.state,
                        sample_sha256=sha, locations=locations, attachments=attachments)
                    receipts = []
                    if call.name.startswith('mcp__rea__') and item.state == 'success':
                        for text in texts:
                            receipts.extend(await asyncio.to_thread(self.store.capture, **scope,
                                call_id=call.id, tool_name=call.name, arguments=args, raw=text,
                                raw_record_id=receipt['id']))
                    receipts.append(receipt)
                    ReaEvidenceMiddleware._receipt(item, {'traceglass_evidence': receipts,
                        'archive_status': 'partial' if receipt['truncated'] else 'saved',
                        'note': 'Captured observations, not verified conclusions. ReadAnalysisEvidence reads original bytes.'})
                    if len(raw) > 16_000:
                        # ToolResponse feeds native model context before its
                        # truncation/compression. Native chunks + end metadata
                        # continue to preserve streamed history and archive links.
                        item.content = [self._compact_receipt(item.metadata['traceglass_archive'])]
                except Exception:
                    logger.warning('Evidence archive failed for session %s', self.scope['session_id'])
                    ReaEvidenceMiddleware._receipt(item, {'archive_status': 'archive_failed',
                        'note': 'Original response preserved. No durable citation was created.'})
            yield item

    @staticmethod
    def _association(name, texts):
        """Read trusted tool envelope locations; never use the request's sample_sha256."""
        digests, locations = set(), []
        for text in texts:
            try:
                payload = json.loads(text)
            except (ValueError, TypeError):
                continue
            if name in {'PrepareAnalysisInput', 'inspect_file'} and isinstance(payload, dict):
                digest = payload.get('sha256')
                if isinstance(digest, str) and re.fullmatch(r'[a-fA-F0-9]{64}', digest):
                    digests.add(digest.lower())
            for _, record in observations(payload, name) if name.startswith('mcp__rea__') else []:
                digest = ((record.get('subject') or {}).get('digest') or {}).get('sha256')
                if isinstance(digest, str) and re.fullmatch(r'[a-fA-F0-9]{64}', digest):
                    digests.add(digest.lower())
                locations.extend(record.get('locations') or [])
        return (next(iter(digests)) if len(digests) == 1 else None), locations
