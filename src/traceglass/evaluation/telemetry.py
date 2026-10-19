"""Observable evaluation costs and reproducibility fingerprints, never credentials."""
from collections import Counter
import asyncio
import hashlib
import json
from pathlib import Path
import subprocess
from dataclasses import asdict, is_dataclass
from collections.abc import AsyncIterable
from pydantic import BaseModel


def skill_fingerprint(path):
    files = {str(p.relative_to(path)): hashlib.sha256(p.read_bytes()).hexdigest()
             for p in sorted(Path(path).rglob('*')) if p.is_file() and '__pycache__' not in p.parts}
    return hashlib.sha256(json.dumps(files, sort_keys=True).encode()).hexdigest()


def token_metrics(usage):
    totals = {'input_tokens': 0, 'output_tokens': 0}
    observed = False
    for item in usage:
        if not isinstance(item, dict):
            continue
        for field, alias in [('input_tokens', 'prompt_tokens'), ('output_tokens', 'completion_tokens')]:
            value = item.get(field, item.get(alias))
            if isinstance(value, (int, float)):
                observed = True
                totals[field] += value
    return {**totals, 'total_tokens': sum(totals.values()) if observed else None,
            'usage_observed': observed}


def tool_metrics(transcript):
    calls, outcomes, signatures = {}, {}, Counter()
    for message in transcript:
        for block in message.get('content', []):
            if block.get('type') == 'tool_call':
                identity = block.get('id')
                if identity not in calls:
                    calls[identity] = block
                    raw = block.get('input', '')
                    try:
                        raw = json.dumps(json.loads(raw), sort_keys=True)
                    except (ValueError, TypeError):
                        raw = str(raw)
                    signatures[(block.get('name'), raw)] += 1
            elif block.get('type') == 'tool_result':
                outcomes[block.get('id')] = block.get('state', 'unknown')
    states = Counter(outcomes.values())
    failures = sum(count for state, count in states.items() if state != 'success')
    return dict(calls=len(calls), results=len(outcomes), failures=failures, states=dict(states),
                failure_rate=failures / len(outcomes) if outcomes else None,
                repeated_calls=sum(max(0, n - 1) for n in signatures.values()),
                retry_scope='identical tool+arguments repeated; purpose/automatic provider retries not inferred')


def fingerprints(kit=None):
    root = Path(__file__).parents[3]
    code = Path(__file__).parents[1]
    files = {str(p.relative_to(code)): hashlib.sha256(p.read_bytes()).hexdigest()
             for p in sorted(code.rglob('*.py'))}
    def git(*args):
        result = subprocess.run(['git', '-C', str(root), *args], capture_output=True)
        return result.stdout if result.returncode == 0 else b''
    record = dict(code_sha256=hashlib.sha256(json.dumps(files, sort_keys=True).encode()).hexdigest(),
                  git_revision=git('rev-parse', 'HEAD').decode().strip(),
                  working_diff_sha256=hashlib.sha256(git('diff', '--binary', 'HEAD')).hexdigest())
    if kit:
        for filename, key in [('MANIFEST.json', 'kit_manifest_sha256'), ('TOOLS.json', 'rea_schema_sha256')]:
            path = Path(kit) / filename
            record[key] = hashlib.sha256(path.read_bytes()).hexdigest() if path.is_file() else None
        try:
            record['rea_image_id'] = json.loads((Path(kit) / 'MANIFEST.json').read_text())['image']['image_id']
        except (OSError, ValueError, KeyError):
            record['rea_image_id'] = None
    return record


from agentscope.middleware import MiddlewareBase
from agentscope.tool import ToolResponse


class EvaluationTelemetry(MiddlewareBase):
    """Keep observed calls independent of compressed model context."""
    def __init__(self):
        self.events = []
        self.model_calls = 0
        self.model_failures = 0
        self.model_cancelled = 0
        self.prompt_hashes = set()
        self.schema_hashes = set()
        self.model_usage = []
        self.api_attempts = 0

    def _usage(self, response):
        usage = getattr(response, 'usage', None)
        if usage is not None:
            self.model_usage.append(usage.model_dump(mode='json') if isinstance(usage, BaseModel)
                                    else asdict(usage) if is_dataclass(usage) else dict(usage))

    async def on_reply(self, agent, input_kwargs, next_handler):
        # Evaluation-only adapter around the pinned model transport. Native
        # compression calls bypass agent middleware; measure them and retries
        # at the same transport boundary without altering execution semantics.
        model = agent.model
        original = getattr(model, '_call_api', None)
        if original is None:
            async for item in next_handler(**input_kwargs):
                yield item
            return
        async def measured(*args, **kwargs):
            self.api_attempts += 1
            response = await original(*args, **kwargs)
            if not isinstance(response, AsyncIterable):
                self._usage(response)
                return response
            async def stream():
                latest_usage = None
                try:
                    async for chunk in response:
                        if getattr(chunk, 'usage', None) is not None:
                            latest_usage = chunk
                        yield chunk
                finally:
                    if latest_usage is not None:
                        self._usage(latest_usage)
            return stream()
        model._call_api = measured
        try:
            async for item in next_handler(**input_kwargs):
                yield item
        finally:
            model._call_api = original

    async def on_model_call(self, agent, input_kwargs, next_handler):
        self.model_calls += 1
        prompt = '\n'.join(m.get_text_content() or '' for m in input_kwargs.get('messages', []) if m.role == 'system')
        self.prompt_hashes.add(hashlib.sha256(prompt.encode()).hexdigest())
        self.schema_hashes.add(hashlib.sha256(json.dumps(input_kwargs.get('tools') or [], sort_keys=True).encode()).hexdigest())
        try:
            return await next_handler(**input_kwargs)
        except Exception:
            self.model_failures += 1
            raise
        except asyncio.CancelledError:
            self.model_cancelled += 1
            raise

    async def on_acting(self, agent, input_kwargs, next_handler):
        call = input_kwargs['tool_call']
        self.events.append({'content':[{'type':'tool_call', 'id':call.id, 'name':call.name, 'input':call.input}]})
        seen = False
        try:
            async for item in next_handler(**input_kwargs):
                if isinstance(item, ToolResponse) and not seen:
                    seen = True
                    state = item.state.value if hasattr(item.state, 'value') else str(item.state)
                    self.events.append({'content':[{'type':'tool_result', 'id':call.id, 'name':call.name, 'state':state}]})
                yield item
        except BaseException as exc:
            if not seen:
                state = 'cancelled' if isinstance(exc, asyncio.CancelledError) else 'exception'
                self.events.append({'content':[{'type':'tool_result', 'id':call.id, 'name':call.name, 'state':state}]})
            raise

    def snapshot(self):
        return dict(tool_metrics={**tool_metrics(self.events), 'scope':'permission-approved execution invocations'},
            model_usage=list(self.model_usage), token_metrics={**token_metrics(self.model_usage),
                'scope':'observed native API response usage, including compression; provider-unreported usage unavailable'},
            model_metrics=dict(calls=self.model_calls, failures=self.model_failures, cancelled=self.model_cancelled,
                provider_api_attempts=self.api_attempts,
                provider_internal_retries=None, scope='middleware model invocations; provider-internal transport attempts unavailable'),
            assembled_prompt_hashes=sorted(self.prompt_hashes), exposed_tool_schema_hashes=sorted(self.schema_hashes))
