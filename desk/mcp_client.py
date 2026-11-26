"""Challenge 8: Desk's agent as an MCP client - its tools come from servers in a config file, not from code.

MCPToolbox connects to every server in mcp_servers.json (stdio subprocesses and Streamable HTTP endpoints),
discovers their tools with tools/list, and offers them to the Challenge 7 loop through the same small interface
the local Toolbox had: specs(), run(name, arguments) -> {"ok", "result" | "error"}, today(), instructions().
The async MCP clients live on a background event loop (an anyio blocking portal), so the agent loop stays
synchronous. Tool errors, HTTP 401/403 and dead servers all come back as structured results, never exceptions.
"""
import json
import os
import re
import shutil
import sys
from contextlib import ExitStack
from dataclasses import dataclass, field
from datetime import date
from functools import partial
from pathlib import Path

DEFAULT_CONFIG = Path("mcp_servers.json")
LOG_DIR = Path("data") / "mcp_logs"
TOOL_NAME = re.compile(r"[^A-Za-z0-9_-]")


class MCPConfigError(Exception):
    pass


@dataclass
class ServerConfig:
    name: str
    transport: str = "stdio"  # stdio | streamable-http
    command: str = ""
    args: list = field(default_factory=list)
    env: dict = None
    cwd: str = None
    url: str = ""
    auth: str = None  # "bearer" -> Authorization: Bearer $token_env
    token_env: str = "MERIDIAN_TOKEN"
    enabled: bool = True
    note: str = ""


def load_config(path=DEFAULT_CONFIG, only=None):
    """Enabled servers from the config file; `only` picks servers by name (enabled or not)."""
    path = Path(path)
    if not path.is_file():
        raise MCPConfigError(f"MCP server config {path} not found")
    try:
        entries = json.loads(path.read_text(encoding="utf-8"))["servers"]
    except (ValueError, KeyError, TypeError) as e:
        raise MCPConfigError(f"{path}: expected {{\"servers\": [...]}} ({e})") from None
    known = {s.get("name") for s in entries}
    missing = [n for n in only or [] if n not in known]
    if missing:
        raise MCPConfigError(f"no server named {', '.join(missing)} in {path} (have: {', '.join(sorted(known))})")
    fields = set(ServerConfig.__dataclass_fields__)
    configs = [ServerConfig(**{k: v for k, v in s.items() if k in fields}) for s in entries]
    return [c for c in configs if (c.name in only if only else c.enabled)]


class MCPToolbox:
    def __init__(self, configs, plan=False, log=lambda m: None, timeout=60.0, client_factory=None):
        self.configs, self.log, self.timeout = configs, log, timeout
        self.client_factory = client_factory or self._client  # tests pass in-process servers here
        self.servers = {}  # name -> {"client", "tools", "resources", "prompts", "instructions", "transport"}
        self.failed = {}  # name -> why it could not connect
        self.routes = {}  # tool name shown to the model -> (server, tool name on that server)
        self.drafts, self.plan, self.use_plan = [], [], plan
        self._http_errors = {}  # server -> (status, body) of its last HTTP error response
        self._stack, self._portal = ExitStack(), None

    # -- connecting --------------------------------------------------------------------

    def __enter__(self):
        import anyio.from_thread

        self._portal = self._stack.enter_context(anyio.from_thread.start_blocking_portal())
        for cfg in self.configs:
            try:
                client = self._stack.enter_context(self._portal.wrap_async_context_manager(self.client_factory(cfg)))
                self.servers[cfg.name] = self._portal.call(self._discover, client, cfg.transport)
            except Exception as e:  # noqa: BLE001 - one dead server must not stop the others
                self.failed[cfg.name] = _describe(e, self._http_errors.pop(cfg.name, None))
                self.log(f"[mcp] could not connect to {cfg.name}: {self.failed[cfg.name]}")
        self._route()
        return self

    def __exit__(self, *exc):
        try:
            self._stack.close()
        except Exception as e:  # noqa: BLE001 - shutting down subprocesses must not hide the run's result
            self.log(f"[mcp] shutdown warning: {type(e).__name__}: {e}")
        return False

    def _client(self, cfg):
        from mcp import Client

        if cfg.transport == "stdio":
            from mcp.client.stdio import StdioServerParameters, stdio_client

            command = sys.executable if cfg.command in ("python", "python3") else (shutil.which(cfg.command) or cfg.command)
            env = {k: os.path.expandvars(str(v)) for k, v in (cfg.env or {}).items()} or None
            LOG_DIR.mkdir(parents=True, exist_ok=True)
            errlog = self._stack.enter_context(open(LOG_DIR / f"{cfg.name}.log", "a", encoding="utf-8"))
            params = StdioServerParameters(command=command, args=[str(a) for a in cfg.args], env=env, cwd=cfg.cwd)
            return Client(stdio_client(params, errlog=errlog))
        if cfg.transport == "streamable-http":
            from mcp.client.streamable_http import streamable_http_client
            from mcp.shared._httpx_utils import create_mcp_http_client

            headers = {}
            if cfg.auth == "bearer":
                token = os.getenv(cfg.token_env or "MERIDIAN_TOKEN")
                if not token:
                    raise MCPConfigError(f"{cfg.name} needs a bearer token: set {cfg.token_env}")
                headers["Authorization"] = f"Bearer {token}"
            http = create_mcp_http_client(headers=headers)

            async def remember_errors(response, server=cfg.name):
                if response.status_code >= 400:
                    await response.aread()
                    self._http_errors[server] = (response.status_code, response.text[:300])

            http.event_hooks["response"].append(remember_errors)
            return Client(streamable_http_client(cfg.url, http_client=http))
        raise MCPConfigError(f"{cfg.name}: unknown transport {cfg.transport!r} (use stdio or streamable-http)")

    async def _discover(self, client, transport):
        tools, cursor = [], None
        while True:
            page = await client.list_tools(cursor=cursor)
            tools += page.tools
            cursor = page.next_cursor
            if not cursor:
                break
        info = {"client": client, "tools": tools, "transport": transport, "instructions": client.instructions or "",
                "resources": [], "prompts": []}
        for kind, call in (("resources", client.list_resources), ("prompts", client.list_prompts)):
            try:
                info[kind] = getattr(await call(), kind)
            except Exception:  # noqa: BLE001 - servers may not offer resources or prompts
                pass
        return info

    def _route(self):
        """Bare tool names unless two servers share one; then server__tool for both."""
        counts = {}
        for server in self.servers.values():
            for t in server["tools"]:
                counts[t.name] = counts.get(t.name, 0) + 1
        self.routes = {}
        for name, server in self.servers.items():
            for t in server["tools"]:
                shown = t.name if counts[t.name] == 1 else f"{name}__{t.name}"
                self.routes[TOOL_NAME.sub("_", shown)[:64]] = (name, t.name)

    # -- what the agent loop uses ----------------------------------------------------------

    def specs(self, plan=None):
        out = []
        for shown, (server, real) in self.routes.items():
            tool = next(t for t in self.servers[server]["tools"] if t.name == real)
            schema = {k: v for k, v in (tool.input_schema or {"type": "object"}).items() if k != "$schema"}
            schema.setdefault("type", "object")
            out.append({"type": "function", "function": {"name": shown, "description": tool.description or tool.name,
                                                         "parameters": schema}})
        if self.use_plan if plan is None else plan:
            from .tools import TODO_TOOL  # the agent's own checklist, not a business tool

            out.append(json.loads(json.dumps(TODO_TOOL)))
        return out

    def instructions(self):
        return "\n".join(f"- {name}: {s['instructions']}" for name, s in self.servers.items() if s["instructions"])

    def today(self):
        found = re.search(r"as of (\d{4}-\d{2}-\d{2})", self.instructions())
        return found.group(1) if found else date.today().isoformat()

    def run(self, name, arguments):
        if name == "todo" and self.use_plan:
            try:
                items = json.loads(arguments or "{}").get("items") or []
            except (ValueError, AttributeError):
                items = []
            self.plan = items
            return {"ok": True, "result": {"plan": items, "done": sum(i.get("status") == "done" for i in items
                                                                       if isinstance(i, dict)), "total": len(items)}}
        if name not in self.routes:
            return _error("unknown_tool", f"there is no tool named {name!r}")
        server, real = self.routes[name]
        try:
            args = json.loads(arguments) if isinstance(arguments, str) and arguments.strip() else (arguments or {})
        except ValueError:
            return _error("invalid_json", f"arguments are not valid JSON: {str(arguments)[:120]!r}")
        if not isinstance(args, dict):
            return _error("invalid_arguments", "arguments must be a JSON object")
        client = self.servers[server]["client"]
        try:
            result = self._portal.call(partial(client.call_tool, real, args, read_timeout_seconds=self.timeout))
        except Exception as e:  # noqa: BLE001 - transport / auth failures are data for the model
            http = self._http_errors.pop(server, None)
            code = _http_code(http) or "call_failed"
            return _error(code, _describe(e, http))
        out = convert_result(result)
        if out["ok"] and real == "draft_email" and isinstance(out["result"], dict) and out["result"].get("saved"):
            self.drafts.append(out["result"]["saved"])
        return out

    def get_prompt(self, name, arguments=None):
        """Render a server prompt (e.g. delay-apology) to text, from whichever server offers it."""
        for server in self.servers.values():
            if any(p.name == name for p in server["prompts"]):
                result = self._portal.call(server["client"].get_prompt, name, arguments or {})
                return "\n\n".join(getattr(m.content, "text", "") for m in result.messages)
        raise MCPConfigError(f"no connected server offers a prompt named {name!r}")

    def summary(self):
        """One line per server, for the CLI and the trace."""
        lines = [f"{name} ({s['transport']}): {len(s['tools'])} tools, {len(s['resources'])} resources, "
                 f"{len(s['prompts'])} prompts" for name, s in self.servers.items()]
        lines += [f"{name}: NOT CONNECTED - {why}" for name, why in self.failed.items()]
        return lines

    def definition_tokens(self, plan=None):
        """Rough cost of the tool definitions sent on EVERY model call (~4 chars per token)."""
        return len(json.dumps(self.specs(plan))) // 4


def convert_result(result):
    """MCP CallToolResult -> Desk's {"ok": ..., "result" | "error"}."""
    text = "\n".join(getattr(c, "text", "") for c in (result.content or []) if getattr(c, "type", "") == "text")
    if result.is_error:
        start = text.find("{")
        try:
            err = json.loads(text[start:]) if start >= 0 else None
        except ValueError:
            err = None
        if isinstance(err, dict) and "code" in err:
            return {"ok": False, "error": {"code": err["code"], "message": err.get("message", "")}}
        return _error("tool_error", text[:500] or "the tool reported an error")
    if result.structured_content is not None:
        return {"ok": True, "result": result.structured_content}
    try:
        return {"ok": True, "result": json.loads(text)}
    except ValueError:
        return {"ok": True, "result": text}


def _http_code(http):
    if not http:
        return None
    status, body = http
    try:
        return json.loads(body).get("error") or f"http_{status}"
    except (ValueError, AttributeError):
        return f"http_{status}"


def _describe(e, http=None):
    if http:
        status, body = http
        return f"HTTP {status}: {body}"
    inner = getattr(e, "exceptions", None)
    if inner:  # anyio / asyncio exception groups
        return _describe(inner[0])
    return f"{type(e).__name__}: {e}"


def _error(code, message):
    return {"ok": False, "error": {"code": code, "message": message}}
