"""Build a tool image from an existing reverse installation without its credentials."""
from __future__ import annotations

import argparse
from pathlib import Path
import shutil
import subprocess
import tempfile


def build_context(project: Path, destination: Path) -> None:
    project = project.resolve(strict=True)
    # Only domain implementations, never the legacy agent/service/orchestrator.
    modules = ['__init__.py', 'tools.py', 'config.py', 'analysis_errors.py',
               'command_execution.py', 'execution_context.py', 'case_archive.py',
               'skill_support.py', 'reporting.py', 'report_templates.py']
    package = destination / 'legacy' / 'src' / 're_agents'
    package.mkdir(parents=True)
    for name in modules:
        shutil.copy2(project / 'src' / 're_agents' / name, package / name)
    for name in ('skills', 'ghidra_scripts', 'rules'):
        source = project / name
        if source.is_dir():
            shutil.copytree(source, destination / 'legacy' / name,
                            ignore=shutil.ignore_patterns('__pycache__', '*.pyc', '.DS_Store'))
    adapter = Path(__file__).resolve().parents[1] / 'src' / 'traceglass' / 'integrations' / 'legacy_mcp.py'
    shutil.copy2(adapter, destination / 'legacy_mcp.py')
    (destination / 'Dockerfile').write_text('''ARG BASE_IMAGE=local/agentscope-re-agents:0.2.1
FROM ${BASE_IMAGE}
USER root
ENTRYPOINT []
CMD ["sleep", "infinity"]
# Old image may contain model configuration. It must not reach analysis agents.
RUN rm -rf /app/config /app/.env /app/src/re_agents /app/prompts /app/output
COPY legacy /opt/traceglass/legacy
COPY legacy_mcp.py /opt/traceglass/legacy_mcp.py
ENV PYTHONPATH=/opt/traceglass/legacy/src \\
    TRACEGLASS_LEGACY_ROOT=/opt/traceglass/legacy \\
    TRACEGLASS_WORKDIR=/workspace \\
    GHIDRA_SCRIPT_ROOT=/opt/traceglass/legacy/ghidra_scripts
WORKDIR /workspace
''', encoding='utf-8')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--legacy-project', required=True, type=Path)
    parser.add_argument('--base-image', default='local/agentscope-re-agents:0.2.1')
    parser.add_argument('--tag', default='local/traceglass-analysis:latest')
    args = parser.parse_args()
    platform = subprocess.run(['docker', 'image', 'inspect', args.base_image,
                               '--format', '{{.Os}}/{{.Architecture}}'],
                              check=True, capture_output=True, text=True).stdout.strip()
    with tempfile.TemporaryDirectory(prefix='traceglass-image-') as directory:
        build_context(args.legacy_project, Path(directory))
        subprocess.run(['docker', 'build', '--platform', platform, '--build-arg', f'BASE_IMAGE={args.base_image}',
                        '--tag', args.tag, directory], check=True)


if __name__ == '__main__':
    main()
