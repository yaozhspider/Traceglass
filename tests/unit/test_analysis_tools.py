from __future__ import annotations

import asyncio
import hashlib
import json
from pathlib import Path

import pytest

from traceglass.tools.analysis import AnalysisTools, build_analysis_tools


def test_native_tools_hash_sample_and_report_metadata(tmp_path: Path) -> None:
    from agentscope.tool import FunctionTool

    sample = tmp_path / "sample.bin"
    sample.write_bytes(b"\x7fELF\x02\x01\x00hello-world\x00")
    tools = {tool.name: tool for tool in build_analysis_tools(tmp_path)}
    assert all(isinstance(tool, FunctionTool) for tool in tools.values())
    result = asyncio.run(tools["inspect_file"].call(path="sample.bin"))
    data = json.loads(result.content[0].text)
    assert data["sha256"] == hashlib.sha256(sample.read_bytes()).hexdigest()
    assert data["format"] == "ELF"
    assert data["size_bytes"] == sample.stat().st_size


def test_tools_reject_traversal_and_symlink_escape(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (tmp_path / "secret.txt").write_text("outside workspace")
    (workspace / "link.txt").symlink_to(tmp_path / "secret.txt")
    tools = AnalysisTools(workspace)
    for path in ("../secret.txt", "link.txt"):
        with pytest.raises(ValueError, match="workspace"):
            asyncio.run(tools.inspect_file(path))


def test_strings_keep_offsets_and_signal_limits(tmp_path: Path) -> None:
    (tmp_path / "sample.bin").write_bytes(b"\x00hello\x00world\x00third\x00")
    tools = AnalysisTools(tmp_path)
    result = asyncio.run(tools.extract_strings("sample.bin", minimum_length=5, limit=2))
    assert result["strings"] == [
        {"offset": 1, "text": "hello"},
        {"offset": 7, "text": "world"},
    ]
    assert result["truncated"] is True


def test_report_write_stays_in_artifact_directory_and_preserves_existing(tmp_path: Path) -> None:
    tools = AnalysisTools(tmp_path)
    result = asyncio.run(tools.write_report("analysis/report.md", "# Evidence\n"))
    assert result["path"] == "artifacts/analysis/report.md"
    assert (tmp_path / result["path"]).read_text() == "# Evidence\n"
    with pytest.raises(FileExistsError):
        asyncio.run(tools.write_report("analysis/report.md", "replace"))
    with pytest.raises(ValueError, match="artifact"):
        asyncio.run(tools.write_report("../../sample.bin", "invalid"))


def test_all_tool_schemas_expose_declared_arguments_and_write_policy(tmp_path: Path) -> None:
    from agentscope.tool import Toolkit

    native = build_analysis_tools(tmp_path)
    toolkit = Toolkit(tools=native)
    schemas = asyncio.run(toolkit.get_tool_schemas())
    report = next(item["function"] for item in schemas if item["function"]["name"] == "write_report")
    assert set(report["parameters"]["required"]) == {"path", "content"}
    assert next(tool for tool in native if tool.name == "write_report").is_read_only is False
    assert all(tool.is_read_only for tool in native if tool.name != "write_report")
