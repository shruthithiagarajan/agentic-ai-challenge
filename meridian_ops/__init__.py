"""meridian-ops: Meridian Supply Co.'s order, catalog, stock and policy tools as an MCP server (Challenge 8).

    python -m meridian_ops                      # stdio (a client starts it as a subprocess)
    python -m meridian_ops --http --port 8080   # Streamable HTTP, stateless, bearer-token auth with per-tool scopes
"""
from .server import build_server

__all__ = ["build_server"]
