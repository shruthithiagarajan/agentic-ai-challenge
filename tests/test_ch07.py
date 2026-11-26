"""Challenge 7 (the agent loop) - offline: temp orders.db / catalog / outbox and a scripted fake SDK client."""
import contextlib, io, json, os, sqlite3, unittest
from pathlib import Path
from unittest import mock

from desk import agent, cli, llm
from desk.agent import Budget, run_agent
from desk.llm import LLMError
from desk.prompts import PLAN_RULE
from desk.tools import Toolbox, agent_tool_specs, validate, SCHEMAS
from tests.test_ch06 import DataMixin, FakeClient, RateLimitError, call, reply


class Clock:
    """Fake monotonic clock: each read advances by `step` seconds."""

    def __init__(self, step=0.0):
        self.t, self.step = 0.0, step

    def __call__(self):
        self.t += self.step
        return self.t


class AgentDataMixin(DataMixin):
    def setUp(self):
        super().setUp()
        con = sqlite3.connect(self.root / "orders.db")
        con.executescript("""
            INSERT INTO customers VALUES ('CUST-0002', 'Apex Fabricators', 'A N Other', 'a@fab.example', 'Pune', 'Silver', 1, 30);
            INSERT INTO customers VALUES ('CUST-0003', 'Kaveri Engineering', 'K', 'k@kaveri.example', 'Mysuru', 'Gold', 1, 60);
            UPDATE orders SET customer_id = 'CUST-0003' WHERE id = 'MS-10422';
        """)
        con.commit()
        con.close()
        self.outbox = self.root / "outbox"
        self.tb = Toolbox(self.root / "orders.db", self.root / "catalog.csv", retrieve=lambda q: self.hits, outbox=self.outbox)
        self.trace_dir = self.root / "traces"

    def agent(self, client, task="Do the task", budget=None, **kw):
        kw.setdefault("clock", Clock())
        return run_agent(task, "m", client, self.tb, budget=budget, sleep=lambda s: None, tool_log=None,
                         trace_dir=self.trace_dir, **kw)


# ---- new tools -------------------------------------------------------------------

class AgentToolTests(AgentDataMixin, unittest.TestCase):
    def test_list_orders_by_name_and_id_with_filters(self):
        r = self.run_tool("list_orders", customer="Apex Industries")["result"]
        self.assertEqual(r["customer"]["email"], "r@apex.example")  # the agent needs it for draft_email
        self.assertEqual([o["order_id"] for o in r["orders"]], ["MS-10388", "MS-10001"])  # newest first
        late = self.run_tool("list_orders", customer="cust-0001", min_days_late=6)["result"]["orders"]
        self.assertEqual([(o["order_id"], o["days_late"]) for o in late], [("MS-10388", 6)])
        self.assertEqual(late[0]["lines"], [{"sku": "MS-VLV0200", "quantity": 20, "unit": "pcs"},
                                            {"sku": "MS-FIT0110", "quantity": 40, "unit": "pcs"}])
        delivered = self.run_tool("list_orders", customer="CUST-0001", status="delivered")["result"]["orders"]
        self.assertEqual([o["order_id"] for o in delivered], ["MS-10001"])

    def test_list_orders_errors(self):
        self.assertEqual(self.run_tool("list_orders", customer="Nobody Ltd")["error"]["code"], "customer_not_found")
        err = self.run_tool("list_orders", customer="Apex")["error"]
        self.assertEqual(err["code"], "ambiguous_customer")
        self.assertIn("Apex Fabricators (CUST-0002)", err["message"])
        self.assertEqual(self.run_tool("list_orders", customer="CUST-0001", status="lost")["error"]["code"], "invalid_arguments")

    def test_draft_email_saves_to_outbox_and_sends_nothing(self):
        r = self.run_tool("draft_email", to="r@apex.example", subject="Apology: order MS-10388", body="Dear Rahul, sorry.")
        self.assertTrue(r["ok"])
        self.assertIs(r["result"]["sent"], False)
        path = Path(r["result"]["saved"])
        self.assertEqual(path.parent, self.outbox)
        text = path.read_text(encoding="utf-8")
        self.assertIn("To: r@apex.example\nSubject: Apology: order MS-10388\nStatus: DRAFT - NOT SENT", text)
        self.assertTrue(text.endswith("Dear Rahul, sorry.\n"))
        self.assertEqual(self.tb.drafts, [str(path)])

    def test_draft_email_path_cannot_escape_outbox_and_bad_address_is_rejected(self):
        r = self.run_tool("draft_email", to="a@b.example", subject="../../etc/passwd", body="x")
        self.assertEqual(Path(r["result"]["saved"]).parent, self.outbox)
        self.assertEqual(self.run_tool("draft_email", to="not-an-email", subject="s", body="b")["error"]["code"],
                         "invalid_arguments")

    def test_todo_and_array_validation(self):
        items = [{"step": "List orders", "status": "done"}, {"step": "Draft", "status": "pending"}]
        self.assertEqual(self.run_tool("todo", items=items)["result"], {"plan": items, "done": 1, "total": 2})
        self.assertEqual(self.tb.plan, items)
        problems = validate(SCHEMAS["todo"], {"items": [{"step": "x", "status": "maybe"}, "y"]})
        self.assertIn("arguments.items[0].status must be one of", " ".join(problems))
        self.assertIn("arguments.items[1] must be an object", problems)
        self.assertEqual(validate(SCHEMAS["todo"], {"items": []}), ["arguments.items needs at least 1 item(s)"])

    def test_agent_tool_list(self):
        names = [t["function"]["name"] for t in agent_tool_specs()]
        self.assertEqual(names, ["get_order", "search_catalog", "check_stock", "calculate", "search_policies",
                                 "list_orders", "draft_email"])
        self.assertEqual([t["function"]["name"] for t in agent_tool_specs(plan=True)][-1], "todo")


# ---- the loop -----------------------------------------------------------------------

class LoopTests(AgentDataMixin, unittest.TestCase):
    def test_multi_step_task_runs_to_completion(self):
        client = FakeClient(
            reply(calls=[call("a", "list_orders", customer="Apex Industries", min_days_late=6)], pin=100, pout=10),
            reply("Checking stock.", calls=[call("b", "check_stock", sku="MS-VLV0200"),
                                            call("c", "check_stock", sku="MS-FIT0110")], pin=200, pout=20),
            reply(calls=[call("d", "draft_email", to="r@apex.example", subject="Sorry", body="3% credit")], pin=300, pout=30),
            reply("Drafted 1 apology for MS-10388.", pin=400, pout=40))
        run = self.agent(client, "Apex task")
        self.assertEqual((run.stop_reason, run.finished, run.answer), ("done", True, "Drafted 1 apology for MS-10388."))
        self.assertEqual((run.steps, run.llm_calls, run.tool_calls), (4, 4, 4))
        self.assertEqual((run.input_tokens, run.output_tokens), (1000, 100))
        self.assertEqual(len(run.drafts), 1)
        self.assertEqual(run.notes, ["Checking stock."])
        # each request carries the whole history; results go back under their call ids
        last = client.requests[3]["messages"]
        self.assertEqual([m["role"] for m in last],
                         ["system", "user", "assistant", "tool", "assistant", "tool", "tool", "assistant", "tool"])
        self.assertEqual([m["tool_call_id"] for m in last if m["role"] == "tool"], ["a", "b", "c", "d"])
        self.assertEqual(json.loads(last[5]["content"])["result"]["stock"], 640)
        self.assertEqual(client.requests[0]["tool_choice"], "auto")
        self.assertEqual(len(client.requests[0]["tools"]), 7)

    def test_trace_is_readable_and_saved(self):
        client = FakeClient(reply(calls=[call("a", "check_stock", sku="MS-VLV0200")], pin=50, pout=5), reply("640."))
        events = []
        run = self.agent(client, on_event=events.append)
        self.assertEqual([e["event"] for e in events], ["start", "turn", "tool", "turn", "stop"])
        tool = events[2]
        self.assertEqual((tool["step"], tool["tool"], tool["arguments"], tool["ok"]), (1, "check_stock", '{"sku": "MS-VLV0200"}', True))
        self.assertGreater(tool["result_chars"], 50)
        self.assertEqual(tool["total_tokens"], 55)
        self.assertIn('"stock": 640', tool["result_preview"])
        saved = [json.loads(l) for l in Path(run.trace_file).read_text(encoding="utf-8").splitlines()]
        self.assertEqual([e["event"] for e in saved], [e["event"] for e in events])
        lines = [agent.format_event(e) for e in events]
        self.assertIn("[step  1]", lines[1])
        self.assertIn("50 in / 5 out (total 55) -> 1 tool call(s)", lines[1])
        self.assertIn('check_stock({"sku": "MS-VLV0200"}) -> ok', lines[2])
        self.assertIn("final answer", lines[3])
        self.assertIn("stop: done after 2 step(s), 1 tool call(s)", lines[4])

    def test_trace_dir_env(self):
        with mock.patch.dict(os.environ, {"DESK_TRACE_DIR": "off"}):
            run = run_agent("t", "m", FakeClient(reply("hi")), self.tb, tool_log=None, clock=Clock())
        self.assertEqual(run.trace_file, "")

    def test_stops_on_max_steps_and_reports_what_it_managed(self):
        client = FakeClient(*[reply("Still looking.", calls=[call(f"c{i}", "search_policies", query=f"2027 price list v{i}")])
                              for i in range(5)])
        run = self.agent(client, budget=Budget(max_steps=3))
        self.assertEqual((run.stop_reason, run.steps, run.finished), ("max_steps", 3, False))
        self.assertTrue(run.answer.startswith("STOPPED before finishing: step limit reached (3 steps)."))
        self.assertIn("Managed in 3 step(s): search_policies x3.", run.answer)
        self.assertIn("No drafts were saved.", run.answer)
        self.assertIn("Model's last note: Still looking.", run.answer)
        self.assertEqual(len(client.requests), 3)  # no extra LLM call for the report

    def test_stops_on_token_budget_without_running_the_pending_calls(self):
        client = FakeClient(reply(calls=[call("a", "check_stock", sku="MS-VLV0200")], pin=900, pout=50),
                            reply(calls=[call("b", "draft_email", to="a@b.example", subject="s", body="b")], pin=900, pout=50))
        run = self.agent(client, budget=Budget(max_tokens=1500))
        self.assertEqual(run.stop_reason, "max_tokens")
        self.assertIn("1,900 tokens > 1,500", run.stop_detail)
        self.assertEqual([c.name for c in run.calls], ["check_stock"])  # the draft never ran
        self.assertEqual(run.drafts, [])
        self.assertIn('Next calls the model wanted (not run): draft_email(', run.answer)

    def test_cost_budget_only_with_prices(self):
        replies = lambda: [reply(calls=[call(f"c{i}", "calculate", expression=f"{i} + 1")], pin=100_000, pout=0)
                           for i in range(4)] + [reply("done")]
        run = self.agent(FakeClient(*replies()), budget=Budget(max_cost_usd=0.50), pricing=(3.0, 15.0))
        self.assertEqual((run.stop_reason, run.stop_detail), ("max_cost", "$0.6000 > $0.50"))
        run = self.agent(FakeClient(*replies()), budget=Budget(max_cost_usd=0.50), pricing=None)
        self.assertEqual((run.stop_reason, run.cost_usd), ("done", None))  # no prices -> cost not enforced

    def test_stops_on_wall_clock(self):
        client = FakeClient(*[reply(calls=[call(f"c{i}", "calculate", expression=f"{i}+1")]) for i in range(10)])
        run = self.agent(client, budget=Budget(wall_clock_s=10), clock=Clock(step=2.0))
        self.assertEqual(run.stop_reason, "timeout")
        self.assertLess(run.steps, 10)

    def test_repeated_identical_call_is_detected(self):
        client = FakeClient(reply(calls=[call("a", "check_stock", sku="MS-VLV0300")]),
                            reply(calls=[call("b", "check_stock", sku="MS-VLV0300")]),
                            reply(calls=[call("c", "check_stock", **{"sku": "MS-VLV0300"})]),
                            reply("never reached"))
        client.replies[1].choices[0].message.tool_calls[0].function.arguments = '{ "sku" : "MS-VLV0300" }'  # same call
        run = self.agent(client, "Keep checking stock for MS-VLV0300 until it is above zero.")
        self.assertEqual(run.stop_reason, "repeated_call")
        self.assertIn('check_stock({"sku": "MS-VLV0300"}) x3', run.stop_detail)
        self.assertEqual([c.ok for c in run.calls], [True, True, False])  # the 3rd was not executed
        self.assertEqual(run.calls[2].result["error"]["code"], "repeated_call")
        self.assertIn("Failed calls: check_stock [repeated_call] x1.", run.answer)

    def test_too_many_calls_in_one_turn(self):
        calls = [call(f"c{i}", "calculate", expression=f"{i}+1") for i in range(4)]
        run = self.agent(FakeClient(reply(calls=calls), reply("ok")), budget=Budget(max_calls_per_turn=2))
        self.assertEqual([c.result.get("error", {}).get("code") for c in run.calls], [None, None, "too_many_calls", "too_many_calls"])
        self.assertEqual(run.stop_reason, "done")

    def test_tool_failure_is_fed_back_and_the_loop_continues(self):
        self.tb.fail = {"get_order"}
        client = FakeClient(reply(calls=[call("a", "get_order", order_id="MS-10388")]), reply("The order system is down."))
        run = self.agent(client)
        self.assertEqual((run.stop_reason, run.answer), ("done", "The order system is down."))
        self.assertEqual(json.loads(client.requests[1]["messages"][-1]["content"])["error"]["code"], "tool_failed")

    def test_plan_mode_adds_todo_and_the_rule(self):
        client = FakeClient(reply(calls=[call("a", "todo", items=[{"step": "List orders", "status": "pending"}])]), reply("ok"))
        run = self.agent(client, plan=True)
        self.assertIn(PLAN_RULE, client.requests[0]["messages"][0]["content"])
        self.assertEqual(client.requests[0]["tools"][-1]["function"]["name"], "todo")
        self.assertEqual(run.plan, [{"step": "List orders", "status": "pending"}])
        plain = FakeClient(reply("ok"))
        self.agent(plain)
        self.assertNotIn(PLAN_RULE, plain.requests[0]["messages"][0]["content"])

    def test_retry_and_give_up(self):
        sleeps = []
        run = run_agent("t", "m", FakeClient(RateLimitError(), reply("hi")), self.tb, sleep=sleeps.append,
                        tool_log=None, trace_dir=None, clock=Clock())
        self.assertEqual((run.answer, run.attempts, sleeps), ("hi", 2, [1.0]))
        with self.assertRaises(LLMError):
            run_agent("t", "m", None, self.tb, sleep=lambda s: None, tool_log=None, trace_dir=None, clock=Clock(),
                      simulate_error=llm.SimulatedError("429"))

    def test_provider_items_are_handed_back_each_turn(self):
        first = reply(calls=[call("a", "calculate", expression="1+1")])
        first.choices[0].message.provider_items = [{"type": "reasoning", "encrypted_content": "e1"}]
        client = FakeClient(first, reply("2"))
        self.agent(client)
        self.assertEqual(client.requests[1]["messages"][2]["provider_items"], [{"type": "reasoning", "encrypted_content": "e1"}])


# ---- CLI ---------------------------------------------------------------------------

class AgentCliTests(AgentDataMixin, unittest.TestCase):
    def run_cli(self, argv, client=None):
        out, err = io.StringIO(), io.StringIO()
        env = {"DESK_DATA_DIR": str(self.root), "DESK_TOOL_LOG": "off", "DESK_TRACE_DIR": str(self.trace_dir),
               "DESK_OUTBOX": str(self.outbox), "DESK_MODEL_AGENT": "agent-model"}
        with mock.patch.dict(os.environ, env), contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            args = cli.build_parser().parse_args(argv[:1] + ["--local"] + argv[1:])  # Ch7: in-process tools
            code = cli.cmd_agent(args, client=client, sleep=lambda s: None, retrieve=lambda q: self.hits, clock=Clock())
        return code, out.getvalue(), err.getvalue()

    def test_agent_command_prints_trace_answer_and_footer(self):
        client = FakeClient(reply(calls=[call("a", "draft_email", to="r@apex.example", subject="Sorry", body="b")], pin=10, pout=2),
                            reply("Drafted one apology.", pin=20, pout=3))
        code, out, err = self.run_cli(["agent", "Apologise to Apex", "--show-results"], client)
        self.assertEqual(code, 0)
        self.assertIn("[agent] model agent-model | budget: 25 steps, 500,000 tokens", err)
        self.assertIn('draft_email({"to": "r@apex.example"', err)
        self.assertIn('"sent": false', err)  # --show-results prints the result preview
        self.assertIn("Drafted one apology.", out)
        self.assertIn("stop: done | steps: 2 | tool calls: 1 | LLM calls: 2 | model: agent-model", out)
        self.assertIn("drafts saved (not sent): 1", out)
        self.assertIn("trace: ", out)
        self.assertIn("input tokens: 30 | output tokens: 5", out)
        self.assertEqual(len(list(self.outbox.glob("*.txt"))), 1)

    def test_limit_stop_exits_3(self):
        client = FakeClient(*[reply(calls=[call(f"c{i}", "calculate", expression=f"{i}+1")]) for i in range(3)])
        code, out, err = self.run_cli(["agent", "Loop forever", "--max-steps", "2"], client)
        self.assertEqual(code, 3)
        self.assertIn("STOPPED before finishing: step limit reached", out)
        self.assertIn("[agent] stop: max_steps", err)

    def test_presets_empty_task_and_simulate(self):
        (self.root / "chat" / "c07_agent_tasks.json").write_text(json.dumps(
            {"apex_task": "APEX TASK", "impossible_task": "IMPOSSIBLE", "repeat_trap": "REPEAT"}), encoding="utf-8")
        client = FakeClient(reply("ok"))
        self.run_cli(["agent", "--preset", "impossible"], client)
        self.assertEqual(client.requests[0]["messages"][1]["content"], "IMPOSSIBLE")
        self.assertEqual(self.run_cli(["agent", "  "])[0], 2)
        code, out, err = self.run_cli(["agent", "hi", "--simulate", "429"])
        self.assertEqual(code, 1)
        self.assertIn("after 3 attempts", err)

    def test_pricing_and_role(self):
        self.assertEqual(cli.pricing({"DESK_PRICE_IN": "3", "DESK_PRICE_OUT": "15"}), (3.0, 15.0))
        self.assertIsNone(cli.pricing({}))
        self.assertIn("AGENT", llm.ROLES)


if __name__ == "__main__":
    unittest.main()
