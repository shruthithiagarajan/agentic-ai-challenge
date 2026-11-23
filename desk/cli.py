import argparse
import os
import sys
from pathlib import Path

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


OUT_DIR = Path("data")  # generated: results.jsonl, needs_review.jsonl (gitignored)


def data_dir():
    """Seed data root (emails/, catalog.csv, ground_truth/). Override with DESK_DATA_DIR."""
    return Path(os.getenv("DESK_DATA_DIR") or "meridian_desk_data")


def cmd_extract(args, client=None, sleep=None):
    from .extract import Extractor, run_batch
    from .llm import make_client

    try:
        client = client or make_client(timeout=60.0)
    except LLMError as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    log = lambda m: print(m, file=sys.stderr)  # noqa: E731
    ex = Extractor(client, pick_model(args), log=log, **({"sleep": sleep} if sleep else {}))
    try:
        s = run_batch(ex, data_dir() / "emails", OUT_DIR, data_dir() / "catalog.csv", log=log)
    except (FileNotFoundError, OSError) as e:
        print(f"error: {e} (set DESK_DATA_DIR to the folder with emails/ and catalog.csv)", file=sys.stderr)
        return 1
    n = s["total"]
    cost = estimate_cost(s["input_tokens"], s["output_tokens"])
    cost_s = f"${cost:.6f} total, ${cost / n:.6f}/email" if cost is not None else "n/a (set DESK_PRICE_IN / DESK_PRICE_OUT)"
    print(f"model: {ex.model} ({ex.mode} mode)")
    print(f"validated: {s['validated']}/{n} (repaired: {s['repaired']}) | needs_review: {s['needs_review']}/{n}")
    print(f"tokens: in {s['input_tokens']} / out {s['output_tokens']} ({(s['input_tokens'] + s['output_tokens']) / n:.0f}/email)"
          f" | latency: {s['latency_s']:.1f}s ({s['latency_s'] / n:.2f}s/email) | est. cost: {cost_s}")
    print(f"SKUs not in catalog.csv: {len(s['sku_not_in_catalog'])} {s['sku_not_in_catalog'] or ''}".rstrip())
    print(f"SKUs not written in the email: {len(s['sku_not_in_email'])} {s['sku_not_in_email'] or ''}".rstrip())
    print(f"wrote {s['results']}" + (f" and {s['review']}" if s["review"] else ""))
    return 0


def cmd_accuracy(args):
    from .extract import accuracy, format_report

    results, labels = OUT_DIR / "results.jsonl", data_dir() / "ground_truth" / "email_labels.json"
    for p, hint in ((results, "run `desk extract` first"), (labels, "set DESK_DATA_DIR")):
        if not p.is_file():
            print(f"error: {p} not found - {hint}", file=sys.stderr)
            return 1
    print(format_report(accuracy(results, labels)))
    return 0


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
    e = sub.add_parser("extract", help="extract an OrderRequest from every email into data/results.jsonl")
    e.add_argument("--model", help=f"overrides DESK_MODEL (default {DEFAULT_MODEL})")
    sub.add_parser("accuracy", help="field-level accuracy of data/results.jsonl against the 15 hand labels")
    return p


def main(argv=None):
    from dotenv import load_dotenv

    load_dotenv()
    utf8_output()
    args = build_parser().parse_args(argv)
    sys.exit({"ask": cmd_ask, "chat": cmd_chat, "extract": cmd_extract, "accuracy": cmd_accuracy}[args.cmd](args))


if __name__ == "__main__":
    main()
