import argparse
import os
import sys

from .llm import DEFAULT_MODEL, LLMError, ask
from .prompts import SYSTEM_PROMPT


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
    return f"--\ninput tokens: {r.input_tokens} | output tokens: {r.output_tokens} | latency: {r.latency_s:.2f}s | est. cost: {cost_s}"


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
    model = args.model or os.getenv("DESK_MODEL") or DEFAULT_MODEL
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


def build_parser():
    p = argparse.ArgumentParser(prog="desk", description="Desk - Meridian Supply Co. customer-operations assistant")
    sub = p.add_subparsers(dest="cmd", required=True)
    a = sub.add_parser("ask", help="send a single question")
    a.add_argument("question")
    a.add_argument("--model", help=f"overrides DESK_MODEL (default {DEFAULT_MODEL})")
    a.add_argument("--temperature", type=temperature, help="sampling temperature 0-2, e.g. 0 or 1")
    a.add_argument("--simulate", choices=["429", "timeout", "network"], help="force a failure to test retry logic without calling the API")
    return p


def main(argv=None):
    from dotenv import load_dotenv

    load_dotenv()
    utf8_output()
    args = build_parser().parse_args(argv)
    sys.exit(cmd_ask(args))


if __name__ == "__main__":
    main()
