"""Start the existing IDA MCP server without its port-killing launcher."""
import argparse
import os
from pathlib import Path
import socket
import subprocess
import tomllib


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--legacy-project', required=True, type=Path)
    parser.add_argument('--data-dir', type=Path, default=Path('traceglass-data/ida'))
    parser.add_argument('--port', type=int, default=8745)
    parser.add_argument(
        '--host',
        default='0.0.0.0',
        help='Bind address; Docker workspaces need a non-loopback host (default: 0.0.0.0)',
    )
    args = parser.parse_args()
    root = args.legacy_project.resolve() / 'vendor' / 'ida-pro-mcp-multiple'
    config = tomllib.loads((root / 'config.toml').read_text())
    python = root / 'ida-pro-mcp' / '.venv' / 'bin' / 'python'
    if not python.is_file():
        raise SystemExit('The legacy IDA MCP Python environment is not installed')
    with socket.socket() as probe:
        if probe.connect_ex(('127.0.0.1', args.port)) == 0:
            raise SystemExit(f'Port {args.port} already has a listener; no existing process was changed')
    data = args.data_dir.resolve()
    (data / 'tmp').mkdir(parents=True, exist_ok=True)
    (data / 'cache').mkdir(exist_ok=True)
    environment = {k:v for k,v in os.environ.items()
                   if not k.lower().endswith('_proxy') and not k.startswith('LLM_')}
    environment.update(PYTHONDONTWRITEBYTECODE='1', IDADIR=config['ida_dir'], TMPDIR=str(data / 'tmp'))
    raise SystemExit(subprocess.call([
        str(python), '-m', 'ida_pro_mcp.multiserver', '--host', args.host,
        '--port', str(args.port), '--base-session-port', str(config.get('base_session_port',10000)),
        '--max-sessions', str(config.get('max_sessions',10)),
        '--idb-cache-dir', str(data / 'cache'),
    ], cwd=data, env=environment))


if __name__ == '__main__':
    main()
