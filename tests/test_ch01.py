import contextlib, io, os, sys, types, unittest
from types import SimpleNamespace
from unittest import mock

from desk import cli, llm
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
            code = cli.cmd_ask(args, sleep=(sleeps.append if sleeps is not None else None), client=client)
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


if __name__ == "__main__":
    unittest.main()
