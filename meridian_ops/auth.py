"""Bearer-token auth for the Streamable HTTP transport, with read / write scopes per tool.

Two layers:
1. The SDK's bearer middleware (fed by StaticTokenVerifier) rejects a missing, unknown or expired token with 401.
2. ScopeGate looks inside each JSON-RPC POST: a `tools/call` for a tool whose scope (tool_scope_map.json) the token
   lacks is rejected with 403 insufficient_scope before it reaches the server. The tool itself checks again
   (server.require_scope), so the scope rule holds even if the gate is bypassed.

The tokens come from meridian_desk_data/mcp/auth_tokens.json - fake tokens for local testing only.
"""
import json
from datetime import datetime, timezone
from pathlib import Path

from mcp.server.auth.provider import AccessToken


def load_tokens(path):
    """{token: (scopes, expires_at unix seconds or None)}. Tokens without scopes are treated as invalid."""
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    out = {}
    for t in data.get("tokens", []):
        if not t.get("token") or not t.get("scopes"):
            continue
        expires = t.get("expires")
        ts = int(datetime.fromisoformat(expires.replace("Z", "+00:00")).timestamp()) if expires else None
        out[t["token"]] = (list(t["scopes"]), ts)
    return out


class StaticTokenVerifier:
    """TokenVerifier over a fixed token table (the SDK rejects expired tokens using expires_at)."""

    def __init__(self, tokens):
        self.tokens = tokens

    def lookup(self, token):
        entry = self.tokens.get(token)
        if not entry:
            return None
        scopes, expires = entry
        if expires is not None and expires < datetime.now(timezone.utc).timestamp():
            return None
        return scopes

    async def verify_token(self, token):
        entry = self.tokens.get(token)
        if not entry:
            return None
        return AccessToken(token=token, client_id=f"desk-{token[:8]}", scopes=entry[0], expires_at=entry[1])


class ScopeGate:
    """ASGI middleware: 403 for a tools/call the bearer token is not scoped for. Everything else passes through."""

    def __init__(self, app, verifier, scopes):
        self.app, self.verifier, self.scopes = app, verifier, scopes

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http" or scope["method"] != "POST":
            return await self.app(scope, receive, send)
        body, more = b"", True
        while more:
            message = await receive()
            body += message.get("body", b"")
            more = message.get("more_body", False)
        token = None
        for key, value in scope.get("headers", []):
            if key.lower() == b"authorization" and value.lower().startswith(b"bearer "):
                token = value[7:].decode("latin-1")
        granted = self.verifier.lookup(token) if token else None
        if granted is not None:  # invalid / missing tokens are left to the SDK's 401
            for tool in _called_tools(body):
                needed = self.scopes.get(tool)
                if needed and needed not in granted:
                    return await _forbidden(send, tool, needed)

        sent = False

        async def replay():
            nonlocal sent
            if not sent:
                sent = True
                return {"type": "http.request", "body": body, "more_body": False}
            return await receive()

        return await self.app(scope, replay, send)


def _called_tools(body):
    try:
        data = json.loads(body or b"null")
    except ValueError:
        return []
    messages = data if isinstance(data, list) else [data]
    return [m.get("params", {}).get("name") for m in messages
            if isinstance(m, dict) and m.get("method") == "tools/call" and isinstance(m.get("params"), dict)]


async def _forbidden(send, tool, needed):
    payload = json.dumps({"error": "insufficient_scope",
                          "error_description": f"{tool} requires the {needed} scope"}).encode()
    await send({"type": "http.response.start", "status": 403, "headers": [
        (b"content-type", b"application/json"),
        (b"www-authenticate", f'Bearer error="insufficient_scope", scope="{needed}"'.encode())]})
    await send({"type": "http.response.body", "body": payload})
