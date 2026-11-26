"""A canned-answer MCP server built from a stub manifest (meridian_desk_data/mcp/fake_servers/*.json).

    python -m meridian_ops.stub meridian_desk_data/mcp/fake_servers/carrier_tracking.json

Used for the Challenge 8 Break it (connect five servers, 50+ tools): each manifest tool is served with its own
inputSchema and answers with fixed text, so the tool definitions cost real tokens without any real backend.
"""
import inspect
import json
import sys
from pathlib import Path
from typing import Any

from mcp.server.mcpserver import MCPServer
from mcp_types import ToolAnnotations


def build_stub(manifest_path):
    manifest = json.loads(Path(manifest_path).read_text(encoding="utf-8"))
    server = manifest.get("server", Path(manifest_path).stem)
    srv = MCPServer(server, instructions=f"{server}: stub server with canned answers (no real backend).",
                    log_level="WARNING")
    for tool in manifest.get("tools", []):
        schema = tool.get("inputSchema") or {"type": "object", "properties": {}}
        name = tool["name"]

        def make(tool_name):
            def call(**kwargs):
                args = {k: v for k, v in kwargs.items() if v is not None}
                return f"[{server} stub] {tool_name} accepted {json.dumps(args)} - canned response, nothing happened."
            return call

        fn = make(name)
        required = set(schema.get("required", []))
        fn.__signature__ = inspect.Signature(
            [inspect.Parameter(p, inspect.Parameter.KEYWORD_ONLY, annotation=Any,
                               default=inspect.Parameter.empty if p in required else None)
             for p in schema.get("properties", {})], return_annotation=str)
        fn.__name__ = name
        srv.add_tool(fn, name=name, description=tool.get("description", name),
                     annotations=ToolAnnotations(openWorldHint=True), structured_output=False)
        srv._tool_manager.get_tool(name).parameters = schema
    return srv


if __name__ == "__main__":
    if len(sys.argv) != 2:
        sys.exit("usage: python -m meridian_ops.stub <manifest.json>")
    build_stub(sys.argv[1]).run("stdio")
