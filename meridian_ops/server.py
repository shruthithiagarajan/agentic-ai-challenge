"""The meridian-ops MCP server: tools, resources and one prompt over the business logic in desk.tools.

Tools     11 (get_order, search_orders, search_catalog, check_stock, calculate, search_policies, list_policies,
          read_policy, draft_email, cancel_order, create_refund), published with Desk's hand-written JSON schemas and
          descriptions, annotations (read-only / destructive / idempotent) and structured output.
Resources one per policy document: policy://returns.md, policy://shipping_delays.md, ...
Prompt    delay-apology(order_id, customer_tone) from meridian_desk_data/mcp/delay_apology_prompt.json

Write tools run against a working copy of orders.db (data/meridian_ops/orders.db), never the seed database. Over
HTTP every tool checks the caller's token scopes (tool_scope_map.json); over stdio the client is the local user.
"""
import inspect
import json
import os
import shutil
from pathlib import Path
from typing import Any

from mcp.server.auth.middleware.auth_context import get_access_token
from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp_types import ToolAnnotations

from desk.tools import WRITE_TOOLS, Toolbox, mcp_tool_specs

ROOT = Path(__file__).resolve().parent.parent
WORK_DB = ROOT / "data" / "meridian_ops" / "orders.db"
EXTRA_SCOPES = {"calculate": None, "search_policies": "policies:read"}  # tools not in tool_scope_map.json


def data_dir():
    return Path(os.getenv("DESK_DATA_DIR") or ROOT / "meridian_desk_data")


def scope_map(root=None):
    """{tool: required scope or None} from meridian_desk_data/mcp/tool_scope_map.json (+ calculate, search_policies)."""
    path = Path(root or data_dir()) / "mcp" / "tool_scope_map.json"
    scopes = dict(EXTRA_SCOPES)
    if path.is_file():
        scopes.update(json.loads(path.read_text(encoding="utf-8")))
    return scopes


def working_copy(seed, work=WORK_DB):
    """Copy the seed orders.db once; every later run (and every write) uses the copy."""
    work = Path(work)
    if not work.is_file():
        work.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(seed, work)
    return work


def annotations_for(name):
    if name in WRITE_TOOLS:
        return ToolAnnotations(readOnlyHint=False, destructiveHint=name == "cancel_order", idempotentHint=True,
                               openWorldHint=False)
    if name == "draft_email":
        return ToolAnnotations(readOnlyHint=False, destructiveHint=False, idempotentHint=False, openWorldHint=False)
    return ToolAnnotations(readOnlyHint=True, openWorldHint=False)


def require_scope(tool, scope):
    """Per-tool authorisation. No token means stdio / in-process (the local user), which may call everything."""
    token = get_access_token()
    if token is not None and scope and scope not in token.scopes:
        raise ToolError(json.dumps({"code": "insufficient_scope", "message": f"{tool} requires the {scope} scope"}))


def _register(srv, toolbox, spec, scope):
    fn = spec["function"]
    name, params = fn["name"], fn["parameters"]

    def call(**kwargs):
        require_scope(name, scope)
        result = toolbox.run(name, kwargs)
        if result["ok"]:
            return result["result"]
        raise ToolError(json.dumps(result["error"]))  # -> isError result carrying Desk's {code, message}

    required = set(params.get("required", []))
    call.__signature__ = inspect.Signature(
        [inspect.Parameter(p, inspect.Parameter.KEYWORD_ONLY, annotation=Any,
                           default=inspect.Parameter.empty if p in required else None)
         for p in params.get("properties", {})],
        return_annotation=dict[str, Any])
    call.__name__ = name
    srv.add_tool(call, name=name, title=name.replace("_", " ").capitalize(), description=fn["description"],
                 annotations=annotations_for(name), structured_output=True)
    srv._tool_manager.get_tool(name).parameters = params  # publish Desk's hand-written schema, not a derived one


def build_server(root=None, db_path=None, outbox=None, retrieve=None, scopes=None, auth=None, token_verifier=None,
                 name="meridian-ops", log_level="WARNING"):
    root = Path(root or data_dir())
    db = Path(db_path) if db_path else working_copy(root / "orders.db")
    toolbox = Toolbox(db, root / "catalog.csv", retrieve=retrieve, outbox=outbox or root / "outbox",
                      policies_dir=root / "policies", writable=True)
    scopes = scope_map(root) if scopes is None else scopes
    srv = MCPServer(
        name, title="Meridian Supply Co. operations", version="0.8.0", log_level=log_level,
        auth=auth, token_verifier=token_verifier,
        instructions=(f"Meridian Supply Co. customer-operations tools: live orders, catalog, stock, policies, a "
                      f"calculator and email drafts. Order data is as of {toolbox.today()}; treat that date as today. "
                      "Prices are in INR. Write tools (cancel_order, create_refund) change records and need the "
                      "orders:write scope; draft_email only saves drafts and never sends."))
    for spec in mcp_tool_specs():
        _register(srv, toolbox, spec, scopes.get(spec["function"]["name"]))

    def reader(policy_name):
        def read() -> str:
            require_scope("policy resources", "policies:read")
            return toolbox.read_policy(policy_name)["text"]
        return read

    for policy in toolbox.list_policies()["policies"]:
        srv.resource(f"policy://{policy['name']}", name=policy["name"].removesuffix(".md"), title=policy["title"],
                     description=f"{policy['title']} (effective {policy['effective_date'] or 'unknown'})",
                     mime_type="text/markdown")(reader(policy["name"]))

    template = json.loads((root / "mcp" / "delay_apology_prompt.json").read_text(encoding="utf-8"))

    def delay_apology(order_id: str, customer_tone: str = "warm and professional") -> str:
        return template["template"].format(order_id=order_id, customer_tone=customer_tone)

    srv.prompt(name=template["name"], description=template["description"])(delay_apology)
    srv.toolbox = toolbox  # for tests
    return srv
