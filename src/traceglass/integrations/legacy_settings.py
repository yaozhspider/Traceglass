"""Read deployment settings from an explicitly selected project, without importing it."""
from __future__ import annotations

import os
import re
from pathlib import Path

from dotenv import dotenv_values
import yaml


def read_environment() -> tuple[dict[str, str], Path | None, dict]:
    local_file = Path(os.environ.get('TRACEGLASS_ENV_FILE', '.env')).expanduser()
    explicit = {k: v for k, v in dotenv_values(local_file, interpolate=False).items() if v is not None}
    explicit.update(os.environ)
    project = explicit.get('TRACEGLASS_LEGACY_PROJECT', '').strip()
    if not project:
        return explicit, None, {}
    root = Path(project).expanduser().resolve()
    config_path = root / 'config' / 'agents.yaml'
    if not config_path.is_file():
        raise ValueError('TRACEGLASS_LEGACY_PROJECT must contain config/agents.yaml')
    environment = {k: v for k, v in dotenv_values(root / '.env', interpolate=False).items() if v is not None}
    environment.update(explicit)
    try:
        raw = yaml.safe_load(config_path.read_text(encoding='utf-8'))
    except yaml.YAMLError:
        raise ValueError('Invalid legacy YAML configuration') from None
    if not isinstance(raw, dict) or not isinstance(raw.get('model'), dict):
        raise ValueError('Legacy configuration must contain a model mapping')

    def expand(value):
        if isinstance(value, str):
            return re.sub(r'\$\{([A-Za-z_][A-Za-z0-9_]*)(?::-([^}]*))?\}',
                          lambda m: environment.get(m[1]) or m[2] or '', value)
        if isinstance(value, dict):
            return {k: expand(v) for k, v in value.items()}
        if isinstance(value, list):
            return [expand(v) for v in value]
        return value

    model = expand(raw['model'])
    if model.get('provider', 'openai_compatible') != 'openai_compatible':
        raise ValueError('Legacy model provider needs an explicit native model adapter')
    environment.setdefault('LLM_MODEL', str(model.get('model_name', '')))
    environment.setdefault('LLM_API_KEY', environment.get(model.get('api_key_env', 'LLM_API_KEY')) or str(model.get('api_key', '')))
    environment.setdefault('LLM_BASE_URL', environment.get(model.get('base_url_env', 'LLM_BASE_URL')) or str(model.get('base_url') or ''))
    return environment, root, model.get('parameters', {})
