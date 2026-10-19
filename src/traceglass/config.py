"""Deployment configuration; runtime behavior belongs to AgentScope."""
from __future__ import annotations

from dataclasses import dataclass, field
import json
import math
import os
from pathlib import Path
import re
import shutil
from urllib.parse import urlsplit

from agentscope.mcp import MCPClient, HttpMCPConfig, StdioMCPConfig

from traceglass.integrations.legacy_settings import read_environment


@dataclass(frozen=True)
class Settings:
    data_dir: Path = field(default_factory=lambda: Path("traceglass-data"))
    model_name: str = ""
    api_key: str = field(default="", repr=False)
    base_url: str | None = None
    model_parameters: dict = field(default_factory=dict)
    legacy_project: Path | None = None
    workspace_backend: str = 'local'
    sandbox_image: str = 'local/traceglass-analysis:latest'
    ida_mcp_url: str | None = None
    rea_kit: Path | None = None
    strict_evidence_references: bool = True
    rea_max_connections: int = 10
    rea_idle_seconds: int = 600
    rea_queue_wait_seconds: float = 30
    ida_local_files: bool = False
    skill_roots: tuple[Path, ...] = ()
    mcp_config_path: Path | None = None
    user_id: str = "local"
    max_iters: int = 2000
    auth_enabled: bool = False
    secure_cookies: bool = False
    allowed_origins: tuple[str, ...] = ('http://127.0.0.1:5177', 'http://localhost:5177')
    model_allowed_hosts: tuple[str, ...] = (
        'api.openai.com', 'api.deepseek.com', 'dashscope.aliyuncs.com',
        'open.bigmodel.cn', 'api.moonshot.cn', 'api.siliconflow.cn',
        'ark.cn-beijing.volces.com', 'api.minimax.chat', 'api.minimaxi.com',
        'llm.asiainfo-sec.com',
    )
    model_allowed_origins: tuple[str, ...] = ()
    model_tls_config: Path | None = None
    windows_config: Path | None = None

    def __post_init__(self):
        for origin in self.model_allowed_origins:
            try:
                parsed = urlsplit(origin)
                valid = (parsed.scheme == 'https' and parsed.hostname and
                         parsed.port != 0 and parsed.path in ('', '/') and
                         not parsed.username and not parsed.password and not parsed.query and
                         not parsed.fragment and '\\' not in origin and
                         not any(ord(c) < 33 for c in origin))
            except ValueError:
                valid = False
            if not valid:
                raise ValueError('TRACEGLASS_MODEL_ALLOWED_ORIGINS requires HTTPS origins (host and optional port, no API path)')
        if self.auth_enabled and self.workspace_backend != 'docker':
            raise ValueError('Multi-user deployments require Docker workspaces')
        if self.auth_enabled:
            for origin in self.allowed_origins:
                parsed = urlsplit(origin)
                local = parsed.hostname in {'localhost','127.0.0.1','::1','testserver'}
                if (not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment
                        or parsed.path not in {'','/'} or parsed.scheme not in {'http','https'}
                        or (not local and (parsed.scheme != 'https' or not self.secure_cookies))):
                    raise ValueError('Public registration requires HTTPS origins and secure cookies')
        if self.auth_enabled and not self.allowed_origins:
            raise ValueError('Configure explicit browser origins for multi-user mode')
        if self.max_iters < 1:
            raise ValueError("TRACEGLASS_MAX_ITERS must be positive")
        if (self.rea_max_connections < 1 or self.rea_idle_seconds < 0 or
                not math.isfinite(self.rea_queue_wait_seconds) or self.rea_queue_wait_seconds <= 0):
            raise ValueError('Invalid REA connection quota or idle timeout')
        if not self.user_id.strip():
            raise ValueError("TRACEGLASS_USER_ID must not be blank")
        if self.workspace_backend not in {'local', 'docker'}:
            raise ValueError('TRACEGLASS_WORKSPACE_BACKEND must be local or docker')
        if self.legacy_project and self.workspace_backend != 'docker':
            raise ValueError('Legacy analysis tools require the Docker workspace')

    @classmethod
    def from_env(cls) -> Settings:
        environment, legacy_project, model_parameters = read_environment()
        roots = environment.get("TRACEGLASS_SKILL_ROOTS", "")
        mcp_path = environment.get("TRACEGLASS_MCP_CONFIG")
        skill_roots = tuple(Path(p).expanduser().resolve() for p in roots.split(os.pathsep) if p)
        if environment.get('LLM_PARAMETERS'):
            model_parameters = json.loads(environment['LLM_PARAMETERS'])
        from agentscope.model import OpenAIChatModel
        model_parameters = OpenAIChatModel.Parameters.model_validate(model_parameters).model_dump(exclude_none=True)
        return cls(
            data_dir=Path(environment.get("TRACEGLASS_DATA_DIR", "traceglass-data")).expanduser().resolve(),
            model_name=environment.get("LLM_MODEL", "").strip(),
            api_key=environment.get("LLM_API_KEY", "").strip(),
            base_url=environment.get("LLM_BASE_URL") or None,
            model_parameters=model_parameters,
            legacy_project=legacy_project,
            workspace_backend=environment.get('TRACEGLASS_WORKSPACE_BACKEND', 'docker' if legacy_project else 'local'),
            sandbox_image=environment.get('TRACEGLASS_SANDBOX_IMAGE', 'local/traceglass-analysis:latest'),
            ida_mcp_url=environment.get('IDA_MCP_URL') if environment.get('IDA_ENABLED', '').lower() in {'true','1','yes'} else None,
            ida_local_files=environment.get('TRACEGLASS_IDA_LOCAL_FILES', '').lower() in {'true','1','yes'},
            rea_kit=Path(environment['TRACEGLASS_REA_KIT']).expanduser().resolve() if environment.get('TRACEGLASS_REA_KIT') else None,
            strict_evidence_references=environment.get('TRACEGLASS_STRICT_EVIDENCE_REFERENCES', 'true').lower() in {'true', '1', 'yes'},
            rea_max_connections=int(environment.get('TRACEGLASS_REA_MAX_CONNECTIONS', '10')),
            rea_idle_seconds=int(environment.get('TRACEGLASS_REA_IDLE_SECONDS', '600')),
            rea_queue_wait_seconds=float(environment.get('TRACEGLASS_REA_QUEUE_WAIT_SECONDS', '30')),
            skill_roots=skill_roots,
            mcp_config_path=Path(mcp_path).expanduser().resolve() if mcp_path else None,
            user_id=environment.get("TRACEGLASS_USER_ID", "local"),
            max_iters=int(environment.get("TRACEGLASS_MAX_ITERS", "2000")),
            auth_enabled=environment.get('TRACEGLASS_AUTH_ENABLED', '').lower() in {'true','1','yes'},
            secure_cookies=environment.get('TRACEGLASS_SECURE_COOKIES', '').lower() in {'true','1','yes'},
            allowed_origins=tuple(x.strip().rstrip('/') for x in environment.get('TRACEGLASS_ALLOWED_ORIGINS',
                'http://127.0.0.1:5177,http://localhost:5177').split(',') if x.strip()),
            model_allowed_hosts=tuple(x.strip().lower() for x in environment.get('TRACEGLASS_MODEL_ALLOWED_HOSTS',
                ','.join(cls.model_allowed_hosts)).split(',') if x.strip()),
            model_allowed_origins=tuple(x.strip().rstrip('/') for x in environment.get('TRACEGLASS_MODEL_ALLOWED_ORIGINS', '').split(',') if x.strip()),
            model_tls_config=Path(environment['TRACEGLASS_MODEL_TLS_CONFIG']).expanduser().resolve() if environment.get('TRACEGLASS_MODEL_TLS_CONFIG') else None,
            windows_config=Path(environment['TRACEGLASS_WINDOWS_CONFIG']).expanduser().resolve() if environment.get('TRACEGLASS_WINDOWS_CONFIG') else None,
        )

    @property
    def model_configured(self) -> bool:
        return bool(self.model_name and self.api_key)


def skill_paths(settings: Settings) -> list[str]:
    """Resolve inherited defaults, project contracts, then explicit user packages."""
    builtins = Path(__file__).parent / "resources" / "skills"
    roots = [(settings.legacy_project / 'skills', False)] if settings.legacy_project else []
    roots.extend([(builtins, False), *((root, True) for root in settings.skill_roots)])
    packages: dict[str, Path] = {}
    explicit_names: dict[str, Path] = {}
    for root, is_explicit in roots:
        if not root.is_dir():
            raise ValueError(f"Skill directory does not exist: {root}")
        root_names: dict[str, Path] = {}
        manifests = sorted(root.rglob("SKILL.md"))
        for manifest in manifests:
            package = manifest.parent.resolve()
            previous = root_names.get(package.name)
            if previous is not None and previous != package:
                raise ValueError(f"Duplicate Skill package directory name: {package.name}")
            root_names[package.name] = package
            if is_explicit:
                previous = explicit_names.get(package.name)
                if previous is not None and previous != package:
                    raise ValueError(f"Duplicate Skill package directory name: {package.name}")
                explicit_names[package.name] = package
            packages[package.name] = package
    return [str(path) for path in packages.values()]


def materialize_skill_paths(settings: Settings, staging_root: Path) -> list[str]:
    """Create native-loader copies with unique frontmatter names.

    External packages may use the same frontmatter name while living in
    different directories. AgentScope indexes by the frontmatter name, so
    preserve both packages but give only the later collision a stable
    qualified name in the workspace copy. The original Skill files remain
    untouched and the legacy MCP resource reader keeps their original names.
    """
    paths = skill_paths(settings)
    staging_root.mkdir(parents=True, exist_ok=True)
    used: set[str] = set()
    result: list[str] = []
    for raw in paths:
        source = Path(raw)
        manifest = source / 'SKILL.md'
        text = manifest.read_text(encoding='utf-8')
        match = re.search(r'(?m)^name:\s*([^\n]+)\s*$', text)
        name = match.group(1).strip() if match else source.name
        nested_manifests = [item for item in source.rglob('SKILL.md') if item != manifest]
        if name not in used and not nested_manifests:
            used.add(name)
            result.append(str(source))
            continue
        qualified = name if name not in used else f'{name}-{source.parent.name}'
        while qualified in used:
            qualified += '-legacy'
        destination = staging_root / qualified
        if destination.exists():
            shutil.rmtree(destination)
        shutil.copytree(source, destination)
        if match:
            destination.joinpath('SKILL.md').write_text(
                text[:match.start(1)] + qualified + text[match.end(1):], encoding='utf-8',
            )
        # AgentScope recursively discovers manifests. Keep nested resources
        # available to the parent Skill, but let the separately assigned
        # nested package own its single SKILL.md.
        for nested in destination.rglob('SKILL.md'):
            if nested != destination / 'SKILL.md':
                nested.unlink()
        used.add(qualified)
        result.append(str(destination))
    return result


def mcp_clients(settings: Settings) -> list[MCPClient]:
    """Validate native MCPClient records without connecting or launching them."""
    records = json.loads(settings.mcp_config_path.read_text(encoding="utf-8")) if settings.mcp_config_path else []
    if not isinstance(records, list):
        raise ValueError("MCP configuration must be a JSON list of native MCPClient records")

    def expand(value):
        if isinstance(value, str):
            def substitute(match):
                name = match.group(1)
                if name not in os.environ:
                    raise ValueError(f"Missing MCP environment variable: {name}")
                return os.environ[name]
            return re.sub(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}", substitute, value)
        if isinstance(value, list):
            return [expand(v) for v in value]
        if isinstance(value, dict):
            return {k: expand(v) for k, v in value.items()}
        return value

    clients = [MCPClient.model_validate(expand(record)) for record in records]
    if settings.rea_kit and any(client.name == 'rea' for client in clients):
        raise ValueError('Configure REA through either TRACEGLASS_REA_KIT or MCP JSON, not both')
    if settings.legacy_project:
        clients.append(MCPClient(
            name='reverse', is_stateful=True, execution_timeout=660,
            mcp_config=StdioMCPConfig(command='/usr/bin/python3', args=['/opt/traceglass/legacy_mcp.py'], env={
                'TRACEGLASS_WORKDIR':'/workspace',
                'TRACEGLASS_LEGACY_ROOT':'/opt/traceglass/legacy',
                'PYTHONPATH':'/opt/traceglass/legacy/src',
                'IDA_MCP_URL':settings.ida_mcp_url or '',
                'IDA_ENABLED':'true' if settings.ida_mcp_url else 'false',
            }),
        ))
    if settings.ida_mcp_url:
        clients.append(MCPClient(name='idapro', is_stateful=False, execution_timeout=300,
                                 mcp_config=HttpMCPConfig(url=settings.ida_mcp_url)))
    if len({c.name for c in clients}) != len(clients):
        raise ValueError("MCP server names must be unique")
    return clients
