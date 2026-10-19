"""Administrator-owned guest policy; credentials never appear in schemas."""
from dataclasses import dataclass, field
import json
import os
from pathlib import Path, PureWindowsPath
import re


@dataclass(frozen=True)
class WindowsConfig:
    host: str
    vm_id: str
    vm_name: str
    esxi_user: str
    esxi_password: str = field(repr=False)
    guest_user: str = field(repr=False)
    guest_password: str = field(repr=False)
    certificate: Path
    allowed_users: tuple[str, ...]
    tools: dict[str, str]
    guest_root: str = r'C:\Traceglass'
    allow_execution: bool = False
    baseline_snapshot: str | None = None
    max_input_bytes: int = 100 * 1024 * 1024
    max_artifact_bytes: int = 32 * 1024 * 1024
    max_timeout: int = 300
    max_concurrent_jobs: int = 1
    auto_reset_on_idle: bool = False

    def __post_init__(self):
        if not re.fullmatch(r'[A-Za-z0-9.-]+', self.host) or not self.vm_id or not self.vm_name:
            raise ValueError('Windows configuration requires a fixed host and VM identity')
        if (not isinstance(self.allowed_users, (list, tuple)) or not self.allowed_users
                or any(not isinstance(u, str) or not u.strip() or '*' in u for u in self.allowed_users)):
            raise ValueError('Windows configuration requires explicit allowed user IDs')
        if not isinstance(self.allow_execution, bool):
            raise ValueError('allow_execution must be a JSON boolean')
        if type(self.max_concurrent_jobs) is not int or not 1 <= self.max_concurrent_jobs <= 64:
            raise ValueError('max_concurrent_jobs must be an integer from 1 to 64')
        if not isinstance(self.auto_reset_on_idle, bool):
            raise ValueError('auto_reset_on_idle must be a JSON boolean')
        if self.auto_reset_on_idle and not self.baseline_snapshot:
            raise ValueError('Automatic recovery requires a configured clean baseline snapshot')
        if self.baseline_snapshot is not None and (not isinstance(self.baseline_snapshot, str) or not self.baseline_snapshot.strip()):
            raise ValueError('baseline_snapshot must name a fixed administrator-approved snapshot')
        if not self.tools.get('python'):
            raise ValueError('Windows Python executable is required')
        for value in (self.guest_root, *self.tools.values()):
            path = PureWindowsPath(value)
            if not path.is_absolute() or path.drive.startswith('\\') or '..' in path.parts or any(c in value for c in '\0\r\n"'):
                raise ValueError('Use fixed local absolute Windows paths')
        if not 1 <= self.max_timeout <= 1800 or not 1 <= self.max_input_bytes <= 512 * 1024**2:
            raise ValueError('Invalid Windows execution budget')
        if not 1 <= self.max_artifact_bytes <= 512 * 1024**2:
            raise ValueError('Invalid Windows artifact budget')

    def allowed(self, user_id):
        return user_id in self.allowed_users

    @classmethod
    def load(cls, path):
        path = Path(path).expanduser().resolve()
        data = json.loads(path.read_text())
        for key in ('esxi_password', 'guest_password'):
            variable = data.pop(key + '_env', '')
            if key in data or not variable or not os.environ.get(variable):
                raise ValueError('Windows credentials require populated environment references')
            data[key] = os.environ[variable]
        data['certificate'] = (path.parent / data['certificate']).resolve()
        data['allowed_users'] = tuple(data.get('allowed_users', ()))
        return cls(**data)
