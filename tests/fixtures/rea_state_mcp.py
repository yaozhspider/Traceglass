"""Real stdio MCP process with per-process state; no Docker required in CI."""
from mcp.server.fastmcp import FastMCP

mcp = FastMCP('rea-state-fixture')
count = 0

@mcp.tool()
def state_probe() -> int:
    """Increment this server's independent counter."""
    global count
    count += 1
    return count

if __name__ == '__main__':
    mcp.run()
