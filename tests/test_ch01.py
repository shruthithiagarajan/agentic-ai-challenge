import contextlib, io, os, sys, types, unittest
from types import SimpleNamespace
from unittest import mock

from desk import cli, llm, rag
from desk.llm import LLMError, SimulatedError, call_with_retry


class RateLimitError(Exception):  # name-matches openai.RateLimitError
    status_code = 429


class APITimeoutError(Exception):  # name-matches openai.APITimeoutError
    pass


class AuthenticationError(Exception):
    status_code = 401


class APIConnectionError(Exception):  # name-matches openai.APIConnectionError (DNS, refused, no network)
    pass


class APIStatusError(Exception):
    status_code = 413


def fake_client(create):
    return SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))


def ok_response(text="Hello", pin=12, pout=7):
    return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=text))], usage=SimpleNamespace(prompt_tokens=pin, completion_tokens=pout))


class RetryTests(unittest.TestCase):
    def test_backoff_1s_then_2s_then_success(self):
        calls, sleeps = [], []

        def fn():
            calls.append(1)
            if len(calls) < 3:
                raise RateLimitError()
            return "ok"

        self.assertEqual(call_with_retry(fn, sleep=sleeps.append), ("ok", 3))
        self.assertEqual(sleeps, [1.0, 2.0])

    def test_rate_limit_exhausts_three_attempts(self):
        sleeps = []
        with self.assertRaises(LLMError) as cm:
            call_with_retry(lambda: (_ for _ in ()).throw(RateLimitError()), sleep=sleeps.append)
        self.assertEqual((cm.exception.attempts, sleeps), (3, [1.0, 2.0]))
        self.assertIn("after 3 attempts", str(cm.exception))
        self.assertIn("429", str(cm.exception))

    def test_timeout_is_retried(self):
        sleeps = []
        with self.assertRaises(LLMError) as cm:
            call_with_retry(lambda: (_ for _ in ()).throw(APITimeoutError()), sleep=sleeps.append)
        self.assertEqual(sleeps, [1.0, 2.0])
        self.assertIn("timed out", str(cm.exception))

    def test_other_errors_are_not_retried(self):
        sleeps = []
        with self.assertRaises(LLMError) as cm:
            call_with_retry(lambda: (_ for _ in ()).throw(AuthenticationError("bad key")), sleep=sleeps.append)
        self.assertEqual((cm.exception.attempts, sleeps), (1, []))
        self.assertIn("GROQ_API_KEY", str(cm.exception))

    def test_network_error_is_retried(self):
        sleeps = []
        with self.assertRaises(LLMError) as cm:
            call_with_retry(lambda: (_ for _ in ()).throw(APIConnectionError()), sleep=sleeps.append)
        self.assertEqual((cm.exception.attempts, sleeps), (3, [1.0, 2.0]))
        self.assertIn("could not reach the Groq API", str(cm.exception))

    def test_request_too_large_not_retried_with_clear_message(self):
        sleeps = []
        with self.assertRaises(LLMError) as cm:
            call_with_retry(lambda: (_ for _ in ()).throw(APIStatusError("Error code: 413 - long raw text")), sleep=sleeps.append)
        self.assertEqual((cm.exception.attempts, sleeps), (1, []))
        self.assertIn("too large", str(cm.exception))
        self.assertNotIn("long raw text", str(cm.exception))


class AskTests(unittest.TestCase):
    def test_success_with_injected_client_sends_system_prompt_model_temperature(self):
        seen = {}

        def create(**kw):
            seen.update(kw)
            return ok_response()

        r = llm.ask("Q?", "SYS", "openai/gpt-oss-20b", temperature=0.0, client=fake_client(create))
        self.assertEqual((r.text, r.input_tokens, r.output_tokens, r.attempts), ("Hello", 12, 7, 1))
        self.assertEqual(seen["model"], "openai/gpt-oss-20b")
        self.assertEqual(seen["temperature"], 0.0)
        self.assertEqual([m["role"] for m in seen["messages"]], ["system", "user"])

    def test_temperature_omitted_when_not_given(self):
        seen = {}
        llm.ask("Q?", "S", "m", client=fake_client(lambda **kw: seen.update(kw) or ok_response()))
        self.assertNotIn("temperature", seen)

    def test_missing_api_key(self):
        with mock.patch.dict(os.environ, {}, clear=True):
            with self.assertRaises(LLMError) as cm:
                llm.ask("Q?", "S", "m")
        self.assertIn("GROQ_API_KEY is not set", str(cm.exception))

    def test_groq_endpoint_constant(self):
        self.assertEqual(llm.GROQ_BASE_URL, "https://api.groq.com/openai/v1")


class CliTests(unittest.TestCase):
    def run_cli(self, argv, env=None, client=None, sleeps=None):
        args = cli.build_parser().parse_args(argv)
        out, err = io.StringIO(), io.StringIO()
        with mock.patch.dict(os.environ, env or {}, clear=True), contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = cli.cmd_ask(args, sleep=(sleeps.append if sleeps is not None else None), client=client,
                               retrieve=lambda q: [rag.Hit("policy.md#1", "stub chunk", 0.1)])  # Ch4: ask retrieves first
        return code, out.getvalue(), err.getvalue()

    def test_footer_fields_and_na_cost(self):
        code, out, _ = self.run_cli(["ask", "hi"], client=fake_client(lambda **kw: ok_response()))
        self.assertEqual(code, 0)
        for s in ("Hello", "input tokens: 12", "output tokens: 7", "latency:", "est. cost: n/a"):
            self.assertIn(s, out)

    def test_cost_from_env(self):
        code, out, _ = self.run_cli(["ask", "hi"], env={"DESK_PRICE_IN": "1", "DESK_PRICE_OUT": "2"}, client=fake_client(lambda **kw: ok_response(pin=1000, pout=500)))
        self.assertIn("est. cost: $0.002000", out)

    def test_default_and_env_and_flag_model_precedence(self):
        seen = []
        c = fake_client(lambda **kw: seen.append(kw["model"]) or ok_response())
        self.run_cli(["ask", "hi"], client=c)
        self.run_cli(["ask", "hi"], env={"DESK_MODEL": "openai/gpt-oss-20b"}, client=c)
        self.run_cli(["ask", "hi", "--model", "qwen/qwen3.8-27b"], env={"DESK_MODEL": "openai/gpt-oss-20b"}, client=c)
        self.assertEqual(seen, ["openai/gpt-oss-120b", "openai/gpt-oss-20b", "qwen/qwen3.8-27b"])

    def test_temperature_flag_passed(self):
        seen = {}
        self.run_cli(["ask", "hi", "--temperature", "1"], client=fake_client(lambda **kw: seen.update(kw) or ok_response()))
        self.assertEqual(seen["temperature"], 1.0)

    def test_simulate_429_retries_then_fails_non_zero(self):
        sleeps = []
        code, out, err = self.run_cli(["ask", "hi", "--simulate", "429"], sleeps=sleeps)
        self.assertEqual((code, out, sleeps), (1, "", [1.0, 2.0]))
        self.assertEqual(err.count("[retry]"), 2)
        self.assertIn("after 3 attempts", err)

    def test_simulate_network_retries_then_fails(self):
        sleeps = []
        code, _, err = self.run_cli(["ask", "hi", "--simulate", "network"], sleeps=sleeps)
        self.assertEqual((code, sleeps), (1, [1.0, 2.0]))
        self.assertIn("could not reach the Groq API", err)

    def test_empty_or_blank_question_rejected_without_calling_api(self):
        create = mock.Mock()
        for q in ("", "   ", "\n\t"):
            code, out, err = self.run_cli(["ask", q], client=fake_client(create))
            self.assertEqual((code, out), (2, ""))
            self.assertIn("question is empty", err)
        create.assert_not_called()

    def test_temperature_range_checked_locally(self):
        for bad in ("-0.1", "2.5", "5", "hot"):
            with self.subTest(bad=bad), contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit) as cm:
                cli.build_parser().parse_args(["ask", "hi", "--temperature", bad])
            self.assertEqual(cm.exception.code, 2)
        for ok in ("0", "1", "2"):
            self.assertEqual(cli.build_parser().parse_args(["ask", "hi", "--temperature", ok]).temperature, float(ok))

    def test_missing_key_exits_non_zero_with_clear_message(self):
        code, _, err = self.run_cli(["ask", "hi"])
        self.assertEqual(code, 1)
        self.assertIn("GROQ_API_KEY is not set", err)

    def test_main_loads_dotenv(self):
        fake = types.ModuleType("dotenv")
        fake.load_dotenv = mock.Mock()
        with mock.patch.dict(sys.modules, {"dotenv": fake}), mock.patch.object(cli, "cmd_ask", return_value=0):
            with self.assertRaises(SystemExit) as cm:
                cli.main(["ask", "hi"])
        self.assertEqual(cm.exception.code, 0)
        fake.load_dotenv.assert_called_once()

    def test_utf8_output_lets_cp1252_stream_print_unicode(self):
        raw = io.BytesIO()
        stream = io.TextIOWrapper(raw, encoding="cp1252")
        with self.assertRaises(UnicodeEncodeError):
            print("non‑breaking", file=stream)
        cli.utf8_output(stream)
        print("non‑breaking", file=stream)
        stream.flush()
        self.assertIn("non‑breaking".encode("utf-8"), raw.getvalue())


# ---- Azure AI Foundry provider (GPT via openai SDK, Claude via anthropic SDK) ----

AZURE_ENV = {"AZURE_AI_API_KEY": "k", "AZURE_OPENAI_BASE_URL": "https://r.services.ai.azure.com/openai/v1",
             "AZURE_ANTHROPIC_BASE_URL": "https://r.services.ai.azure.com/anthropic"}


def claude_message(text="Paris", stop="end_turn", pin=23, pout=5):
    return SimpleNamespace(content=[SimpleNamespace(type="thinking", thinking=""), SimpleNamespace(type="text", text=text)],
                           stop_reason=stop, stop_details=None, usage=SimpleNamespace(input_tokens=pin, output_tokens=pout))


class FakeStream:
    def __init__(self, pieces, final):
        self.text_stream, self.final = iter(pieces), final

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def get_final_message(self):
        return self.final


def fake_anthropic(message=None, stream=None, seen=None):
    def record(result):
        return lambda **kw: (seen.append(kw) if seen is not None else None) or result
    return SimpleNamespace(messages=SimpleNamespace(create=record(message), stream=record(stream)))


class BadRequestError(Exception):  # name-matches openai.BadRequestError
    status_code = 400


class ProviderTests(unittest.TestCase):
    def test_provider_selection(self):
        cases = [({}, "groq"), ({"AZURE_AI_API_KEY": "k"}, "azure"),
                 ({"AZURE_AI_API_KEY": "k", "DESK_PROVIDER": "groq"}, "groq"), ({"DESK_PROVIDER": " Azure "}, "azure")]
        for env, want in cases:
            with self.subTest(env=env), mock.patch.dict(os.environ, env, clear=True):
                self.assertEqual(llm.provider(), want)
        with mock.patch.dict(os.environ, {"DESK_PROVIDER": "openai"}, clear=True), self.assertRaises(LLMError):
            llm.provider()

    def test_make_client_routes_claude_models_to_anthropic(self):
        with mock.patch.dict(os.environ, AZURE_ENV, clear=True):
            self.assertIsInstance(llm.make_client(model="claude-sonnet-5-5"), llm.ClaudeChat)
            self.assertIsInstance(llm.make_client(model="gpt-6.2-sol"), llm.AzureGPT)

    def test_missing_azure_settings_are_clear_errors(self):
        with mock.patch.dict(os.environ, {"DESK_PROVIDER": "azure"}, clear=True), self.assertRaises(LLMError) as cm:
            llm.make_client(model="gpt-6.2-sol")
        self.assertIn("AZURE_AI_API_KEY is not set", str(cm.exception))
        env = dict(AZURE_ENV, AZURE_ANTHROPIC_BASE_URL="")
        with mock.patch.dict(os.environ, env, clear=True), self.assertRaises(LLMError) as cm:
            llm.make_client(model="claude-sonnet-5-5")
        self.assertIn("AZURE_ANTHROPIC_BASE_URL is not set", str(cm.exception))

    def test_each_challenge_role_gets_its_own_model(self):
        env = {"DESK_MODEL": "fallback", "DESK_MODEL_CHAT": "claude-sonnet-5-5", "DESK_MODEL_EXTRACT": "gpt-6.2-sol"}
        with mock.patch.dict(os.environ, env, clear=True):
            self.assertEqual(llm.model_for("CHAT"), "claude-sonnet-5-5")
            self.assertEqual(llm.model_for("EXTRACT"), "gpt-6.2-sol")
            self.assertEqual(llm.model_for("JUDGE"), "fallback")  # role not set -> DESK_MODEL
            self.assertEqual(llm.model_for("CHAT", override="from-flag"), "from-flag")  # --model wins
        with mock.patch.dict(os.environ, {}, clear=True):
            self.assertEqual(llm.model_for("RAG"), llm.DEFAULT_MODEL)
        self.assertEqual(set(llm.ROLES), {"ONECALL", "CHAT", "EXTRACT", "RAG", "REWRITE", "JUDGE", "TOOLS", "AGENT"})

    def test_commands_use_their_role_model(self):
        env = {"DESK_MODEL_RAG": "rag-model", "DESK_MODEL_REWRITE": "rewrite-model", "DESK_MODEL_CHAT": "chat-model"}
        seen = []
        client = fake_client(lambda **kw: seen.append(kw["model"]) or ok_response("standalone query [a.md#1]"))
        args = cli.build_parser().parse_args(["ask", "and for 30,000?", "--prev", "Freight on 18,000?"])
        with mock.patch.dict(os.environ, env, clear=True), contextlib.redirect_stdout(io.StringIO()), \
                contextlib.redirect_stderr(io.StringIO()):
            cli.cmd_ask(args, client=client, retrieve=lambda q: [rag.Hit("a.md#1", "x", 0.1)])
            self.assertEqual(cli.pick_model(cli.build_parser().parse_args(["chat"]), "CHAT"), "chat-model")
        self.assertEqual(seen, ["rewrite-model", "rag-model"])  # rewrite first, then the grounded answer

    def test_messages_name_the_active_provider(self):
        with mock.patch.dict(os.environ, AZURE_ENV, clear=True):
            self.assertIn("Azure AI Foundry", llm.retry_reason(APIConnectionError()))
            self.assertIn("AZURE_AI_API_KEY", llm.describe(AuthenticationError()))


class AzureGPTTests(unittest.TestCase):
    def test_temperature_dropped_after_the_deployment_rejects_it(self):
        calls = []

        def create(**kw):
            calls.append(dict(kw))
            if "temperature" in kw:
                raise BadRequestError("Unsupported value: 'temperature' does not support 0 with this model.")
            return ok_response("Paris")

        gpt = llm.AzureGPT(fake_client(create))
        with contextlib.redirect_stderr(io.StringIO()) as err:
            r = llm.ask("Q?", "S", "gpt-6.2-sol", temperature=0.0, client=gpt)
            llm.ask("Q?", "S", "gpt-6.2-sol", temperature=0.0, client=gpt)
        self.assertEqual(r.text, "Paris")
        self.assertEqual(["temperature" in c for c in calls], [True, False, False])  # rejected once, then never sent
        self.assertEqual(err.getvalue(), "")  # internal temperature=0 calls stay quiet; the CLI notes a user's --temperature

    def test_cli_notes_an_ignored_temperature_on_azure_only(self):
        args = cli.build_parser().parse_args(["ask", "hi", "--temperature", "0"])
        for env, expect in ((AZURE_ENV, True), ({}, False)):
            with self.subTest(env=bool(env)), mock.patch.dict(os.environ, env, clear=True), \
                    contextlib.redirect_stderr(io.StringIO()) as err:
                cli.temperature_note(args)
            self.assertEqual("--temperature is ignored on Azure" in err.getvalue(), expect)

    def test_other_bad_requests_are_not_swallowed(self):
        def create(**kw):
            raise BadRequestError("context length exceeded")

        with self.assertRaises(LLMError):
            llm.ask("Q?", "S", "gpt-6.2-sol", temperature=0.0, client=llm.AzureGPT(fake_client(create)))


class ClaudeChatTests(unittest.TestCase):
    def test_maps_openai_style_call_to_anthropic_messages(self):
        seen = []
        claude = llm.ClaudeChat(fake_anthropic(message=claude_message(), seen=seen), max_tokens=1000)
        r = llm.ask("Capital of France?", "Be brief.", "claude-sonnet-5-5", temperature=0.0, client=claude)
        self.assertEqual((r.text, r.input_tokens, r.output_tokens), ("Paris", 23, 5))  # thinking block skipped
        self.assertEqual(seen[0], {"model": "claude-sonnet-5-5", "max_tokens": 1000, "system": "Be brief.",
                                   "messages": [{"role": "user", "content": "Capital of France?"}]})  # no temperature

    def test_max_tokens_from_env(self):
        with mock.patch.dict(os.environ, {"DESK_MAX_TOKENS": "2048"}, clear=True):
            self.assertEqual(llm.ClaudeChat(fake_anthropic()).max_tokens, 2048)
        with mock.patch.dict(os.environ, {}, clear=True):
            self.assertEqual(llm.ClaudeChat(fake_anthropic()).max_tokens, llm.CLAUDE_MAX_TOKENS)

    def test_json_response_format_becomes_an_instruction_and_fences_are_stripped(self):
        seen = []
        claude = llm.ClaudeChat(fake_anthropic(message=claude_message('```json\n{"city": "Paris"}\n```'), seen=seen))
        fmt = {"type": "json_schema", "json_schema": {"name": "A", "schema": {"type": "object"}, "strict": False}}
        resp = claude.chat.completions.create(model="claude-sonnet-5-5", response_format=fmt,
                                              messages=[{"role": "system", "content": "Extract."},
                                                        {"role": "user", "content": "Email"}])
        self.assertEqual(resp.choices[0].message.content, '{"city": "Paris"}')
        self.assertIn('ONE JSON object only', seen[0]["system"])
        self.assertIn('{"type": "object"}', seen[0]["system"])

    def test_refusal_is_a_clear_error_and_not_retried(self):
        sleeps = []
        claude = llm.ClaudeChat(fake_anthropic(message=claude_message("", stop="refusal")))
        with self.assertRaises(LLMError) as cm:
            llm.ask("Q?", "S", "claude-sonnet-5-5", client=claude, sleep=sleeps.append)
        self.assertIn("Claude declined the request", str(cm.exception))
        self.assertEqual(sleeps, [])

    def test_streaming_yields_text_then_usage(self):
        final = claude_message("Hello there", pin=30, pout=4)
        claude = llm.ClaudeChat(fake_anthropic(stream=FakeStream(["Hello", " there"], final)))
        tokens = []
        r = llm.stream_chat([{"role": "system", "content": "S"}, {"role": "user", "content": "hi"}],
                            "claude-sonnet-5-5", client=claude, on_token=tokens.append)
        self.assertEqual((tokens, r.text, r.input_tokens, r.output_tokens, r.estimated),
                         (["Hello", " there"], "Hello there", 30, 4, False))


if __name__ == "__main__":
    unittest.main()
