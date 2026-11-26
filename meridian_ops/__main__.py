"""python -m meridian_ops [--http] [--host 127.0.0.1] [--port 8080] [--tokens auth_tokens.json]"""
import argparse

from .server import data_dir, scope_map


def http_app(host="127.0.0.1", port=8080, tokens_path=None, **server_kwargs):
    """The stateless Streamable HTTP app with bearer auth (401) and the per-tool scope gate (403)."""
    from mcp.server.auth.settings import AuthSettings

    from .auth import ScopeGate, StaticTokenVerifier, load_tokens
    from .server import build_server

    verifier = StaticTokenVerifier(load_tokens(tokens_path or data_dir() / "mcp" / "auth_tokens.json"))
    base = f"http://{host}:{port}"
    srv = build_server(auth=AuthSettings(issuer_url=base, resource_server_url=f"{base}/mcp", validate_token_resource=False),
                       token_verifier=verifier,
                       **server_kwargs)
    app = srv.streamable_http_app(stateless_http=True, json_response=True, host=host)
    return ScopeGate(app, verifier, scope_map()), srv


def main(argv=None):
    p = argparse.ArgumentParser(prog="python -m meridian_ops", description="meridian-ops MCP server")
    p.add_argument("--http", action="store_true", help="serve Streamable HTTP instead of stdio")
    p.add_argument("--host", default="127.0.0.1")
    p.add_argument("--port", type=int, default=8080)
    p.add_argument("--tokens", help="token table (default meridian_desk_data/mcp/auth_tokens.json)")
    p.add_argument("--log-level", default="WARNING", choices=["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"],
                   type=str.upper, help="server log detail on stderr (default WARNING; INFO also logs each tool error)")
    args = p.parse_args(argv)
    if not args.http:
        from .server import build_server

        build_server(log_level=args.log_level).run("stdio")
        return
    import uvicorn

    app, _ = http_app(args.host, args.port, args.tokens, log_level=args.log_level)
    print(f"meridian-ops: Streamable HTTP on http://{args.host}:{args.port}/mcp (stateless, bearer auth)", flush=True)
    uvicorn.run(app, host=args.host, port=args.port, log_level="warning")


if __name__ == "__main__":
    main()
