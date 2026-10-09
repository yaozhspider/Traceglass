"""Prepare workspace inputs for analyzers without running any target code."""
from __future__ import annotations

import asyncio
import hashlib
import os
from pathlib import Path
import re
import tempfile

from agentscope.tool import FunctionTool


class AnalysisInput:
    def __init__(self, root: Path, staging: Path, *, docker: bool, ida_local_files: bool):
        self.root = root.resolve()
        self.staging = staging.resolve()
        self.docker = docker
        self.ida_local_files = ida_local_files

    async def prepare_analysis_input(self, path: str) -> dict:
        """Resolve and hash an existing workspace binary before opening an analyzer.

        Returns REA's /workspace path. For explicitly configured same-host IDA,
        also returns a read-only staged host path for mcp__idapro__session_create.
        Remote IDA gets no guessed path: use REA or configure its file transfer.
        This copies bytes only; it never executes or imports the target.

        Args:
            path: Workspace-relative path, or /workspace/... in Docker mode.
        """
        return await asyncio.to_thread(self._prepare, path)

    def _prepare(self, raw: str) -> dict:
        path = Path(raw)
        if path.is_absolute():
            prefix = Path('/workspace') if self.docker else self.root
            try:
                path = path.relative_to(prefix)
            except ValueError:
                raise ValueError('Input must be inside this workspace.') from None
        source = (self.root / path).resolve()
        if not source.is_relative_to(self.root):
            raise ValueError('Input must be inside this workspace.')
        if not source.is_file():
            raise FileNotFoundError(f'Workspace file not found: {raw}')
        # A bounded streamed snapshot keeps source changes from invalidating the
        # hash/path pair. Staging lives outside the agent-writable workspace.
        self.staging.mkdir(parents=True, exist_ok=True, mode=0o700)
        digest, size = hashlib.sha256(), 0
        fd, temporary = tempfile.mkstemp(prefix='.input-', dir=self.staging)
        try:
            with source.open('rb') as incoming, os.fdopen(fd, 'wb') as outgoing:
                for block in iter(lambda: incoming.read(1024*1024), b''):
                    size += len(block)
                    if size > 512*1024*1024:
                        raise ValueError('Input exceeds the 512 MiB preparation limit.')
                    digest.update(block)
                    outgoing.write(block)
            sha = digest.hexdigest()
            host_path = None
            if self.ida_local_files:
                suffix = source.suffix if re.fullmatch(r'\.[A-Za-z0-9]{1,12}', source.suffix) else '.bin'
                # Unique directory avoids collisions with an existing IDB or
                # another worker's concurrently opened IDA session.
                case = Path(tempfile.mkdtemp(prefix=sha[:16]+'-', dir=self.staging))
                destination = case / ('input' + suffix)
                os.chmod(temporary, 0o444)
                os.replace(temporary, destination)
                host_path = str(destination)
            return {'sha256': sha, 'size_bytes': size,
                    'workspace_path': source.relative_to(self.root).as_posix(),
                    'rea_file_path': '/workspace/' + source.relative_to(self.root).as_posix(),
                    'ida_file_path': host_path,
                    'status': 'prepared' if host_path else 'rea_available_ida_transfer_unconfigured',
                    'next_step': 'Use ida_file_path with IDA session_create; if unavailable, open rea_file_path with REA provider ghidra. '
                                 'Record tool failures as coverage gaps; do not infer a benign verdict.'}
        finally:
            Path(temporary).unlink(missing_ok=True)

    def tool(self):
        return FunctionTool(func=self.prepare_analysis_input, name='PrepareAnalysisInput',
                            is_read_only=False, is_concurrency_safe=True)
