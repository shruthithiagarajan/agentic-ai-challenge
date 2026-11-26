"""Challenge 8 (MCP) - offline: in-process MCP servers, a temp copy of orders.db, fake LLM; one real local HTTP server."""
import contextlib, io, json, os, shutil, socket, tempfile, threading, time, unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

import anyio

from desk import agent, cli
from desk.mcp_client import MCPConfigError, MCPToolbox, ServerConfig, convert_result, load_config
from desk.tools import TOOLS, Toolbox, mcp_tool_specs
from meridian_ops import build_server
from meridian_ops.auth import ScopeGate, StaticTokenVerifier, load_tokens
from meridian_ops.server import scope_map
from meridian_ops.stub import build_stub
from tests.test_ch06 import FakeClient, call, reply

DATA = Path("meridian_desk_data")
READ, RW = "tok_read_7f3a91c2", "tok_rw_b21d04e8"


class ServerMixin:
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        shutil.copy(DATA / "orders.db", self.tmp / "orders.db")
        self.outbox = self.tmp / "outbox"
        self.srv = build_server(db_path=self.tmp / "orders.db", outbox=self.outbox, retrieve=lambda q: [
            SimpleNamespace(id="shipping_delays.md#3", text="6 to 10 days: 3% credit", note="",
                            meta={"title": "Shipping Delays", "heading": "3. Compensation", "effective_date": "2025-09-01"})])

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def client(self, coro_fn):
        from mcp import Client

        async def main():
            async with Client(self.srv) as c:
                return await coro_fn(c)

        return anyio.run(main)

    def toolbox(self, servers=None, plan=False):
        """MCPToolbox over in-process servers: {name: MCPServer}."""
        from mcp import Client

        servers = servers or {"meridian-ops": self.srv}
        return MCPToolbox([ServerConfig(name=n) for n in servers], plan=plan,
                          client_factory=lambda cfg: Client(servers[cfg.name]))


# ---- the server ---------------------------------------------------------------------

class ServerTests(ServerMixin, unittest.TestCase):
    def test_tools_publish_desks_schemas_annotations_and_structured_output(self):
        tools = self.client(lambda c: c.list_tools())
        by = {t.name: t for t in tools.tools}
        self.assertEqual(list(by), [s["function"]["name"] for s in mcp_tool_specs()])
        self.assertEqual(len(by), 11)
        self.assertEqual(by["get_order"].input_schema, TOOLS[0]["function"]["parameters"])  # hand-written schema
        self.assertEqual(by["get_order"].description, TOOLS[0]["function"]["description"])
        self.assertIs(by["get_order"].annotations.read_only_hint, True)
        self.assertIs(by["cancel_order"].annotations.destructive_hint, True)
        self.assertIs(by["create_refund"].annotations.idempotent_hint, True)
        self.assertIs(by["draft_email"].annotations.read_only_hint, False)
        self.assertEqual(by["get_order"].output_schema["type"], "object")

    def test_calls_return_structured_content_and_desk_errors(self):
        async def go(c):
            return (await c.call_tool("get_order", {"order_id": "MS-10422"}),
                    await c.call_tool("get_order", {"order_id": "MS-99999"}),
                    await c.call_tool("check_stock", {"sku": "nope"}))
        ok, missing, bad = self.client(go)
        self.assertEqual(convert_result(ok)["result"]["status"], "shipped")
        self.assertEqual(convert_result(missing)["error"]["code"], "order_not_found")
        self.assertEqual(convert_result(bad)["error"]["code"], "invalid_arguments")

    def test_write_tools_change_only_the_working_copy(self):
        async def go(c):
            return [await c.call_tool("cancel_order", {"order_id": "MS-10488", "reason": "customer asked"}),
                    await c.call_tool("cancel_order", {"order_id": "MS-10422", "reason": "too late"}),
                    await c.call_tool("create_refund", {"order_id": "MS-10388", "amount_inr": 1347, "reason": "delay",
                                                        "idempotency_key": "ref-MS-10388-001"}),
                    await c.call_tool("create_refund", {"order_id": "MS-10388", "amount_inr": 1347, "reason": "delay",
                                                        "idempotency_key": "ref-MS-10388-001"}),
                    await c.call_tool("create_refund", {"order_id": "MS-10388", "amount_inr": 999999, "reason": "too big",
                                                        "idempotency_key": "ref-MS-10388-002"})]
        cancel, shipped, refund, again, too_big = [convert_result(r) for r in self.client(go)]
        self.assertEqual(cancel["result"]["previous_status"], "processing")
        self.assertEqual(shipped["error"]["code"], "not_cancellable")
        self.assertIs(refund["result"]["duplicate"], False)
        self.assertIs(again["result"]["duplicate"], True)  # idempotency key: no second refund
        self.assertEqual(too_big["error"]["code"], "amount_too_large")
        seed = Toolbox(DATA / "orders.db", DATA / "catalog.csv")
        self.assertEqual(seed.run("get_order", '{"order_id": "MS-10488"}')["result"]["status"], "processing")
        self.assertEqual(seed.run("cancel_order", '{"order_id": "MS-10488", "reason": "x x"}')["error"]["code"], "read_only")

    def test_policy_resources_and_delay_apology_prompt(self):
        async def go(c):
            return (await c.list_resources(), await c.read_resource("policy://shipping_delays.md"),
                    await c.list_prompts(), await c.get_prompt("delay-apology", {"order_id": "MS-10388"}))
        resources, doc, prompts, prompt = self.client(go)
        self.assertEqual(len(resources.resources), 14)
        self.assertIn("Shipping Delays and Compensation", doc.contents[0].text)
        self.assertEqual([(a.name, a.required) for a in prompts.prompts[0].arguments],
                         [("order_id", True), ("customer_tone", False)])
        self.assertIn("Draft an apology email for order MS-10388", prompt.messages[0].content.text)
        self.assertIn("Tone: warm and professional", prompt.messages[0].content.text)

    def test_tool_checks_token_scope_itself(self):
        from mcp.server.auth.middleware.auth_context import auth_context_var
        from mcp.server.auth.middleware.bearer_auth import AuthenticatedUser
        from mcp.server.auth.provider import AccessToken

        user = AuthenticatedUser(AccessToken(token=READ, client_id="t", scopes=["orders:read"]))
        token = auth_context_var.set(user)
        try:
            r = self.client(lambda c: c.call_tool("cancel_order", {"order_id": "MS-10488", "reason": "x x x"}))
        finally:
            auth_context_var.reset(token)
        self.assertEqual(convert_result(r)["error"]["code"], "insufficient_scope")

    def test_log_level_is_configurable(self):
        self.assertEqual(self.srv.settings.log_level, "WARNING")  # quiet by default: tool errors are normal answers
        loud = build_server(db_path=self.tmp / "orders.db", outbox=self.outbox, retrieve=lambda q: [], log_level="INFO")
        self.assertEqual(loud.settings.log_level, "INFO")

    def test_stub_server_from_manifest(self):
        stub = build_stub(DATA / "mcp" / "fake_servers" / "carrier_tracking.json")

        async def go():
            from mcp import Client
            async with Client(stub) as c:
                return await c.list_tools(), await c.call_tool("track_shipment", {"awb": "DL566614170"})
        tools, result = anyio.run(go)
        self.assertIn("track_shipment", [t.name for t in tools.tools])
        self.assertIn("canned response", convert_result(result)["result"])


# ---- auth ----------------------------------------------------------------------------

class AuthTests(unittest.TestCase):
    def test_token_table(self):
        tokens = load_tokens(DATA / "mcp" / "auth_tokens.json")
        self.assertEqual(set(tokens), {READ, RW, "tok_expired_0c9e55aa"})  # no-scope / null tokens dropped
        v = StaticTokenVerifier(tokens)
        self.assertIsNone(v.lookup("tok_expired_0c9e55aa"))
        self.assertIsNone(v.lookup("nope"))
        self.assertIn("orders:write", v.lookup(RW))
        access = anyio.run(v.verify_token, READ)
        self.assertEqual(access.scopes, ["orders:read", "catalog:read", "policies:read"])
        self.assertIsNone(anyio.run(v.verify_token, "tok_invalid_zzz"))

    def gate(self, token, method="tools/call", tool="create_refund"):
        seen, sent = [], []
        body = json.dumps({"jsonrpc": "2.0", "id": 1, "method": method, "params": {"name": tool, "arguments": {}}}).encode()

        async def inner(scope, receive, send):
            seen.append((await receive())["body"])

        async def receive():
            return {"type": "http.request", "body": body, "more_body": False}

        async def send(message):
            sent.append(message)

        headers = [(b"authorization", f"Bearer {token}".encode())] if token else []
        gate = ScopeGate(inner, StaticTokenVerifier(load_tokens(DATA / "mcp" / "auth_tokens.json")), scope_map(DATA))
        anyio.run(gate, {"type": "http", "method": "POST", "headers": headers}, receive, send)
        return seen, sent

    def test_scope_gate(self):
        seen, sent = self.gate(READ)
        self.assertEqual((seen, sent[0]["status"]), ([], 403))
        self.assertIn(b"orders:write", sent[1]["body"])
        self.assertIn((b"www-authenticate", b'Bearer error="insufficient_scope", scope="orders:write"'), sent[0]["headers"])
        self.assertEqual(len(self.gate(RW)[0]), 1)  # allowed: body replayed to the server
        self.assertEqual(len(self.gate(READ, tool="check_stock")[0]), 1)
        self.assertEqual(len(self.gate(READ, method="tools/list")[0]), 1)
        self.assertEqual(len(self.gate(None)[0]), 1)  # no token: the SDK answers 401

    def test_http_end_to_end_401_403_and_scoped_calls(self):
        import httpx2
        import uvicorn

        from meridian_ops.__main__ import http_app

        import warnings
        warnings.simplefilter("ignore", ResourceWarning)  # Windows proactor sockets closed by the server thread
        tmp = Path(tempfile.mkdtemp())
        shutil.copy(DATA / "orders.db", tmp / "orders.db")
        with socket.socket() as s:
            s.bind(("127.0.0.1", 0))
            port = s.getsockname()[1]
        app, _ = http_app("127.0.0.1", port, db_path=tmp / "orders.db", outbox=tmp / "outbox", retrieve=lambda q: [])
        server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="error"))
        thread = threading.Thread(target=server.run, daemon=True)
        thread.start()
        try:
            for _ in range(50):
                if server.started:
                    break
                time.sleep(0.1)
            url = f"http://127.0.0.1:{port}/mcp"
            body = {"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}}
            hdr = {"Accept": "application/json, text/event-stream"}
            self.assertEqual(httpx2.post(url, json=body, headers=hdr).status_code, 401)
            for bad in ("tok_invalid_zzz", "tok_expired_0c9e55aa"):
                self.assertEqual(httpx2.post(url, json=body, headers={**hdr, "Authorization": f"Bearer {bad}"}).status_code, 401)
            cfg = ServerConfig(name="http", transport="streamable-http", url=url, auth="bearer", token_env="T_TOKEN")
            refund = json.dumps({"order_id": "MS-10388", "amount_inr": 5, "reason": "test", "idempotency_key": "ref-http-001"})
            with mock.patch.dict(os.environ, {"T_TOKEN": READ}), MCPToolbox([cfg]) as tb:
                self.assertEqual(len(tb.routes), 11)
                self.assertTrue(tb.run("check_stock", '{"sku": "MS-VLV0200"}')["ok"])
                denied = tb.run("create_refund", refund)
            self.assertEqual(denied["error"]["code"], "insufficient_scope")
            self.assertIn("HTTP 403", denied["error"]["message"])
            with mock.patch.dict(os.environ, {"T_TOKEN": RW}), MCPToolbox([cfg]) as tb:
                self.assertTrue(tb.run("create_refund", refund)["ok"])
            with mock.patch.dict(os.environ, {"T_TOKEN": "tok_invalid_zzz"}), MCPToolbox([cfg]) as tb:
                self.assertIn("HTTP 401", tb.failed["http"])
        finally:
            server.should_exit = True
            thread.join(5)
            shutil.rmtree(tmp, ignore_errors=True)


class LoadBalancerTests(unittest.TestCase):
    def test_round_robin_and_auth_header_passes_through(self):
        import httpx2
        from starlette.testclient import TestClient

        from meridian_ops.lb import build_lb

        seen = []

        def backend(request):
            seen.append((str(request.url), request.headers.get("authorization")))
            return httpx2.Response(200, json={"jsonrpc": "2.0", "id": 1, "result": {}})

        app = build_lb(["http://a:1", "http://b:2"], client=httpx2.AsyncClient(transport=httpx2.MockTransport(backend)))
        with TestClient(app) as tc:
            hit = [tc.post("/mcp", json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"},
                           headers={"Authorization": f"Bearer {READ}"}).headers["x-meridian-backend"] for _ in range(4)]
        self.assertEqual(hit, ["http://a:1", "http://b:2", "http://a:1", "http://b:2"])
        self.assertEqual(seen[0], ("http://a:1/mcp", f"Bearer {READ}"))
        self.assertEqual(app.state.counts, {"http://a:1": 2, "http://b:2": 2})


# ---- the client side -------------------------------------------------------------------

class ClientTests(ServerMixin, unittest.TestCase):
    def test_discovery_specs_today_and_instructions(self):
        with self.toolbox() as tb:
            names = [s["function"]["name"] for s in tb.specs()]
            self.assertEqual(len(names), 11)
            self.assertEqual(tb.today(), "2026-10-05")  # from the server's instructions
            self.assertIn("meridian-ops: Meridian Supply Co.", tb.instructions())
            self.assertEqual(tb.summary(), ["meridian-ops (stdio): 11 tools, 14 resources, 1 prompts"])
            self.assertGreater(tb.definition_tokens(), 1000)
            self.assertIn("Draft an apology email for order MS-10401", tb.get_prompt("delay-apology", {"order_id": "MS-10401"}))

    def test_run_routes_calls_and_never_raises(self):
        with self.toolbox() as tb:
            self.assertEqual(tb.run("check_stock", '{"sku": "MS-VLV0200"}')["result"]["stock"], 640)
            self.assertEqual(tb.run("get_order", '{"order_id": "MS-99999"}')["error"]["code"], "order_not_found")
            self.assertEqual(tb.run("teleport", "{}")["error"]["code"], "unknown_tool")
            self.assertEqual(tb.run("get_order", "{oops")["error"]["code"], "invalid_json")
            saved = tb.run("draft_email", json.dumps({"to": "a@b.example", "subject": "Hi", "body": "x"}))
            self.assertEqual(tb.drafts, [saved["result"]["saved"]])
            self.assertTrue(Path(saved["result"]["saved"]).is_file())

    def test_name_clash_gets_server_prefix_and_dead_server_is_skipped(self):
        from mcp import Client

        servers = {"a": self.srv, "b": build_server(db_path=self.tmp / "orders.db", outbox=self.outbox,
                                                     retrieve=lambda q: [])}

        def factory(cfg):
            if cfg.name == "dead":
                raise ConnectionError("nobody home")
            return Client(servers[cfg.name])

        with MCPToolbox([ServerConfig(name="a"), ServerConfig(name="dead"), ServerConfig(name="b")],
                        client_factory=factory) as tb:
            self.assertIn("a__get_order", tb.routes)
            self.assertIn("b__get_order", tb.routes)
            self.assertNotIn("get_order", tb.routes)
            self.assertIn("ConnectionError: nobody home", tb.failed["dead"])
            self.assertTrue(tb.run("b__check_stock", '{"sku": "MS-VLV0200"}')["ok"])

    def test_plan_tool_is_local(self):
        with self.toolbox(plan=True) as tb:
            self.assertEqual(tb.specs()[-1]["function"]["name"], "todo")
            r = tb.run("todo", json.dumps({"items": [{"step": "a", "status": "done"}]}))
            self.assertEqual((r["result"]["done"], tb.plan), (1, [{"step": "a", "status": "done"}]))

    def test_load_config(self):
        cfgs = load_config("mcp_servers.json")
        self.assertEqual([c.name for c in cfgs], ["meridian-ops"])  # only the enabled one
        self.assertEqual([c.name for c in load_config("mcp_servers.json", only=["filesystem", "fetch"])],
                         ["filesystem", "fetch"])
        with self.assertRaises(MCPConfigError):
            load_config("mcp_servers.json", only=["nope"])
        with self.assertRaises(MCPConfigError):
            load_config("missing.json")


# ---- the agent on MCP -------------------------------------------------------------------

class AgentOnMCPTests(ServerMixin, unittest.TestCase):
    def test_agent_module_has_no_hard_coded_tools(self):
        source = Path(agent.__file__).read_text(encoding="utf-8")
        self.assertNotIn("from .tools", source)
        self.assertNotIn("agent_tool_specs", source)
        self.assertIn("toolbox.specs(plan)", source)

    def test_loop_uses_discovered_tools(self):
        model = FakeClient(reply(calls=[call("a", "search_orders", customer="Apex", min_days_late=6),
                                        call("b", "check_stock", sku="MS-VLV0300")]),
                           reply("2 orders found; MS-VLV0300 is out of stock."))
        with self.toolbox() as tb:
            run = agent.run_agent("Apex check", "m", model, tb, sleep=lambda s: None, tool_log=None, trace_dir=None)
        self.assertEqual((run.stop_reason, run.tool_calls), ("done", 2))
        sent = model.requests[0]
        self.assertEqual(len(sent["tools"]), 11)
        self.assertIn("Today is 2026-10-05", sent["messages"][0]["content"])
        self.assertIn("Connected tool servers:", sent["messages"][0]["content"])
        results = [json.loads(m["content"]) for m in model.requests[1]["messages"] if m["role"] == "tool"]
        self.assertEqual(results[0]["result"]["customer"]["id"], "CUST-0001")
        self.assertIs(results[1]["result"]["in_stock"], False)

    def run_cli(self, argv, model=None):
        from mcp import Client

        out, err = io.StringIO(), io.StringIO()
        env = {"DESK_TOOL_LOG": "off", "DESK_TRACE_DIR": "off", "DESK_MODEL_AGENT": "agent-model"}
        with mock.patch.dict(os.environ, env), contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            args = cli.build_parser().parse_args(argv)
            factory = lambda cfg: Client(self.srv)  # noqa: E731
            if args.cmd == "mcp":
                code = cli.cmd_mcp(args, mcp_factory=factory)
            else:
                code = cli.cmd_agent(args, client=model, sleep=lambda s: None, mcp_factory=factory)
        return code, out.getvalue(), err.getvalue()

    def test_cli_agent_prompt_and_mcp_commands(self):
        model = FakeClient(reply(calls=[call("a", "get_order", order_id="MS-10388")]), reply("Drafted."))
        code, out, err = self.run_cli(["agent", "--prompt", "delay-apology", "--arg", "order_id=MS-10388"], model)
        self.assertEqual(code, 0)
        self.assertIn("[mcp] meridian-ops (stdio): 11 tools, 14 resources, 1 prompts", err)
        self.assertIn("[agent] task: Draft an apology email for order MS-10388", err)
        self.assertIn("tools: MCP", out)
        code, out, err = self.run_cli(["mcp", "list"])
        self.assertIn("tool     cancel_order  [destructive, idempotent]", out)
        self.assertIn("resource policy://returns.md", out)
        self.assertIn("prompt   delay-apology(order_id, customer_tone)", out)
        self.assertIn("total: 11 tools from 1 server(s)", out)
        code, out, err = self.run_cli(["mcp", "call", "check_stock", '{"sku": "MS-VLV0300"}'])
        self.assertEqual((code, json.loads(out)["result"]["stock"]), (0, 0))
        self.assertEqual(self.run_cli(["agent", "x", "--prompt", "p", "--arg", "novalue"])[0], 2)
        code, out, err = self.run_cli(["agent", "x", "--servers", "missing.json"])
        self.assertEqual(code, 1)
        self.assertIn("--local", err)


if __name__ == "__main__":
    unittest.main()
