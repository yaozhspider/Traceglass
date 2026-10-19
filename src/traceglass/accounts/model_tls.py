"""Administrator-scoped CA trust and TLS identity for self-hosted model origins.

The HTTP destination stays unchanged. Only an explicitly allowed origin gets
its private CA and certificate identity, with chain/name verification enabled.
"""
from dataclasses import dataclass
import json
from pathlib import Path
import ssl
from urllib.parse import urlsplit

import httpx
from agentscope.middleware import MiddlewareBase
from agentscope.model import OpenAIChatModel


def origin_key(url):
    parsed = urlsplit(str(url))
    return parsed.scheme, parsed.hostname, parsed.port or 443


@dataclass(frozen=True)
class TLSProfile:
    origin: tuple
    context: ssl.SSLContext
    server_name: str


class OriginTLSTransport(httpx.AsyncBaseTransport):
    def __init__(self, profile):
        self.profile = profile
        self.transport = httpx.AsyncHTTPTransport(verify=profile.context, trust_env=False)

    async def handle_async_request(self, request):
        if origin_key(request.url) != self.profile.origin:
            raise httpx.ConnectError('Scoped model TLS client cannot access another origin', request=request)
        # httpcore verifies this certificate identity while connecting to the
        # original URL's host/port. No verify=False or global trust-store change.
        request.extensions['sni_hostname'] = self.profile.server_name
        return await self.transport.handle_async_request(request)

    async def aclose(self):
        await self.transport.aclose()


class ModelTLSPolicy:
    def __init__(self, config_path: Path | None, allowed_origins=()):
        self.profiles = {}
        if config_path is None:
            return
        config_path = Path(config_path).expanduser().resolve(strict=True)
        entries = json.loads(config_path.read_text())
        if not isinstance(entries, list):
            raise ValueError('Model TLS configuration must be a list of origin/ca_file/server_name profiles')
        allowed = {origin_key(url) for url in allowed_origins}
        for entry in entries:
            if not isinstance(entry, dict) or set(entry) != {'origin', 'ca_file', 'server_name'}:
                raise ValueError('Each model TLS profile requires origin, ca_file and server_name')
            if any(not isinstance(value, str) or not value.strip() for value in entry.values()):
                raise ValueError('Model TLS profile fields must be nonempty strings')
            parsed = urlsplit(entry['origin'])
            origin = origin_key(entry['origin'])
            if (origin not in allowed or parsed.scheme != 'https' or parsed.path not in ('', '/') or
                    parsed.username or parsed.password or parsed.query or parsed.fragment or
                    origin in self.profiles):
                raise ValueError('Model TLS profiles require unique, explicitly allowed HTTPS origins')
            name = entry['server_name']
            if any(ord(c) < 33 for c in name) or any(c in name for c in '/\\:@?#'):
                raise ValueError('TLS server_name must be a certificate DNS name or IPv4 address')
            ca_file = Path(entry['ca_file']).expanduser()
            if not ca_file.is_absolute():
                ca_file = config_path.parent / ca_file
            context = ssl.create_default_context(cafile=str(ca_file.resolve(strict=True)))
            self.profiles[origin] = TLSProfile(origin, context, name)

    def profile(self, url):
        return self.profiles.get(origin_key(url))

    def client(self, url, **kwargs):
        profile = self.profile(url)
        options = {'timeout': 25, 'follow_redirects': False, 'trust_env': False, **kwargs}
        if profile:
            options['transport'] = OriginTLSTransport(profile)
        return httpx.AsyncClient(**options)


class ModelTLSMiddleware(MiddlewareBase):
    """Adapt native OpenAI SDK clients for this reply, including compression/fallback.

This runs for leaders and workers through the same native middleware factory.
Unconfigured origins retain their original clients and verification settings.
"""
    def __init__(self, policy):
        self.policy = policy

    async def on_reply(self, agent, input_kwargs, next_handler):
        replacements = []
        models = [agent.model, agent.model_config.fallback_model]
        seen = set()
        try:
            for model in models:
                if not isinstance(model, OpenAIChatModel) or id(model) in seen:
                    continue
                seen.add(id(model))
                original = model.client
                if not self.policy.profile(original.base_url):
                    continue
                client = self.policy.client(original.base_url, timeout=original.timeout)
                try:
                    replacement = original.with_options(http_client=client)
                except BaseException:
                    await client.aclose()
                    raise
                model.client = replacement
                replacements.append((model, original, replacement))
            async for event in next_handler(**input_kwargs):
                yield event
        finally:
            for model, original, replacement in reversed(replacements):
                model.client = original
                await replacement.close()
