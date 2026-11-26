"""Stretch: a tiny round-robin load balancer in front of several meridian-ops HTTP instances.

    python -m meridian_ops --http --port 8081
    python -m meridian_ops --http --port 8082
    python -m meridian_ops.lb --port 8080 --backends 8081 8082

Each request goes to the next backend in turn; the response carries X-Meridian-Backend so you can watch it rotate.
This only works because the 2026-07-28 transport is stateless: no session lives on one instance, so any request
can go anywhere. Authorization headers pass straight through, so every backend still checks the token.
"""
import argparse
import itertools

import httpx2
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import Response
from starlette.routing import Route

HOP_BY_HOP = {"connection", "keep-alive", "transfer-encoding", "te", "trailer", "upgrade", "host", "content-length"}


def build_lb(backends, client=None):
    rotation = itertools.cycle(backends)
    counts = {b: 0 for b in backends}
    client = client or httpx2.AsyncClient(timeout=120.0)

    async def forward(request: Request):
        backend = next(rotation)
        counts[backend] += 1
        headers = {k: v for k, v in request.headers.items() if k.lower() not in HOP_BY_HOP}
        upstream = await client.request(request.method, backend.rstrip("/") + request.url.path, headers=headers,
                                        content=await request.body(), params=dict(request.query_params))
        out = {k: v for k, v in upstream.headers.items() if k.lower() not in HOP_BY_HOP | {"content-encoding"}}
        out["X-Meridian-Backend"] = backend
        return Response(upstream.content, status_code=upstream.status_code, headers=out)

    app = Starlette(routes=[Route("/{path:path}", forward, methods=["GET", "POST", "DELETE"])])
    app.state.counts = counts
    return app


def main(argv=None):
    p = argparse.ArgumentParser(prog="python -m meridian_ops.lb")
    p.add_argument("--host", default="127.0.0.1")
    p.add_argument("--port", type=int, default=8080)
    p.add_argument("--backends", nargs="+", required=True, help="backend ports or URLs, e.g. 8081 8082")
    args = p.parse_args(argv)
    backends = [b if b.startswith("http") else f"http://127.0.0.1:{b}" for b in args.backends]
    import uvicorn

    print(f"meridian-ops LB: http://{args.host}:{args.port}/mcp -> {', '.join(backends)} (round robin)", flush=True)
    uvicorn.run(build_lb(backends), host=args.host, port=args.port, log_level="warning")


if __name__ == "__main__":
    main()
