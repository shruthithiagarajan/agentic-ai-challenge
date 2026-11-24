"""Challenge 6 (tool calling) - offline: a small temp orders.db + catalog.csv and fake SDK clients."""
import contextlib, copy, io, json, os, sqlite3, tempfile, unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from desk import cli, llm, tooluse
from desk.llm import LLMError
from desk.rag import Hit
from desk.tools import TOOLS, Toolbox, ToolError, safe_eval, tool_specs, validate

CATALOG = """sku,name,category,unit_price,stock,specs
MS-VLV0145,SS316 Ball Valve 0.75" BSP,Valves,2475,1733,"{""material"": ""SS316"", ""unit"": ""pcs""}"
MS-VLV0200,Brass Ball Valve 2" BSP,Valves,1875,640,"{""material"": ""Brass"", ""unit"": ""pcs""}"
MS-VLV0300,Cast Iron Gate Valve DN80 Flanged,Valves,6150,0,"{""material"": ""Cast Iron"", ""unit"": ""pcs""}"
MS-FIT0110,Brass Elbow 90deg 1" BSP,Fittings,185,4200,"{""material"": ""Brass"", ""unit"": ""pcs""}"
MS-FAS0105,Hex Bolt M12x60 (box of 100),Fasteners,1450,600,"{""unit"": ""box""}"
"""


def make_db(path):
    con = sqlite3.connect(path)
    con.executescript("""
        CREATE TABLE customers(id TEXT PRIMARY KEY, name TEXT, contact_name TEXT, email TEXT, city TEXT, tier TEXT,
                               credit_limit INTEGER, credit_days INTEGER);
        CREATE TABLE orders(id TEXT PRIMARY KEY, customer_id TEXT, status TEXT, order_date TEXT, promised_date TEXT,
                            shipped_date TEXT, delivered_date TEXT, carrier TEXT, tracking_no TEXT, total REAL, notes TEXT,
                            refund_amount REAL, refund_approved_by TEXT);
        CREATE TABLE order_lines(id INTEGER PRIMARY KEY AUTOINCREMENT, order_id TEXT, sku TEXT, quantity INTEGER,
                                 unit TEXT, unit_price_charged REAL);
        CREATE TABLE meta(key TEXT PRIMARY KEY, value TEXT);
        INSERT INTO meta VALUES ('as_of_date', '2026-10-05');
        INSERT INTO customers VALUES ('CUST-0001', 'Apex Industries', 'Rahul Deshmukh', 'r@apex.example', 'Pune', 'Gold', 1, 60);
        INSERT INTO orders VALUES ('MS-10388', 'CUST-0001', 'delayed', '2026-09-23', '2026-09-29', NULL, NULL, NULL, NULL,
                                   44900, 'Plant maintenance window', NULL, NULL);
        INSERT INTO orders VALUES ('MS-10422', 'CUST-0001', 'shipped', '2026-09-29', '2026-10-06', '2026-10-02', NULL,
                                   'Delhivery', 'DL566614170', 40225, '', NULL, NULL);
        INSERT INTO orders VALUES ('MS-10001', 'CUST-0001', 'delivered', '2026-07-01', '2026-07-05', '2026-07-03',
                                   '2026-07-08', 'VRL', 'X1', 1000, '', NULL, NULL);
        INSERT INTO order_lines(order_id, sku, quantity, unit, unit_price_charged) VALUES
            ('MS-10388', 'MS-VLV0200', 20, 'pcs', 1875), ('MS-10388', 'MS-FIT0110', 40, 'pcs', 185);
    """)
    con.commit()
    con.close()


class DataMixin:
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        make_db(root / "orders.db")
        (root / "catalog.csv").write_text(CATALOG, encoding="utf-8")
        (root / "chat").mkdir()
        self.root = root
        self.hits = [Hit("bulk_pricing.md#2", "[Bulk Pricing | 2. Discount tiers | Effective 2026-01-01]\n250-499: 12%",
                         meta={"title": "Bulk Pricing", "heading": "2. Discount tiers", "effective_date": "2026-01-01"})]
        self.tb = Toolbox(root / "orders.db", root / "catalog.csv", retrieve=lambda q: self.hits)

    def tearDown(self):
        self.tmp.cleanup()

    def run_tool(self, name, **args):
        return self.tb.run(name, json.dumps(args))


# ---- fake OpenAI-style client ----------------------------------------------

def call(cid, name, **args):
    return SimpleNamespace(id=cid, type="function", function=SimpleNamespace(name=name, arguments=json.dumps(args)))


def reply(text="", calls=None, pin=100, pout=10):
    return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=text, tool_calls=calls))],
                           usage=SimpleNamespace(prompt_tokens=pin, completion_tokens=pout))


class FakeClient:
    """Returns the queued replies in order and records a deep copy of every request."""

    def __init__(self, *replies):
        self.replies, self.requests = list(replies), []
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self.create))

    def create(self, **kwargs):
        self.requests.append(copy.deepcopy(kwargs))
        r = self.replies.pop(0)
        if isinstance(r, Exception):
            raise r
        return r


class RateLimitError(Exception):  # name-matches openai.RateLimitError
    pass


# ---- the tool definitions ----------------------------------------------------

class ToolSpecTests(unittest.TestCase):
    def test_five_tools_with_json_schema_parameters_and_real_descriptions(self):
        names = [t["function"]["name"] for t in TOOLS]
        self.assertEqual(names, ["get_order", "search_catalog", "check_stock", "calculate", "search_policies"])
        for t in TOOLS:
            fn = t["function"]
            self.assertEqual(t["type"], "function")
            self.assertGreater(len(fn["description"]), 200, fn["name"])  # written like docs, not a label
            self.assertEqual(fn["parameters"]["type"], "object")
            self.assertIs(fn["parameters"]["additionalProperties"], False)
            self.assertTrue(fn["parameters"]["required"])

    def test_vague_mode_degrades_only_check_stock(self):
        vague = {t["function"]["name"]: t["function"] for t in tool_specs(vague=True)}
        self.assertEqual(vague["check_stock"]["description"], "Gets product data.")
        self.assertEqual(vague["check_stock"]["parameters"]["properties"]["sku"]["description"], "an id")
        self.assertEqual(vague["get_order"]["description"], TOOLS[0]["function"]["description"])
        self.assertNotEqual(TOOLS[2]["function"]["description"], "Gets product data.")  # original untouched

    def test_validator(self):
        schema = TOOLS[1]["function"]["parameters"]  # search_catalog
        self.assertEqual(validate(schema, {"query": "valve", "filters": {"category": "Valves", "min_stock": 5}}), [])
        problems = validate(schema, {"filters": {"category": "Toys", "max_unit_price": -1, "colour": "red"}, "x": 1})
        text = " | ".join(problems)
        for part in ("arguments.query is required", "arguments.x is not a known parameter", "must be one of",
                     "max_unit_price must be at least 0", "colour is not a known parameter"):
            self.assertIn(part, text)
        self.assertEqual(validate({"type": "integer"}, True), ["arguments must be an integer"])  # bool is not a number
        self.assertEqual(validate({"type": "integer"}, 5.0), [])


# ---- each tool ----------------------------------------------------------------

class OrderToolTests(DataMixin, unittest.TestCase):
    def test_order_status_comes_from_the_database(self):
        r = self.run_tool("get_order", order_id="MS-10422")
        self.assertTrue(r["ok"])
        o = r["result"]
        self.assertEqual((o["status"], o["carrier"], o["tracking_no"], o["days_late"], o["data_as_of"]),
                         ("shipped", "Delhivery", "DL566614170", 0, "2026-10-05"))
        self.assertEqual(o["customer"]["name"], "Apex Industries")
        self.assertNotIn("email", o["customer"])  # only what the task needs

    def test_lines_names_and_days_late(self):
        o = self.run_tool("get_order", order_id="MS-10388")["result"]
        self.assertEqual(o["days_late"], 6)  # promised 09-29, not delivered, today 10-05
        self.assertEqual(o["lines"][0], {"sku": "MS-VLV0200", "name": 'Brass Ball Valve 2" BSP', "quantity": 20,
                                         "unit": "pcs", "unit_price_charged_inr": 1875.0, "line_total_inr": 37500.0})
        self.assertEqual(self.run_tool("get_order", order_id="MS-10001")["result"]["days_late"], 3)  # delivered late

    def test_unknown_order_is_a_structured_error(self):
        self.assertEqual(self.run_tool("get_order", order_id="MS-99999"),
                         {"ok": False, "error": {"code": "order_not_found",
                                                 "message": "no order with id MS-99999 in the order database"}})

    def test_sql_injection_is_rejected_by_validation_and_db_is_untouched(self):
        r = self.run_tool("get_order", order_id="''; DROP TABLE orders;--")
        self.assertEqual(r["error"]["code"], "invalid_arguments")
        self.assertTrue(self.run_tool("get_order", order_id="MS-10422")["ok"])

    def test_database_is_opened_read_only(self):
        con = self.tb._connect()
        with self.assertRaises(sqlite3.OperationalError):
            con.execute("DELETE FROM orders")
        con.close()

    def test_missing_database_is_a_structured_error(self):
        tb = Toolbox(self.root / "nope.db", self.root / "catalog.csv")
        self.assertEqual(tb.run("get_order", '{"order_id": "MS-10422"}')["error"]["code"], "database_unavailable")


class CatalogToolTests(DataMixin, unittest.TestCase):
    def test_check_stock(self):
        self.assertEqual(self.run_tool("check_stock", sku="MS-VLV0200")["result"],
                         {"sku": "MS-VLV0200", "name": 'Brass Ball Valve 2" BSP', "stock": 640, "unit": "pcs", "in_stock": True})
        self.assertIs(self.run_tool("check_stock", sku="MS-VLV0300")["result"]["in_stock"], False)
        self.assertEqual(self.run_tool("check_stock", sku="MS-XYZ0001")["error"]["code"], "sku_not_found")
        self.assertEqual(self.run_tool("check_stock", sku="vlv0200")["error"]["code"], "invalid_arguments")

    def test_search_by_words_maps_stainless_to_ss(self):
        r = self.run_tool("search_catalog", query="stainless steel ball valves")["result"]
        self.assertEqual(r["results"][0]["sku"], "MS-VLV0145")

    def test_search_by_exact_sku_returns_only_that_product(self):
        r = self.run_tool("search_catalog", query="price of MS-VLV0200")["result"]
        self.assertEqual([p["sku"] for p in r["results"]], ["MS-VLV0200"])
        self.assertEqual(r["results"][0]["unit_price_inr"], 1875.0)

    def test_filters_and_no_match(self):
        r = self.run_tool("search_catalog", query="valve", filters={"max_unit_price": 2000, "min_stock": 1})["result"]
        self.assertEqual([p["sku"] for p in r["results"]], ["MS-VLV0200"])
        self.assertEqual(self.run_tool("search_catalog", query="teapot")["result"]["count"], 0)
        self.assertTrue(self.tb.run("search_catalog", '{"query": "valve", "filters": null}')["ok"])  # null = not given


class CalculatorTests(unittest.TestCase):
    def test_discount_maths_is_exact(self):
        self.assertEqual(safe_eval("340 * 1875 * (1 - 0.12)"), 561000)
        self.assertEqual(safe_eval("1875 * 0.88"), 1650)
        self.assertEqual(safe_eval("round(10 / 3, 2)"), 3.33)
        self.assertEqual(safe_eval("max(2, -abs(-5)) + 2 ** 3 - 7 // 2 + 7 % 4"), 10)

    def test_unsafe_or_malformed_expressions_are_rejected(self):
        cases = {"__import__('os').system('ls')": "unsupported_expression", "x + 1": "unsupported_expression",
                 "(1).__class__": "unsupported_expression", "'a' * 3": "unsupported_expression",
                 "True + 1": "unsupported_expression", "340 * 1,875": "unsupported_expression",
                 "12% of 5": "invalid_expression", "1 / 0": "division_by_zero", "9 ** 99": "number_too_large",
                 "10 ** 10 * 10 ** 10": "number_too_large", "1 + " * 60 + "1": "expression_too_long",
                 "round(1.5, 0.5)": "invalid_expression"}
        for expr, code in cases.items():
            with self.subTest(expr=expr), self.assertRaises(ToolError) as cm:
                safe_eval(expr)
            self.assertEqual(cm.exception.code, code)

    def test_calculate_tool_result(self):
        tb = Toolbox("x.db", "x.csv")
        self.assertEqual(tb.run("calculate", '{"expression": "2 + 2"}'), {"ok": True, "result": {"expression": "2 + 2", "result": 4}})


class PolicyToolTests(DataMixin, unittest.TestCase):
    def test_wraps_the_retriever_with_citable_ids(self):
        r = self.run_tool("search_policies", query="bulk discount tiers")["result"]["results"][0]
        self.assertEqual((r["id"], r["section"], r["effective_date"]), ("bulk_pricing.md#2", "2. Discount tiers", "2026-01-01"))

    def test_empty_index_and_crashing_retriever_become_errors(self):
        self.hits = []
        self.assertEqual(self.run_tool("search_policies", query="x")["error"]["code"], "index_empty")
        self.tb._retrieve = lambda q: 1 / 0
        err = self.run_tool("search_policies", query="x")["error"]
        self.assertEqual(err["code"], "tool_failed")
        self.assertIn("ZeroDivisionError", err["message"])


class RunNeverRaisesTests(DataMixin, unittest.TestCase):
    def test_bad_calls_come_back_as_structured_errors(self):
        self.assertEqual(self.tb.run("delete_order", "{}")["error"]["code"], "unknown_tool")
        self.assertEqual(self.tb.run("get_order", "{not json")["error"]["code"], "invalid_json")
        self.assertEqual(self.tb.run("get_order", "")["error"]["code"], "invalid_arguments")  # required missing
        self.assertEqual(self.tb.run("get_order", "[1, 2]")["error"]["code"], "invalid_arguments")

    def test_forced_crash(self):
        tb = Toolbox(self.root / "orders.db", self.root / "catalog.csv", fail=["get_order"])
        err = tb.run("get_order", '{"order_id": "MS-10422"}')["error"]
        self.assertEqual(err["code"], "tool_failed")
        self.assertIn("simulated outage", err["message"])


# ---- the round trip ------------------------------------------------------------

class RoundTripTests(DataMixin, unittest.TestCase):
    def answer(self, client, question="Q", **kw):
        return tooluse.answer(question, "m", client, self.tb, sleep=lambda s: None, log_file=None, **kw)

    def test_two_tools_at_once_then_final_answer(self):
        client = FakeClient(reply(calls=[call("c1", "check_stock", sku="MS-VLV0200"),
                                         call("c2", "search_catalog", query="MS-VLV0200")], pin=50, pout=5),
                            reply("640 in stock at 1875 each.", pin=80, pout=9))
        run = self.answer(client, "Is MS-VLV0200 in stock and what does it cost?")
        self.assertEqual(run.text, "640 in stock at 1875 each.")
        self.assertEqual([c.name for c in run.calls], ["check_stock", "search_catalog"])
        self.assertEqual((run.llm_calls, run.input_tokens, run.output_tokens), (2, 130, 14))
        first, second = client.requests
        self.assertEqual((first["tool_choice"], len(first["tools"])), ("auto", 5))
        self.assertIn("Today is 2026-10-05", first["messages"][0]["content"])
        msgs = second["messages"]
        self.assertEqual([m["role"] for m in msgs], ["system", "user", "assistant", "tool", "tool"])
        self.assertEqual([c["id"] for c in msgs[2]["tool_calls"]], ["c1", "c2"])
        self.assertEqual([m["tool_call_id"] for m in msgs[3:]], ["c1", "c2"])
        self.assertEqual(json.loads(msgs[3]["content"])["result"]["stock"], 640)

    def test_no_tool_needed_is_one_call(self):
        client = FakeClient(reply("I can only help with Meridian orders, products and policies."))
        run = self.answer(client, "What's the weather in Pune?")
        self.assertEqual((run.llm_calls, run.calls), (1, []))

    def test_unknown_order_goes_back_to_the_model(self):
        client = FakeClient(reply(calls=[call("c1", "get_order", order_id="MS-99999")]),
                            reply("I couldn't find order MS-99999."))
        run = self.answer(client)
        self.assertFalse(run.calls[0].ok)
        sent = json.loads(client.requests[1]["messages"][-1]["content"])
        self.assertEqual(sent["error"]["code"], "order_not_found")
        self.assertEqual(run.text, "I couldn't find order MS-99999.")

    def test_discount_goes_through_the_calculator(self):
        client = FakeClient(reply(calls=[call("c1", "calculate", expression="340 * 1875 * (1 - 0.12)")]),
                            reply("Total after 12%: 561000"))
        run = self.answer(client)
        self.assertEqual(run.calls[0].result, {"ok": True, "result": {"expression": "340 * 1875 * (1 - 0.12)", "result": 561000}})

    def test_second_round_is_recorded_but_not_run(self):
        client = FakeClient(reply(calls=[call("c1", "get_order", order_id="MS-10388")]),
                            reply(calls=[call("c2", "check_stock", sku="MS-VLV0200")]),
                            reply("Order delayed; stock not checked."))
        run = self.answer(client)
        self.assertEqual([c.name for c in run.calls], ["get_order"])  # check_stock never ran
        self.assertEqual(run.wanted, [("check_stock", '{"sku": "MS-VLV0200"}')])
        self.assertEqual((run.llm_calls, client.requests[2]["tool_choice"]), (3, "none"))
        self.assertEqual(client.requests[2]["messages"][-1], {"role": "user", "content": tooluse.NO_MORE_TOOLS})
        self.assertEqual(run.text, "Order delayed; stock not checked.")

    def test_too_many_calls_in_one_reply(self):
        calls = [call(f"c{i}", "calculate", expression="1 + 1") for i in range(tooluse.MAX_CALLS_PER_ROUND + 2)]
        run = self.answer(FakeClient(reply(calls=calls), reply("done")))
        self.assertEqual([c.result.get("error", {}).get("code") for c in run.calls[-2:]], ["too_many_calls"] * 2)
        self.assertTrue(all(c.ok for c in run.calls[:-2]))

    def test_rate_limit_is_retried(self):
        sleeps = []
        client = FakeClient(RateLimitError(), reply("hi"))
        run = tooluse.answer("Q", "m", client, self.tb, sleep=sleeps.append, log_file=None)
        self.assertEqual((run.text, run.attempts, sleeps), ("hi", 2, [1.0]))

    def test_provider_items_are_handed_back(self):
        first = reply(calls=[call("c1", "calculate", expression="1 + 1")])
        first.choices[0].message.provider_items = [{"type": "reasoning", "encrypted_content": "abc"}]
        client = FakeClient(first, reply("2"))
        self.answer(client)
        self.assertEqual(client.requests[1]["messages"][2]["provider_items"], [{"type": "reasoning", "encrypted_content": "abc"}])

    def test_every_call_is_logged(self):
        log = self.root / "logs" / "calls.jsonl"
        client = FakeClient(reply(calls=[call("c1", "get_order", order_id="MS-99999"), call("c2", "calculate", expression="1+1")]),
                            reply("ok"))
        tooluse.answer("Where is MS-99999?", "m", client, self.tb, log_file=log)
        rows = [json.loads(line) for line in log.read_text(encoding="utf-8").splitlines()]
        self.assertEqual([(r["tool"], r["ok"], r["error"]) for r in rows],
                         [("get_order", False, "order_not_found"), ("calculate", True, None)])
        self.assertEqual(rows[0]["question"], "Where is MS-99999?")
        self.assertIn("ms", rows[0])

    def test_log_path_env(self):
        with mock.patch.dict(os.environ, {"DESK_TOOL_LOG": "off"}):
            self.assertIsNone(tooluse.log_path())
        with mock.patch.dict(os.environ, {"DESK_TOOL_LOG": "x/y.jsonl"}):
            self.assertEqual(tooluse.log_path(), Path("x/y.jsonl"))

    def test_selection_scoring(self):
        self.assertEqual(tooluse.score_selection(["check_stock", "check_stock"], ["check_stock"]), (False, 1, 0))
        self.assertEqual(tooluse.score_selection(["check_stock"], ["check_stock", "search_catalog"]), (True, 1, 1))
        self.assertEqual(tooluse.score_selection([], []), (True, 0, 0))
        self.assertEqual(tooluse.score_selection([], ["get_order"]), (False, 0, 1))


# ---- SDK adapters --------------------------------------------------------------

class ClaudeToolTests(unittest.TestCase):
    def test_tools_and_history_map_to_anthropic_and_tool_use_maps_back(self):
        seen = []
        out = SimpleNamespace(content=[SimpleNamespace(type="text", text="Checking."),
                                       SimpleNamespace(type="tool_use", id="tu_2", name="check_stock", input={"sku": "MS-VLV0200"})],
                              stop_reason="tool_use", stop_details=None, usage=SimpleNamespace(input_tokens=9, output_tokens=3))
        claude = llm.ClaudeChat(SimpleNamespace(messages=SimpleNamespace(create=lambda **kw: seen.append(kw) or out)), max_tokens=500)
        messages = [{"role": "system", "content": "S"}, {"role": "user", "content": "Q"},
                    {"role": "assistant", "content": "", "tool_calls": [
                        {"id": "tu_1", "type": "function", "function": {"name": "get_order", "arguments": '{"order_id": "MS-1"}'}}]},
                    {"role": "tool", "tool_call_id": "tu_1", "content": '{"ok": false, "error": {"code": "x"}}'},
                    {"role": "user", "content": "Answer now."}]
        resp = claude.chat.completions.create(model="claude-sonnet-5-5", messages=messages, tools=tool_specs(), tool_choice="auto")
        kw = seen[0]
        self.assertEqual(kw["tool_choice"], {"type": "auto"})
        self.assertEqual(kw["tools"][0]["name"], "get_order")
        self.assertEqual(kw["tools"][0]["input_schema"], TOOLS[0]["function"]["parameters"])
        self.assertEqual(kw["messages"][1], {"role": "assistant", "content": [
            {"type": "tool_use", "id": "tu_1", "name": "get_order", "input": {"order_id": "MS-1"}}]})
        self.assertEqual(kw["messages"][2], {"role": "user", "content": [
            {"type": "tool_result", "tool_use_id": "tu_1", "content": '{"ok": false, "error": {"code": "x"}}', "is_error": True},
            {"type": "text", "text": "Answer now."}]})
        msg = resp.choices[0].message
        self.assertEqual((msg.content, resp.choices[0].finish_reason), ("Checking.", "tool_calls"))
        self.assertEqual((msg.tool_calls[0].id, msg.tool_calls[0].function.name, json.loads(msg.tool_calls[0].function.arguments)),
                         ("tu_2", "check_stock", {"sku": "MS-VLV0200"}))

    def test_tool_choice_none_and_plain_reply(self):
        seen = []
        out = SimpleNamespace(content=[SimpleNamespace(type="text", text="Hi")], stop_reason="end_turn", stop_details=None,
                              usage=SimpleNamespace(input_tokens=1, output_tokens=1))
        claude = llm.ClaudeChat(SimpleNamespace(messages=SimpleNamespace(create=lambda **kw: seen.append(kw) or out)))
        resp = claude.chat.completions.create(model="c", messages=[{"role": "user", "content": "Q"}], tools=tool_specs(), tool_choice="none")
        self.assertEqual(seen[0]["tool_choice"], {"type": "none"})
        self.assertIsNone(resp.choices[0].message.tool_calls)


class AzureResponsesTests(unittest.TestCase):
    def fake(self, output, text=""):
        seen = []
        resp = SimpleNamespace(output=output, output_text=text, usage=SimpleNamespace(input_tokens=11, output_tokens=4))
        raw = SimpleNamespace(responses=SimpleNamespace(create=lambda **kw: seen.append(copy.deepcopy(kw)) or resp),
                              chat=SimpleNamespace(completions=SimpleNamespace(create=lambda **kw: self.fail("chat.completions used"))))
        return llm.AzureGPT(raw), seen

    def test_tool_requests_use_the_responses_api(self):
        fc = SimpleNamespace(type="function_call", call_id="call_1", name="get_order", arguments='{"order_id":"MS-10422"}',
                             model_dump=lambda exclude_none=True: {"type": "function_call", "call_id": "call_1"})
        gpt, seen = self.fake([fc])
        resp = gpt.chat.completions.create(model="gpt-6.2-sol", temperature=0, tool_choice="auto", tools=tool_specs(),
                                           messages=[{"role": "system", "content": "S"}, {"role": "user", "content": "Q"}])
        kw = seen[0]
        self.assertEqual((kw["instructions"], kw["input"], kw["store"], kw["tool_choice"]),
                         ("S", [{"role": "user", "content": "Q"}], False, "auto"))
        self.assertNotIn("temperature", kw)
        self.assertEqual(kw["tools"][0]["name"], "get_order")
        self.assertEqual(kw["tools"][0]["parameters"], TOOLS[0]["function"]["parameters"])
        msg = resp.choices[0].message
        self.assertEqual((msg.tool_calls[0].id, msg.tool_calls[0].function.name), ("call_1", "get_order"))
        self.assertEqual(msg.provider_items, [{"type": "function_call", "call_id": "call_1"}])
        self.assertEqual((resp.usage.prompt_tokens, resp.usage.completion_tokens), (11, 4))

    def test_history_maps_to_items(self):
        gpt, seen = self.fake([], text="Shipped.")
        history = [{"role": "user", "content": "Q"},
                   {"role": "assistant", "content": "", "tool_calls": [
                       {"id": "call_1", "type": "function", "function": {"name": "get_order", "arguments": "{}"}}]},
                   {"role": "tool", "tool_call_id": "call_1", "content": "{}"}]
        resp = gpt.chat.completions.create(model="m", messages=history, tools=tool_specs(), tool_choice="none")
        self.assertEqual(seen[0]["input"], [{"role": "user", "content": "Q"},
                                            {"type": "function_call", "call_id": "call_1", "name": "get_order", "arguments": "{}"},
                                            {"type": "function_call_output", "call_id": "call_1", "output": "{}"}])
        self.assertEqual((resp.choices[0].message.content, resp.choices[0].message.tool_calls), ("Shipped.", None))
        history[1]["provider_items"] = [{"type": "reasoning", "encrypted_content": "e"}, {"type": "function_call", "call_id": "call_1"}]
        gpt.chat.completions.create(model="m", messages=history, tools=tool_specs())
        self.assertEqual(seen[1]["input"][1:3], history[1]["provider_items"])  # raw items handed back unchanged


# ---- CLI ---------------------------------------------------------------------------

class CliTests(DataMixin, unittest.TestCase):
    def run_cli(self, argv, client=None):
        out, err = io.StringIO(), io.StringIO()
        env = {"DESK_DATA_DIR": str(self.root), "DESK_TOOL_LOG": "off", "DESK_MODEL_TOOLS": "tools-model"}
        with mock.patch.dict(os.environ, env), contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            args = cli.build_parser().parse_args(argv)
            fn = cli.cmd_tools if args.cmd == "tools" else cli.cmd_eval_tools
            code = fn(args, client=client, sleep=lambda s: None, retrieve=lambda q: self.hits)
        return code, out.getvalue(), err.getvalue()

    def test_tools_command_prints_calls_answer_and_footer(self):
        client = FakeClient(reply(calls=[call("c1", "get_order", order_id="MS-10422")]),
                            reply("MS-10422 shipped with Delhivery [shipping.md#1]."))
        code, out, err = self.run_cli(["tools", "Where is MS-10422?"], client)
        self.assertEqual(code, 0)
        self.assertIn('[tool] get_order({"order_id": "MS-10422"}) -> ok', err)
        self.assertIn("MS-10422 shipped with Delhivery", out)
        self.assertIn("tools run: get_order | LLM calls: 2 | model: tools-model", out)
        self.assertIn("input tokens: 200 | output tokens: 20", out)
        self.assertEqual(client.requests[0]["model"], "tools-model")  # DESK_MODEL_TOOLS

    def test_errors_and_second_round_are_shown(self):
        client = FakeClient(reply(calls=[call("c1", "get_order", order_id="MS-99999")]),
                            reply(calls=[call("c2", "check_stock", sku="MS-VLV0200")]), reply("Not found."))
        code, out, err = self.run_cli(["tools", "Q", "--model", "other"], client)
        self.assertIn("-> error order_not_found", err)
        self.assertIn('"code": "order_not_found"', err)  # failed results are always printed
        self.assertIn("asked for a second round - NOT run: check_stock", err)
        self.assertEqual(client.requests[0]["model"], "other")

    def test_fail_tool_flag(self):
        client = FakeClient(reply(calls=[call("c1", "get_order", order_id="MS-10422")]), reply("Lookup is down."))
        code, out, err = self.run_cli(["tools", "Q", "--fail-tool", "get_order"], client)
        self.assertEqual(code, 0)
        self.assertIn("-> error tool_failed", err)

    def test_empty_question_simulate_and_missing_data(self):
        self.assertEqual(self.run_cli(["tools", "  "])[0], 2)
        code, out, err = self.run_cli(["tools", "hi", "--simulate", "429"])
        self.assertEqual(code, 1)
        self.assertIn("after 3 attempts", err)
        (self.root / "orders.db").unlink()
        code, out, err = self.run_cli(["tools", "hi"], FakeClient())
        self.assertEqual(code, 1)
        self.assertIn("orders.db not found", err)

    def test_eval_tools(self):
        prompts = [{"prompt": "Any MS-VLV0300 left?", "expected_tools": ["check_stock"]},
                   {"prompt": "Weather?", "expected_tools": []}]
        (self.root / "chat" / "tool_test_prompts.json").write_text(json.dumps(prompts), encoding="utf-8")
        client = FakeClient(reply(calls=[call("c1", "search_catalog", query="MS-VLV0300")]), reply("0 left"),
                            reply("Can't help with weather."))
        code, out, err = self.run_cli(["eval-tools", "--vague"], client)
        self.assertEqual(code, 0)
        self.assertIn("FAIL  expected [check_stock] -> chose [search_catalog]", out)
        self.assertIn("PASS  expected [no tool] -> chose [no tool]", out)
        self.assertIn("passed: 1/2", out)
        self.assertIn("VAGUE", out)
        self.assertEqual(client.requests[0]["tools"][2]["function"]["description"], "Gets product data.")

    def test_tools_role_is_registered(self):
        self.assertIn("TOOLS", llm.ROLES)
        with mock.patch.dict(os.environ, {"DESK_MODEL_TOOLS": "a", "DESK_MODEL": "b"}):
            self.assertEqual(llm.model_for("TOOLS"), "a")


if __name__ == "__main__":
    unittest.main()
