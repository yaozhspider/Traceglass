"""Real stdio MCP process with per-process state; no Docker required in CI."""
from mcp.server.fastmcp import FastMCP

mcp = FastMCP('rea-state-fixture')
count = 0

@mcp.prompt()
def investigate_feature(feature: str) -> str:
    """Trace a selected feature using this session's observations."""
    return f'Investigate {feature}; counter={count}. Keep unknowns explicit.'

@mcp.tool()
def state_probe() -> int:
    """Increment this server's independent counter."""
    global count
    count += 1
    return count

@mcp.tool()
def evidence_probe() -> dict:
    """Return a synthetic evidence envelope for archive integration tests."""
    evidence = {'evidence_id': 'ev_' + 'a' * 64, 'subject': None,
        'provider': {'id': 'fixture', 'name': 'Fixture', 'version': '1'},
        'operation': 'evidence_probe', 'parameters': {}, 'raw_result': None,
        'normalized_result': {'counter': count}, 'confidence': 'observed',
        'authority': 'controlled-replay', 'limitations': ['test fixture, no actual sample'],
        'locations': [], 'evidence_links': []}
    return {'result': evidence['normalized_result'], 'evidence_id': evidence['evidence_id'], 'evidence': evidence}

if __name__ == '__main__':
    mcp.run()
