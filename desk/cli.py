import argparse
import os
import sys

from .chat import run_chat
from .llm import DEFAULT_MODEL, LLMError, ask
from .prompts import SYSTEM_PROMPT
from .session import DEFAULT_MAX_HISTORY_TOKENS, Session, count_tokens


def utf8_output(*streams):
    """Model replies contain chars like U+2011; Windows pipes default to cp1252 and would crash on print."""
    for s in streams or (sys.stdout, sys.stderr):
        if hasattr(s, "reconfigure"):
            s.reconfigure(encoding="utf-8", errors="replace")


def estimate_cost(in_tok, out_tok, env=os.environ):
    """USD, from DESK_PRICE_IN / DESK_PRICE_OUT (per 1M tokens). None if unset."""
    try:
        pin, pout = float(env["DESK_PRICE_IN"]), float(env["DESK_PRICE_OUT"])
    except (KeyError, ValueError):
        return None
    return (in_tok * pin + out_tok * pout) / 1_000_000


def footer(r, env=os.environ):
    cost = estimate_cost(r.input_tokens, r.output_tokens, env)
    cost_s = f"${cost:.6f}" if cost is not None else "n/a"
    est = " (estimated)" if getattr(r, "estimated", False) else ""
    return f"--\ninput tokens: {r.input_tokens} | output tokens: {r.output_tokens}{est} | latency: {r.latency_s:.2f}s | est. cost: {cost_s}"


def pick_model(args):
    return args.model or os.getenv("DESK_MODEL") or DEFAULT_MODEL


def temperature(value):
    """argparse type: a float in Groq's accepted range 0-2."""
    t = float(value)
    if not 0 <= t <= 2:
        raise argparse.ArgumentTypeError(f"must be between 0 and 2, got {value}")
    return t


def cmd_ask(args, sleep=None, client=None):
    if not args.question.strip():
        print("error: question is empty - try: desk ask \"your question\"", file=sys.stderr)
        return 2
    model = pick_model(args)
    extra = {"sleep": sleep} if sleep else {}
    try:
        r = ask(args.question, SYSTEM_PROMPT, model, temperature=args.temperature, simulate=args.simulate, client=client,
                log=lambda m: print(f"[retry] {m}", file=sys.stderr), **extra)
    except LLMError as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    print(r.text)
    print(footer(r))
    return 0


def cmd_chat(args, sleep=None, client=None, input_fn=input):
    raw = os.getenv("DESK_MAX_HISTORY_TOKENS") or str(DEFAULT_MAX_HISTORY_TOKENS)
    if not raw.isdigit() or int(raw) <= 0:
        print(f"error: DESK_MAX_HISTORY_TOKENS must be a positive whole number, got {raw!r}", file=sys.stderr)
        return 2
    session = Session(SYSTEM_PROMPT, int(raw), pin_first=args.pin_first)

    def chat_footer(r):
        return footer(r) + f" | history: ~{count_tokens(session.messages())}/{session.max_tokens} tokens"

    return run_chat(session, pick_model(args), chat_footer, temperature=args.temperature, simulate=args.simulate,
                    client=client, sleep=sleep, input_fn=input_fn)


def build_parser():
    p = argparse.ArgumentParser(prog="desk", description="Desk - Meridian Supply Co. customer-operations assistant")
    sub = p.add_subparsers(dest="cmd", required=True)
    a = sub.add_parser("ask", help="send a single question")
    a.add_argument("question")
    c = sub.add_parser("chat", help="multi-turn streaming chat (/reset /save /load /exit)")
    c.add_argument("--pin-first", action="store_true", help="never drop the first user message from history")
    for s in (a, c):
        s.add_argument("--model", help=f"overrides DESK_MODEL (default {DEFAULT_MODEL})")
        s.add_argument("--temperature", type=temperature, help="sampling temperature 0-2, e.g. 0 or 1")
        s.add_argument("--simulate", choices=["429", "timeout", "network"], help="force a failure to test retry logic without calling the API")
    return p


def main(argv=None):
    from dotenv import load_dotenv

    load_dotenv()
    utf8_output()
    args = build_parser().parse_args(argv)
    sys.exit({"ask": cmd_ask, "chat": cmd_chat}[args.cmd](args))


if __name__ == "__main__":
    main()
