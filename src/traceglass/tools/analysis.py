"""Small workspace-scoped analysis tools for the native AgentScope runtime.

These tools inspect bytes and save reports; they never execute a sample.
AgentScope owns schemas, dispatch, tool events, and error normalization.
Blocking filesystem work runs in a thread so it cannot stall the event stream.
"""

from __future__ import annotations

import asyncio
import hashlib
from pathlib import Path
import re
from typing import Any


MAX_SCAN_BYTES = 16_777_216
MAX_REPORT_BYTES = 2_097_152


class AnalysisTools:
    """Filesystem capabilities bound to one workspace and artifact directory."""

    def __init__(self, workspace: str | Path, *, artifacts_dir: str = "artifacts") -> None:
        self.workspace = Path(workspace).expanduser().resolve(strict=True)
        if not self.workspace.is_dir():
            raise ValueError("workspace must be a directory")
        self.artifacts = self._within(artifacts_dir, self.workspace, "workspace")
        if self.artifacts == self.workspace:
            raise ValueError("artifact directory must be a subdirectory of the workspace")

    @staticmethod
    def _within(path: str, root: Path, label: str) -> Path:
        value = Path(path)
        if value.is_absolute():
            raise ValueError(f"path must be relative to the {label}")
        resolved = (root / value).resolve()
        if not resolved.is_relative_to(root):
            raise ValueError(f"path must stay inside the {label}")
        return resolved

    def _sample(self, path: str) -> Path:
        resolved = self._within(path, self.workspace, "workspace")
        if not resolved.is_file():
            raise FileNotFoundError(f"workspace file not found: {path}")
        return resolved

    async def inspect_file(self, path: str) -> dict[str, Any]:
        """Return file size, SHA-256, and a header-based format identification.

        Args:
            path: File path relative to the workspace.
        """
        def run() -> dict[str, Any]:
            sample = self._sample(path)
            digest = hashlib.sha256()
            size = 0
            with sample.open("rb") as stream:
                header = stream.read(16)
                digest.update(header)
                size += len(header)
                for chunk in iter(lambda: stream.read(1_048_576), b""):
                    digest.update(chunk)
                    size += len(chunk)
            signatures = (
                (b"\x7fELF", "ELF"), (b"MZ", "DOS/PE candidate"),
                (b"\xcf\xfa\xed\xfe", "Mach-O"), (b"\xce\xfa\xed\xfe", "Mach-O"),
                (b"\xfe\xed\xfa\xcf", "Mach-O"), (b"\xfe\xed\xfa\xce", "Mach-O"),
                (b"\xca\xfe\xba\xbe", "Mach-O universal / Java class candidate"),
                (b"PK\x03\x04", "ZIP"), (b"%PDF-", "PDF"),
            )
            format_name = next((name for magic, name in signatures if header.startswith(magic)), "unknown")
            return {
                "path": str(sample.relative_to(self.workspace)),
                "size_bytes": size,
                "sha256": digest.hexdigest(),
                "format": format_name,
                "header_hex": header.hex(),
            }

        return await asyncio.to_thread(run)

    async def extract_strings(
        self, path: str, minimum_length: int = 6, limit: int = 500,
    ) -> dict[str, Any]:
        """Extract printable ASCII strings and byte offsets from at most 16 MiB.

        Args:
            path: File path relative to the workspace.
            minimum_length: Minimum string length, from 3 to 256 characters.
            limit: Maximum returned strings, from 1 to 2000.
        """
        if not 3 <= minimum_length <= 256:
            raise ValueError("minimum_length must be between 3 and 256")
        if not 1 <= limit <= 2000:
            raise ValueError("limit must be between 1 and 2000")

        def run() -> dict[str, Any]:
            sample = self._sample(path)
            with sample.open("rb") as stream:
                data = stream.read(MAX_SCAN_BYTES + 1)
            truncated = len(data) > MAX_SCAN_BYTES
            data = data[:MAX_SCAN_BYTES]
            strings = []
            for match in re.finditer(rb"[\x20-\x7e]{" + str(minimum_length).encode() + rb",}", data):
                if len(strings) == limit:
                    truncated = True
                    break
                value = match.group()
                item: dict[str, Any] = {"offset": match.start(), "text": value[:4096].decode("ascii")}
                if len(value) > 4096:
                    item["text_truncated"] = True
                strings.append(item)
            return {
                "path": str(sample.relative_to(self.workspace)),
                "encoding": "ASCII", "scanned_bytes": len(data),
                "strings": strings, "truncated": truncated,
            }

        return await asyncio.to_thread(run)

    async def write_report(self, path: str, content: str) -> dict[str, Any]:
        """Create a UTF-8 report beneath artifacts without replacing existing files.

        Args:
            path: New report path relative to the artifact directory.
            content: UTF-8 report content, at most 2 MiB.
        """
        encoded = content.encode("utf-8")
        if len(encoded) > MAX_REPORT_BYTES:
            raise ValueError(f"report must not exceed {MAX_REPORT_BYTES} bytes")

        def run() -> dict[str, Any]:
            # Recheck the artifact root in case it was replaced by a symlink.
            if not self.artifacts.resolve().is_relative_to(self.workspace):
                raise ValueError("artifact directory must stay inside the workspace")
            destination = self._within(path, self.artifacts, "artifact directory")
            destination.parent.mkdir(parents=True, exist_ok=True)
            with destination.open("xb") as stream:
                stream.write(encoded)
            return {
                "path": str(destination.relative_to(self.workspace)),
                "size_bytes": len(encoded),
                "sha256": hashlib.sha256(encoded).hexdigest(),
            }

        return await asyncio.to_thread(run)


def build_analysis_tools(workspace: str | Path, *, artifacts_dir: str = "artifacts") -> list[Any]:
    """Build native FunctionTools, using AgentScope's own schema and dispatch."""
    from agentscope.tool import FunctionTool

    tools = AnalysisTools(workspace, artifacts_dir=artifacts_dir)
    return [
        FunctionTool(func=tools.inspect_file, is_read_only=True),
        FunctionTool(func=tools.extract_strings, is_read_only=True),
        FunctionTool(func=tools.write_report, is_read_only=False, is_concurrency_safe=False),
    ]


__all__ = ["AnalysisTools", "build_analysis_tools"]
