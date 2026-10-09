"""Expose existing analysis functions over MCP inside the native Docker workspace."""
from __future__ import annotations

import asyncio
from functools import wraps
import hashlib
import importlib
import importlib.util
import inspect
import os
from pathlib import Path
import sys
from typing import Any

from mcp.server.fastmcp import FastMCP
from mcp.types import ToolAnnotations


def _load_modules(root: Path):
    source = root / 'src' / 're_agents'
    name = '_traceglass_domain_' + hashlib.sha256(str(source).encode()).hexdigest()[:16]
    if name not in sys.modules:
        spec = importlib.util.spec_from_file_location(name, source / '__init__.py', submodule_search_locations=[str(source)])
        if spec is None or spec.loader is None:
            raise ValueError('Legacy domain package is missing')
        module = importlib.util.module_from_spec(spec)
        sys.modules[name] = module
        spec.loader.exec_module(module)
    return [importlib.import_module(f'{name}.{part}') for part in ('tools', 'skill_support', 'case_archive')]


def _async_tool(function):
    @wraps(function)
    async def run(**kwargs):
        return await asyncio.to_thread(function, **kwargs)
    # Resolve legacy postponed annotations in their own namespace. A historical
    # tool uses Any without importing it; complete that type namespace here.
    signature = inspect.signature(function, eval_str=True, globals={**function.__globals__, 'Any': Any})
    run.__signature__ = signature
    run.__annotations__ = {p.name: p.annotation for p in signature.parameters.values() if p.annotation is not inspect.Parameter.empty}
    if signature.return_annotation is not inspect.Signature.empty:
        run.__annotations__['return'] = signature.return_annotation
    return run


def build_server(root: Path, workdir: Path) -> FastMCP:
    root, workdir = root.resolve(strict=True), workdir.resolve(strict=True)
    if workdir.is_relative_to(root):
        raise ValueError('Analysis output must be outside the legacy project')
    tools_module, skills_module, archive_module = _load_modules(root)
    for name in ('samples', 'rules', 'reports'):
        (workdir / name).mkdir(exist_ok=True)
    case_root = workdir / 'artifacts' / 'cases' / 'default'
    archive = archive_module.CaseArchive(case_root)
    archive_module.write_json(case_root / 'sandbox-binding.json', {
        'status':'configured', 'backend':'agentscope-docker',
        'sandbox_workdir':str(archive.sandbox),
        'console_directory':str(archive.sandbox),
    })
    settings = tools_module.AnalysisSettings(
        sample_root=workdir / 'samples', rules_root=workdir / 'rules',
        output_root=case_root, dynamic_report_root=workdir / 'reports',
        ghidra_script_root=str(root / 'ghidra_scripts'), allow_dynamic_execution=False,
        ida_enabled=bool(os.environ.get('IDA_MCP_URL')),
        ida_mcp_url=os.environ.get('IDA_MCP_URL', ''),
    )
    capabilities = tools_module.StaticAnalysisTools(settings)
    functions = tools_module.select_tool_functions(capabilities, list(tools_module.TOOL_METHODS))
    catalog = skills_module.discover_skill_dirs([root / 'skills'])
    resources = skills_module.SkillResourceReader([root / 'skills'], list(catalog), skill_dirs=catalog)
    functions.extend([resources.read_skill_resource, archive.analysis_case_info,
                      archive.read_command_output, archive.save_analysis_artifact,
                      archive.record_evidence, archive.validate_reverse_report,
                      archive.archive_sandbox_experiment, archive.record_crypto_experiment])
    writes = {'save_analysis_artifact', 'record_evidence', 'validate_reverse_report',
              'archive_sandbox_experiment', 'record_crypto_experiment', 'reverse_tool_call'}
    server = FastMCP('Traceglass Reverse', instructions=(
        'Existing reverse tools run in the Docker workspace. Sample paths are relative '
        'to /workspace/samples; report paths to /workspace/reports. Use analysis_case_info '
        'for experiment/output paths. Native Bash runs in the same container.'))
    for function in functions:
        server.add_tool(_async_tool(function), name=function.__name__,
                        annotations=ToolAnnotations(readOnlyHint=function.__name__ not in writes,
                                                    destructiveHint=False))
    return server


def main():
    root = Path(os.environ.get('TRACEGLASS_LEGACY_ROOT', '/opt/traceglass/legacy'))
    workdir = Path(os.environ.get('TRACEGLASS_WORKDIR', '/workspace'))
    build_server(root, workdir).run(transport='stdio')


if __name__ == '__main__':
    main()
