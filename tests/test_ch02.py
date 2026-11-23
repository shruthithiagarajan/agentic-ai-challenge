"""Challenge 2 - desk chat: streaming, history, slash commands, sliding window. Offline (fake SDK)."""
import io
import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from desk import cli, llm
from desk.chat import run_chat
from desk.llm import LLMError, stream_chat
from desk.prompts import SYSTEM_PROMPT
from desk.session import Session, count_tokens, handle_command, safe_name


class RateLimitError(Exception):  # name-matches openai.RateLimitError
    status_code = 429


def chunk(text=None, usage=None, x_groq=None, choices=True):
    c = SimpleNamespace(choices=[SimpleNamespace(delta=SimpleNamespace(content=text))] if choices else [], usage=usage)
    if x_groq is not None:
        c.x_groq = x_groq
    return c


def usage(p, c):
    return SimpleNamespace(prompt_tokens=p, completion_tokens=c)


def reply(*pieces, p=20, c=5):
    """A streamed reply: role-only chunk, content pieces, then a usage-only chunk (as with include_usage)."""
    return [chunk(None), *[chunk(t) for t in pieces], chunk(choices=False, usage=usage(p, c))]


def fake_client(create):
    return SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))


def scripted_client(*replies, seen=None):
    """Each create() call returns the next reply's chunks; records the messages it was sent."""
    queue = list(replies)

    def create(**kw):
        if seen is not None:
            seen.append([dict(m) for m in kw["messages"]])
        return iter(queue.pop(0))

    return fake_client(create)


class StreamTests(unittest.TestCase):
    def test_chunks_assembled_tokens_emitted_and_stream_kwargs(self):
        seen, tokens = {}, []
        c = fake_client(lambda **kw: seen.update(kw) or iter(reply("Hel", "lo", " there", p=42, c=3)))
        r = stream_chat([{"role": "user", "content": "hi"}], "m", temperature=0.5, client=c, on_token=tokens.append)
        self.assertEqual(tokens, ["Hel", "lo", " there"])
        self.assertEqual((r.text, r.input_tokens, r.output_tokens, r.estimated, r.attempts), ("Hello there", 42, 3, False, 1))
        self.assertIs(seen["stream"], True)
        self.assertEqual(seen["stream_options"], {"include_usage": True})
        self.assertEqual(seen["temperature"], 0.5)

    def test_usage_from_groq_x_groq_object_and_dict(self):
        for xg in (SimpleNamespace(usage=usage(11, 4)), {"usage": {"prompt_tokens": 11, "completion_tokens": 4}}):
            with self.subTest(x_groq=type(xg).__name__):
                chunks = [chunk("ok"), chunk(choices=False, x_groq=xg)]
                r = stream_chat([{"role": "user", "content": "q"}], "m", client=fake_client(lambda **kw: iter(chunks)), on_token=lambda t: None)
                self.assertEqual((r.input_tokens, r.output_tokens, r.estimated), (11, 4, False))

    def test_no_usage_falls_back_to_chars_div_4(self):
        msgs = [{"role": "system", "content": "s" * 40}, {"role": "user", "content": "u" * 20}]
        r = stream_chat(msgs, "m", client=fake_client(lambda **kw: iter([chunk("x" * 16)])), on_token=lambda t: None)
        self.assertEqual((r.input_tokens, r.output_tokens, r.estimated), (15, 4, True))

    def test_retries_before_first_token(self):
        calls, sleeps = [], []

        def create(**kw):
            calls.append(1)
            if len(calls) == 1:
                raise RateLimitError()
            return iter(reply("ok"))

        r = stream_chat([{"role": "user", "content": "q"}], "m", client=fake_client(create), sleep=sleeps.append, on_token=lambda t: None)
        self.assertEqual((r.text, r.attempts, sleeps), ("ok", 2, [1.0]))

    def test_error_while_waiting_for_first_token_is_retried(self):
        calls = []

        def stream_failing_before_text():
            yield chunk(None)  # role-only chunk: no text shown yet
            raise RateLimitError()

        def create(**kw):
            calls.append(1)
            return stream_failing_before_text() if len(calls) == 1 else iter(reply("ok"))

        tokens = []
        r = stream_chat([{"role": "user", "content": "q"}], "m", client=fake_client(create), sleep=lambda s: None, on_token=tokens.append)
        self.assertEqual((r.text, r.attempts, tokens), ("ok", 2, ["ok"]))

    def test_error_after_first_token_is_not_retried(self):
        calls = []

        def broken():
            yield chunk("partial")
            raise RateLimitError()

        def create(**kw):
            calls.append(1)
            return broken()

        tokens = []
        with self.assertRaises(LLMError) as cm:
            stream_chat([{"role": "user", "content": "q"}], "m", client=fake_client(create), sleep=lambda s: None, on_token=tokens.append)
        self.assertEqual((len(calls), tokens), (1, ["partial"]))
        self.assertIn("not retried", str(cm.exception))

    def test_simulated_failure_retries_then_fails(self):
        sleeps = []
        with self.assertRaises(LLMError) as cm:
            stream_chat([{"role": "user", "content": "q"}], "m", simulate="429", sleep=sleeps.append, on_token=lambda t: None)
        self.assertEqual(sleeps, [1.0, 2.0])
        self.assertIn("after 3 attempts", str(cm.exception))


class SessionTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name) / "sessions"
        self.s = Session("SYS", directory=self.dir)

    def tearDown(self):
        self.tmp.cleanup()

    def test_history_grows_with_system_first(self):
        self.assertEqual(self.s.messages("q1"), [{"role": "system", "content": "SYS"}, {"role": "user", "content": "q1"}])
        self.s.add_turn("q1", "a1")
        self.s.add_turn("q2", "a2")
        self.assertEqual([m["role"] for m in self.s.messages("q3")], ["system", "user", "assistant", "user", "assistant", "user"])
        self.assertEqual(self.s.messages()[3]["content"], "q2")

    def test_reset_clears_history_keeps_system(self):
        self.s.add_turn("q", "a")
        self.assertEqual(handle_command(self.s, "/reset"), ("ok", "history cleared"))
        self.assertEqual(self.s.messages(), [{"role": "system", "content": "SYS"}])

    def test_save_load_round_trip(self):
        self.s.add_turn("My name is Ravi", "Hello Ravi ‑ welcome")
        action, msg = handle_command(self.s, "/save ravi")
        self.assertEqual(action, "ok")
        self.assertTrue((self.dir / "ravi.json").is_file(), msg)
        other = Session("SYS", directory=self.dir)
        self.assertEqual(handle_command(other, "/load ravi"), ("ok", "loaded 2 messages from 'ravi'"))
        self.assertEqual(other.turns, self.s.turns)

    def test_load_replaces_current_history(self):
        self.s.add_turn("old", "old")
        self.s.save("one")
        self.s.add_turn("newer", "newer")
        self.s.load("one")
        self.assertEqual(len(self.s.turns), 2)

    def test_path_traversal_and_bad_names_rejected(self):
        for bad in ("../evil", "..", "a/b", "a\\b", "..\\x", "/abs", "C:evil", "", ".hidden", "x" * 80):
            with self.subTest(name=bad):
                with self.assertRaises(ValueError):
                    safe_name(bad)
                action, msg = handle_command(self.s, f"/save {bad}") if bad else ("ok", "error: usage")
                self.assertEqual(action, "ok")
                self.assertTrue(msg.startswith(("error", "usage")), msg)
        self.assertFalse(self.dir.exists())  # nothing written anywhere
        self.assertFalse((Path(self.tmp.name) / "evil.json").exists())
        for good in ("ravi", "order-123", "v1.2_test"):
            self.assertEqual(safe_name(good), good)

    def test_load_missing_corrupt_and_wrong_shape(self):
        self.assertIn("no saved session", handle_command(self.s, "/load nope")[1])
        self.dir.mkdir(parents=True)
        (self.dir / "bad.json").write_text("{not json", encoding="utf-8")
        self.assertIn("not valid JSON", handle_command(self.s, "/load bad")[1])
        (self.dir / "shape.json").write_text('{"turns": [{"role": "system", "content": "hijack"}]}', encoding="utf-8")
        self.assertIn("unexpected format", handle_command(self.s, "/load shape")[1])
        self.assertEqual(self.s.turns, [])

    def test_commands_usage_unknown_and_exit(self):
        self.assertEqual(handle_command(self.s, "/save"), ("ok", "usage: /save <name>"))
        self.assertIn("unknown command", handle_command(self.s, "/frobnicate")[1])
        self.assertEqual(handle_command(self.s, "/exit")[0], "exit")


class SlidingWindowTests(unittest.TestCase):
    def pair(self, i):
        return f"q{i} " + "x" * 400, f"a{i} " + "y" * 400  # ~100 tokens each

    def test_drops_oldest_pairs_and_keeps_system(self):
        s = Session("SYS", max_tokens=500)
        for i in range(5):
            s.add_turn(*self.pair(i))
        dropped = s.fit("new question")
        msgs = s.messages("new question")
        self.assertGreater(dropped, 0)
        self.assertEqual(dropped % 2, 0)  # whole user/assistant pairs
        self.assertLessEqual(count_tokens(msgs), 500)
        self.assertEqual(msgs[0], {"role": "system", "content": "SYS"})
        self.assertEqual(msgs[1]["role"], "user")
        self.assertTrue(msgs[-3]["content"].startswith("q4"))  # newest pair kept
        self.assertFalse(any(m["content"].startswith("q0") for m in msgs))  # oldest gone

    def test_under_budget_drops_nothing(self):
        s = Session("SYS", max_tokens=3000)
        s.add_turn("hi", "hello")
        self.assertEqual(s.fit("again"), 0)
        self.assertEqual(len(s.turns), 2)

    def test_pin_first_keeps_first_user_message(self):
        s = Session("SYS", max_tokens=500, pin_first=True)
        s.add_turn("My name is Ravi and I am at Acme.", "Noted.")
        for i in range(1, 6):
            s.add_turn(*self.pair(i))
        s.fit("What is my name?")
        msgs = s.messages("What is my name?")
        self.assertEqual(msgs[1], {"role": "user", "content": "My name is Ravi and I am at Acme."})
        self.assertLessEqual(count_tokens(msgs), 500)
        self.assertNotIn({"role": "assistant", "content": "Noted."}, msgs)

    def test_only_pinned_left_stops_without_looping(self):
        s = Session("S" * 4000, max_tokens=100)
        s.add_turn("q", "a")
        s.fit("q2")
        self.assertEqual(s.turns, [])


class ChatLoopTests(unittest.TestCase):
    def run_chat(self, lines, client, session=None, **kw):
        session = session or Session("SYS")
        inputs = iter(lines)

        def input_fn(prompt):
            try:
                return next(inputs)
            except StopIteration:
                raise EOFError

        out, err = io.StringIO(), io.StringIO()
        code = run_chat(session, "m", lambda r: f"--footer {r.input_tokens}", client=client, input_fn=input_fn, out=out, err=err, **kw)
        return code, out.getvalue(), err.getvalue(), session

    def test_second_turn_sends_first_exchange(self):
        seen = []
        client = scripted_client(reply("Hello Ravi"), reply("Your name is Ravi"), seen=seen)
        code, out, _, s = self.run_chat(["My name is Ravi", "What is my name?", "/exit"], client)
        self.assertEqual(code, 0)
        self.assertIn("desk> Hello Ravi", out)
        self.assertIn("--footer 20", out)
        self.assertEqual(seen[1][1:], [{"role": "user", "content": "My name is Ravi"}, {"role": "assistant", "content": "Hello Ravi"},
                                       {"role": "user", "content": "What is my name?"}])
        self.assertEqual(len(s.turns), 4)

    def test_failed_turn_not_added_and_eof_exits(self):
        def create(**kw):
            raise ValueError("boom")  # non-retryable

        code, _, err, s = self.run_chat(["hi"], fake_client(create))
        self.assertEqual((code, s.turns), (0, []))
        self.assertIn("error:", err)

    def test_blank_lines_ignored_and_reset_mid_chat(self):
        seen = []
        client = scripted_client(reply("a1"), reply("a2"), seen=seen)
        self.run_chat(["", "   ", "q1", "/reset", "q2"], client)
        self.assertEqual(len(seen), 2)
        self.assertEqual(seen[1][1:], [{"role": "user", "content": "q2"}])

    def test_window_notice_printed_when_history_dropped(self):
        s = Session("SYS", max_tokens=100)  # system 4 + q0 104 + a0 4 + next 5 = 117 > 100
        s.add_turn("q0 " + "x" * 400, "a0")
        _, _, err, _ = self.run_chat(["next"], scripted_client(reply("ok")), session=s)
        self.assertIn("[history] dropped 2", err)


class CliChatTests(unittest.TestCase):
    def test_parser_chat_flags(self):
        a = cli.build_parser().parse_args(["chat", "--pin-first", "--temperature", "0", "--model", "x"])
        self.assertEqual((a.cmd, a.pin_first, a.temperature, a.model), ("chat", True, 0.0, "x"))
        self.assertFalse(cli.build_parser().parse_args(["chat"]).pin_first)

    def test_cmd_chat_uses_env_budget_system_prompt_and_history_footer(self):
        seen = []
        args = cli.build_parser().parse_args(["chat"])
        inputs = iter(["hi", "/exit"])
        out = io.StringIO()
        with mock.patch.dict(os.environ, {"DESK_MAX_HISTORY_TOKENS": "1234"}, clear=True), mock.patch("sys.stdout", out):
            code = cli.cmd_chat(args, client=scripted_client(reply("hey"), seen=seen), input_fn=lambda p: next(inputs))
        self.assertEqual(code, 0)
        self.assertEqual(seen[0][0], {"role": "system", "content": SYSTEM_PROMPT})
        self.assertIn("/1234 tokens", out.getvalue())

    def test_bad_budget_env_rejected(self):
        args = cli.build_parser().parse_args(["chat"])
        for bad in ("abc", "0", "-5"):
            with self.subTest(bad=bad), mock.patch.dict(os.environ, {"DESK_MAX_HISTORY_TOKENS": bad}, clear=True), mock.patch("sys.stderr", io.StringIO()):
                self.assertEqual(cli.cmd_chat(args, input_fn=lambda p: "/exit"), 2)

    def test_system_prompt_forbids_inventing(self):
        self.assertIn("If you don't know, say so. Never invent order, price, or policy details", SYSTEM_PROMPT)

    def test_estimated_footer_marked(self):
        r = llm.Result("t", 1, 2, 0.1, 1, estimated=True)
        self.assertIn("output tokens: 2 (estimated)", cli.footer(r, env={}))


if __name__ == "__main__":
    unittest.main()
