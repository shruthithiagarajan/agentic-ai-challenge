import argparse
import os
import sys
import time
from pathlib import Path

from .chat import run_chat
from .llm import DEFAULT_MODEL, LLMError, ask
from .prompts import RAG_SYSTEM_PROMPT, SYSTEM_PROMPT
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


def default_retrieve(question):
    """Challenge 5 pipeline over the .chroma index: BM25 + dense hybrid, rerank, latest-effective-date preference."""
    from . import rag

    corpus = rag.Corpus(rag.open_collection())
    if corpus.is_stale():
        print("warning: the index was built by an older `desk ingest` - run `desk ingest` again", file=sys.stderr)
    return rag.search(corpus, question)


def cmd_ask(args, sleep=None, client=None, retrieve=None):
    """Answer only from retrieved policy chunks, with [file.md#N] citations."""
    from . import rag

    if not args.question.strip():
        print("error: question is empty - try: desk ask \"your question\"", file=sys.stderr)
        return 2
    model = pick_model(args)
    extra = {"sleep": sleep} if sleep else {}
    log = lambda m: print(f"[retry] {m}", file=sys.stderr)  # noqa: E731
    debug = getattr(args, "debug", False)
    question = args.question
    prev = getattr(args, "prev", None) or []
    if prev and not args.simulate:  # follow-up: make it standalone before searching
        history = [{"role": "user", "content": p} for p in prev]
        question = rag.rewrite_query(args.question, history, lambda system, user: ask(
            user, system, model, temperature=0, client=client, log=log, **extra).text)
        if debug:
            print(f"[debug] follow-up rewritten to: {question}", file=sys.stderr)
    try:
        t0 = time.perf_counter()
        hits = (retrieve or default_retrieve)(question)
        retrieval_s = time.perf_counter() - t0
    except ImportError as e:
        print(f"error: {e.name or 'chromadb / rank-bm25'} is not installed - run: pip install -e .", file=sys.stderr)
        return 1
    if not hits:
        print("error: no policy documents are indexed - run `desk ingest` first", file=sys.stderr)
        return 1
    if getattr(args, "catalog", False):
        catalog = data_dir() / "catalog.csv"
        if catalog.is_file():
            hits = hits + rag.catalog_hits(f"{args.question} {question}", catalog)
        else:
            print(f"warning: {catalog} not found - --catalog ignored", file=sys.stderr)
    if debug:
        print(f"[debug] top {len(hits)} chunks ({retrieval_s:.2f}s):", file=sys.stderr)
        for h in hits:
            print(f"[debug]   {rag.debug_line(h)}", file=sys.stderr)
    try:
        r = ask(rag.build_context(question, hits), RAG_SYSTEM_PROMPT, model, temperature=args.temperature,
                simulate=args.simulate, client=client, log=log, **extra)
    except LLMError as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    answer = rag.normalize_citations(r.text)
    print(answer, flush=True)  # keep the answer above any warning when stdout is piped
    for problem in rag.citation_problems(answer, hits):
        print(f"[citations] warning: {problem}", file=sys.stderr)
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


def cmd_ingest(args, collection=None):
    from . import rag

    src = data_dir() / "policies"
    try:
        collection = collection or rag.open_collection()
        s = rag.ingest(collection, src, loader=rag.load_sections)
    except ImportError:
        print("error: chromadb is not installed - run: pip install -e .", file=sys.stderr)
        return 1
    except FileNotFoundError as e:
        print(f"error: {e} (set DESK_DATA_DIR to the folder that holds policies/)", file=sys.stderr)
        return 1
    print(f"loaded {s['files']} files from {src}")
    print(f"stored {s['chunks']} chunks ({s['new']} new, {s['removed']} stale removed); "
          f"collection '{rag.COLLECTION}' in {rag.CHROMA_DIR}/ now holds {s['total']} chunks")
    return 0


def cmd_eval_rag(args, client=None, sleep=None, corpora=None):
    """Challenge 5: retrieval ablation (Recall@5, MRR) + LLM-judged faithfulness on the golden questions."""
    from . import rag, rag_eval
    from .llm import make_client

    root = data_dir()
    golden_path, policies = root / "evals" / "golden_retrieval.jsonl", root / "policies"
    for path in (golden_path, policies):
        if not path.exists():
            print(f"error: {path} not found (set DESK_DATA_DIR)", file=sys.stderr)
            return 1
    golden = rag_eval.load_golden(golden_path, args.limit)
    log = lambda m: print(m, file=sys.stderr)  # noqa: E731
    if client is None:
        try:
            client = make_client(timeout=60.0)
        except LLMError as e:
            if not args.no_faithfulness:
                print(f"error: {e}", file=sys.stderr)
                return 1
            log(f"note: {e} - follow-up rewriting falls back to joining the previous question")
    llm = rag_eval.GroqLLM(client, pick_model(args), log=log, **({"sleep": sleep} if sleep else {}))
    try:
        corpora = corpora or rag_eval.build_corpora(policies)
    except ImportError as e:
        print(f"error: {e.name or 'chromadb / rank-bm25'} is not installed - run: pip install -e .", file=sys.stderr)
        return 1
    log(f"retrieval ablation over {len(golden)} questions ...")
    rows, rewrites = rag_eval.run_ablation(corpora, golden, rag_eval.fixed_section_map(policies),
                                           rag_eval.effective_dates(policies),
                                           lambda q, history: rag.rewrite_query(q, history, llm))
    print(rag_eval.format_ablation(rows, len(golden)))
    for qid, query in rewrites.items():
        print(f"rewritten {qid}: {query}")
    if args.no_faithfulness:
        return 0
    catalog = root / "catalog.csv" if args.catalog and (root / "catalog.csv").is_file() else None
    log(f"answering + judging {len(golden)} questions with {llm.model} (slow on the free tier) ...")
    results = rag_eval.run_faithfulness(corpora["sections"], golden, rewrites, llm, catalog, log=log)
    print()
    print(rag_eval.format_faithfulness(results, llm))
    return 0


def build_parser():
    p = argparse.ArgumentParser(prog="desk", description="Desk - Meridian Supply Co. customer-operations assistant")
    sub = p.add_subparsers(dest="cmd", required=True)
    a = sub.add_parser("ask", help="answer a question from the policy documents, with citations")
    a.add_argument("question")
    a.add_argument("--debug", action="store_true", help="print the retrieved chunk ids, scores and snippets")
    a.add_argument("--prev", action="append", metavar="EARLIER_QUESTION",
                   help="an earlier question in this conversation; a follow-up like 'and for 30,000?' is rewritten "
                        "into a standalone query before searching (repeatable)")
    a.add_argument("--catalog", action="store_true", help="also give the model the catalog.csv rows of any SKU named")
    c = sub.add_parser("chat", help="multi-turn streaming chat (/reset /save /load /exit)")
    c.add_argument("--pin-first", action="store_true", help="never drop the first user message from history")
    for s in (a, c):
        s.add_argument("--model", help=f"overrides DESK_MODEL (default {DEFAULT_MODEL})")
        s.add_argument("--temperature", type=temperature, help="sampling temperature 0-2, e.g. 0 or 1")
        s.add_argument("--simulate", choices=["429", "timeout", "network"], help="force a failure to test retry logic without calling the API")
    e = sub.add_parser("extract", help="extract an OrderRequest from every email into data/results.jsonl")
    e.add_argument("--model", help=f"overrides DESK_MODEL (default {DEFAULT_MODEL})")
    sub.add_parser("ingest", help="chunk the policy docs by heading into the local Chroma index (safe to re-run)")
    ev = sub.add_parser("eval-rag", help="Recall@5 / MRR ablation + LLM-judged faithfulness on the golden questions")
    ev.add_argument("--model", help=f"answer + judge model; overrides DESK_MODEL (default {DEFAULT_MODEL})")
    ev.add_argument("--limit", type=int, help="only the first N golden questions (quick runs)")
    ev.add_argument("--no-faithfulness", action="store_true", help="retrieval metrics only (no answer/judge calls)")
    ev.add_argument("--catalog", action="store_true", help="give the model catalog.csv rows for SKUs, as desk ask --catalog")
    sub.add_parser("accuracy", help="field-level accuracy of data/results.jsonl against the 15 hand labels")
    return p


def main(argv=None):
    from dotenv import load_dotenv

    load_dotenv()
    utf8_output()
    args = build_parser().parse_args(argv)
    sys.exit({"ask": cmd_ask, "chat": cmd_chat, "ingest": cmd_ingest, "eval-rag": cmd_eval_rag, "extract": cmd_extract,
              "accuracy": cmd_accuracy}[args.cmd](args))


if __name__ == "__main__":
    main()
