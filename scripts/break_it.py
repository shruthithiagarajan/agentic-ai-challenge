"""Run the Challenge 1 'Break it' + stretch experiments live and print Markdown for NOTES.md.

Usage:  python scripts/break_it.py [--write]     (needs GROQ_API_KEY; model from DESK_MODEL)
--write appends the results to NOTES.md.
"""
import argparse
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from desk.cli import estimate_cost, utf8_output  # noqa: E402
from dotenv import load_dotenv  # noqa: E402
from desk.llm import DEFAULT_MODEL, LLMError, ask  # noqa: E402
from desk.prompts import SYSTEM_PROMPT  # noqa: E402


def run(q, model, temperature=None):
    r = ask(q, SYSTEM_PROMPT, model, temperature=temperature)
    cost = estimate_cost(r.input_tokens, r.output_tokens)
    return r, (f"${cost:.6f}" if cost is not None else "n/a")


def block(title, q, r, cost):
    return f"**{title}**\n\nQ: {q[:160]}{'...' if len(q) > 160 else ''}\n\n> " + r.text.strip().replace("\n", "\n> ") + f"\n\n`in {r.input_tokens} / out {r.output_tokens} tokens, {r.latency_s:.2f}s, {cost}`\n"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--write", action="store_true")
    args = ap.parse_args()
    load_dotenv()
    model = os.environ.get("DESK_MODEL") or DEFAULT_MODEL
    if not os.environ.get("GROQ_API_KEY"):
        sys.exit("Set GROQ_API_KEY first (see .env.example).")
    out = ["\n## Challenge 1 - live Break it run\n"]
    try:
        q1, q2 = "My name is Ravi", "What is my name?"
        r1, c1 = run(q1, model)
        r2, c2 = run(q2, model)
        out += ["### 1. No memory between calls\n", block("Call 1", q1, r1, c1), block("Call 2 (separate call)", q2, r2, c2)]
        long_q = "I run a fabrication unit. " + " ".join(["We use brass and stainless valves on water, mild-chemical and compressed-air lines and want the trade-offs."] * 40) + " Summarise all of the above in five bullets."
        r3, c3 = run(long_q, model)
        out += ["### 2. Long question - blank-terminal wait\n", f"The terminal shows nothing until the whole reply is done: **{r3.latency_s:.2f}s** of blank screen for a {len(long_q.split())}-word question.\n", block("Reply", long_q, r3, c3)]
        q4 = "What is Meridian's return window for opened items?"
        r4, c4 = run(q4, model)
        out += ["### 3. Invented policy\n", block("Answer (the model has never seen Meridian's policies)", q4, r4, c4),
                "Truth per `data/policies`: 7 days and 20% restocking fee (returns_opened_items.md, effective 2026-03-01); the older returns.md says 14 days / 10%.\n"]
        out.append("### Stretch - same question x5 at temperature 0 vs 1\n")
        sq = "Suggest a name for a new brand of brass ball valve."
        for t in (0, 1):
            answers = [run(sq, model, t)[0].text.strip() for _ in range(5)]
            out.append(f"- temperature {t}: **{len(set(answers))} distinct answers out of 5**\n" + "".join(f"  - {a[:100]!r}\n" for a in answers))
    except LLMError as e:
        sys.exit(f"error: {e}")
    text = "\n".join(out)
    if args.write:  # write first: results are saved even if printing fails
        with open("NOTES.md", "a", encoding="utf-8") as f:
            f.write(text + "\n")
    utf8_output()
    print(text)
    if args.write:
        print("\n(appended to NOTES.md)")


if __name__ == "__main__":
    main()
