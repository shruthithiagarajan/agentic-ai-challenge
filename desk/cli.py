import argparse
import os
import sys
import time
from pathlib import Path

from .chat import run_chat
from .llm import DEFAULT_MODEL, LLMError, ask, model_for
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


def pick_model(args, role=None):
    """--model > DESK_MODEL_<ROLE> (this command's model in .env) > DESK_MODEL > default."""
    if role:
        return model_for(role, getattr(args, "model", None))
    return args.model or os.getenv("DESK_MODEL") or DEFAULT_MODEL


def temperature_note(args):
    """Azure's GPT reasoning deployments and the current Anthropic SDK take no temperature: say so when one is given."""
    from .llm import provider

    try:
        on_azure = provider() == "azure"
    except LLMError:
        return
    if getattr(args, "temperature", None) is not None and on_azure:
        print("note: --temperature is ignored on Azure - its GPT reasoning and Claude deployments only use their default",
              file=sys.stderr)


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
    model, rewrite_model = pick_model(args, "RAG"), model_for("REWRITE")
    extra = {"sleep": sleep} if sleep else {}
    log = lambda m: print(f"[retry] {m}", file=sys.stderr)  # noqa: E731
    debug = getattr(args, "debug", False)
    temperature_note(args)
    question = args.question
    prev = getattr(args, "prev", None) or []
    if debug:
        print(f"[debug] answer model: {model}" + (f" | rewrite model: {rewrite_model}" if prev else ""), file=sys.stderr)
    if prev and not args.simulate:  # follow-up: make it standalone before searching
        history = [{"role": "user", "content": p} for p in prev]
        question = rag.rewrite_query(args.question, history, lambda system, user: ask(
            user, system, rewrite_model, temperature=0, client=client, log=log, **extra).text)
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

    temperature_note(args)
    return run_chat(session, pick_model(args, "CHAT"), chat_footer, temperature=args.temperature, simulate=args.simulate,
                    client=client, sleep=sleep, input_fn=input_fn)


OUT_DIR = Path("data")  # generated: results.jsonl, needs_review.jsonl (gitignored)


def data_dir():
    """Seed data root (emails/, catalog.csv, ground_truth/). Override with DESK_DATA_DIR."""
    return Path(os.getenv("DESK_DATA_DIR") or "meridian_desk_data")


def cmd_extract(args, client=None, sleep=None):
    from .extract import Extractor, run_batch
    from .llm import make_client

    model = pick_model(args, "EXTRACT")
    try:
        client = client or make_client(timeout=60.0, model=model)
    except LLMError as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    log = lambda m: print(m, file=sys.stderr)  # noqa: E731
    ex = Extractor(client, model, log=log, **({"sleep": sleep} if sleep else {}))
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

    def llm_for(model):
        """One LLM per role; without a key, retrieval-only runs still work (rewrites fall back)."""
        try:
            c = client or make_client(timeout=60.0, model=model)
        except LLMError as e:
            if not args.no_faithfulness:
                raise
            log(f"note: {e} - follow-up rewriting falls back to joining the previous question")
            c = None
        return rag_eval.ChatLLM(c, model, log=log, **({"sleep": sleep} if sleep else {}))

    try:
        rewriter = llm_for(model_for("REWRITE"))
        corpora = corpora or rag_eval.build_corpora(policies)
    except LLMError as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    except ImportError as e:
        print(f"error: {e.name or 'chromadb / rank-bm25'} is not installed - run: pip install -e .", file=sys.stderr)
        return 1
    log(f"retrieval ablation over {len(golden)} questions (follow-ups rewritten by {rewriter.model}) ...")
    rows, rewrites = rag_eval.run_ablation(corpora, golden, rag_eval.fixed_section_map(policies),
                                           rag_eval.effective_dates(policies),
                                           lambda q, history: rag.rewrite_query(q, history, rewriter))
    print(rag_eval.format_ablation(rows, len(golden)))
    for qid, query in rewrites.items():
        print(f"rewritten {qid}: {query}")
    if args.no_faithfulness:
        return 0
    try:
        answerer, judge = llm_for(pick_model(args, "RAG")), llm_for(model_for("JUDGE"))
    except LLMError as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    catalog = root / "catalog.csv" if args.catalog and (root / "catalog.csv").is_file() else None
    log(f"answering {len(golden)} questions with {answerer.model}, judging with {judge.model} ...")
    results = rag_eval.run_faithfulness(corpora["sections"], golden, rewrites, answerer, catalog, log=log, judge=judge)
    print()
    print(rag_eval.format_faithfulness(results, answerer, judge))
    return 0


TOOL_NAMES = ["get_order", "search_catalog", "check_stock", "calculate", "search_policies"]


def make_toolbox(args, retrieve=None):
    """Toolbox over the seed data, or (None, error message) when a data file is missing."""
    from .tools import Toolbox

    root = data_dir()
    db, catalog = root / "orders.db", root / "catalog.csv"
    for path in (db, catalog):
        if not path.is_file():
            return None, f"{path} not found (set DESK_DATA_DIR to the folder that holds orders.db and catalog.csv)"
    return Toolbox(db, catalog, retrieve=retrieve, fail=getattr(args, "fail_tool", None)), None


def cmd_tools(args, client=None, sleep=None, retrieve=None):
    """Challenge 6: answer with one round of tool calls (orders, stock, catalog, calculator, policies)."""
    import json

    from . import rag, tooluse
    from .llm import SimulatedError, make_client

    if not args.question.strip():
        print("error: question is empty - try: desk tools \"Where is order MS-10422?\"", file=sys.stderr)
        return 2
    model = pick_model(args, "TOOLS")
    toolbox, problem = make_toolbox(args, retrieve)
    if problem:
        print(f"error: {problem}", file=sys.stderr)
        return 1
    if args.debug:
        print(f"[debug] model: {model}" + (" | tools: VAGUE check_stock description (stretch)" if args.vague else ""),
              file=sys.stderr)

    def show(call):
        status = "ok" if call.ok else f"error {call.result['error']['code']}"
        print(f"[tool] {call.name}({call.arguments}) -> {status} ({call.ms:.0f} ms)", file=sys.stderr)
        if args.debug or not call.ok:
            print(f"[tool]   {json.dumps(call.result, ensure_ascii=False)[:700]}", file=sys.stderr)

    try:
        client = client if (client or args.simulate) else make_client(timeout=60.0, model=model)
        run = tooluse.answer(args.question, model, client, toolbox, vague=args.vague, sleep=sleep or time.sleep,
                             log=lambda m: print(f"[retry] {m}", file=sys.stderr), on_call=show,
                             simulate_error=SimulatedError(args.simulate) if args.simulate else None)
    except LLMError as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    if run.wanted:
        wanted = ", ".join(f"{name}({arguments})" for name, arguments in run.wanted)
        print(f"[tools] the model asked for a second round - NOT run: {wanted}. Desk does one round of tool calls; "
              f"chained lookups need the agent loop (Challenge 7).", file=sys.stderr)
    print(rag.normalize_citations(run.text) or "(the model returned no answer)", flush=True)
    names = ", ".join(c.name for c in run.calls) or "none"
    print(f"--\ntools run: {names} | LLM calls: {run.llm_calls} | model: {model}")
    print(footer(run).removeprefix("--\n"))
    return 0


def cmd_eval_tools(args, client=None, sleep=None, retrieve=None):
    """Stretch: does the model pick the expected tools for the 15 prompts in chat/tool_test_prompts.json?"""
    import json

    from . import tooluse
    from .llm import make_client

    path = data_dir() / "chat" / "tool_test_prompts.json"
    if not path.is_file():
        print(f"error: {path} not found (set DESK_DATA_DIR)", file=sys.stderr)
        return 1
    prompts = json.loads(path.read_text(encoding="utf-8"))[:args.limit or None]
    model = pick_model(args, "TOOLS")
    toolbox, problem = make_toolbox(args, retrieve)
    if problem:
        print(f"error: {problem}", file=sys.stderr)
        return 1
    try:
        client = client or make_client(timeout=60.0, model=model)
    except LLMError as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    label = "VAGUE check_stock description" if args.vague else "normal descriptions"
    print(f"Tool selection on {len(prompts)} prompts ({model}, {label})", flush=True)
    passed = matched = expected_total = extra = llm_calls = tin = tout = 0
    for i, item in enumerate(prompts, 1):
        expected = item.get("expected_tools", [])
        try:
            run = tooluse.answer(item["prompt"], model, client, toolbox, vague=args.vague, sleep=sleep or time.sleep)
            chosen = tooluse.selected_tools(run)
            llm_calls, tin, tout = llm_calls + run.llm_calls, tin + run.input_tokens, tout + run.output_tokens
        except LLMError as e:
            chosen, run = [f"LLM error: {e}"], None
        ok, m, x = tooluse.score_selection(expected, chosen) if run else (False, 0, 0)
        passed, matched, expected_total, extra = passed + ok, matched + m, expected_total + len(expected), extra + x
        print(f"{i:>2} {'PASS' if ok else 'FAIL'}  expected [{', '.join(expected) or 'no tool'}] -> chose "
              f"[{', '.join(chosen) or 'no tool'}]  {item['prompt'][:60]}", flush=True)
    n = len(prompts) or 1
    print(f"passed: {passed}/{len(prompts)} ({100 * passed / n:.0f}%) | expected tools chosen: {matched}/{expected_total}"
          f" | extra tool calls: {extra} | LLM calls: {llm_calls} | tokens: in {tin} / out {tout}")
    print("chose = the round that ran + any second round the model asked for (not run)")
    return 0


AGENT_PRESETS = {"apex": "apex_task", "impossible": "impossible_task", "repeat": "repeat_trap"}


def pricing(env=os.environ):
    """(USD per 1M input tokens, per 1M output tokens) from DESK_PRICE_IN / DESK_PRICE_OUT, else None."""
    try:
        return float(env["DESK_PRICE_IN"]), float(env["DESK_PRICE_OUT"])
    except (KeyError, ValueError):
        return None


def _prompt_args(pairs):
    out = {}
    for pair in pairs or []:
        key, sep, value = pair.partition("=")
        if not sep or not key.strip():
            raise ValueError(f"--arg must look like name=value, got {pair!r}")
        out[key.strip()] = value
    return out


def open_toolbox(args, retrieve=None, mcp_factory=None):
    """(context manager giving the agent's toolbox, error message). Ch8 default: MCP servers from the config file;
    --local: the Ch7 in-process Toolbox."""
    import contextlib

    if getattr(args, "local", False):
        toolbox, problem = make_toolbox(args, retrieve)
        if toolbox is not None and os.getenv("DESK_OUTBOX"):
            toolbox.outbox = Path(os.environ["DESK_OUTBOX"])
        return (contextlib.nullcontext(toolbox) if toolbox else None), problem
    from .mcp_client import MCPConfigError, MCPToolbox, load_config

    try:
        configs = load_config(args.servers, args.only)
    except MCPConfigError as e:
        return None, f"{e} (or run with --local for the Challenge 7 in-process tools)"
    if not configs:
        return None, f"no enabled servers in {args.servers} - enable one or pick servers with --only NAME"
    return MCPToolbox(configs, plan=getattr(args, "plan", False), client_factory=mcp_factory,
                      log=lambda m: print(m, file=sys.stderr)), None


def cmd_agent(args, client=None, sleep=None, retrieve=None, clock=None, mcp_factory=None):
    """Challenge 7 loop; Challenge 8: its tools come from MCP servers (mcp_servers.json) unless --local."""
    import json

    from . import agent, rag
    from .llm import SimulatedError, make_client

    task = (args.task or "").strip()
    if args.preset:
        path = data_dir() / "chat" / "c07_agent_tasks.json"
        if not path.is_file():
            print(f"error: {path} not found (set DESK_DATA_DIR)", file=sys.stderr)
            return 1
        task = json.loads(path.read_text(encoding="utf-8"))[AGENT_PRESETS[args.preset]]
    if not task and not args.prompt:
        print("error: task is empty - try: desk agent \"Where is order MS-10388 and is it all in stock?\", "
              "desk agent --preset apex or desk agent --prompt delay-apology --arg order_id=MS-10388", file=sys.stderr)
        return 2
    try:
        prompt_args = _prompt_args(args.arg)
    except ValueError as e:
        print(f"error: {e}", file=sys.stderr)
        return 2
    model = pick_model(args, "AGENT")
    opened, problem = open_toolbox(args, retrieve, mcp_factory)
    if problem:
        print(f"error: {problem}", file=sys.stderr)
        return 1
    budget = agent.Budget(max_steps=args.max_steps, max_tokens=args.max_tokens, max_cost_usd=args.max_cost,
                          wall_clock_s=args.timeout)
    prices = pricing()

    def show(event):
        print(agent.format_event(event), file=sys.stderr, flush=True)
        if args.show_results and event["event"] == "tool":
            print(f"            {event['result_preview']}", file=sys.stderr)

    with opened as toolbox:
        if not args.local:
            for line in toolbox.summary():
                print(f"[mcp] {line}", file=sys.stderr)
            if not toolbox.servers:
                print("error: no MCP server could be reached - see the [mcp] lines above", file=sys.stderr)
                return 1
            print(f"[mcp] {len(toolbox.routes)} tools discovered | tool definitions ~{toolbox.definition_tokens():,} "
                  "tokens on every model call", file=sys.stderr)
        if args.prompt:
            if args.local:
                print("error: --prompt needs MCP servers (drop --local)", file=sys.stderr)
                return 2
            try:
                task = toolbox.get_prompt(args.prompt, prompt_args)
            except Exception as e:  # noqa: BLE001 - unknown prompt / missing argument
                print(f"error: prompt {args.prompt!r}: {e}", file=sys.stderr)
                return 1
        print(f"[agent] task: {task}", file=sys.stderr)
        try:
            client = client if (client or args.simulate) else make_client(timeout=90.0, model=model)
            run = agent.run_agent(task, model, client, toolbox, budget=budget, plan=args.plan, pricing=prices,
                                  sleep=sleep or time.sleep, log=lambda m: print(f"[retry] {m}", file=sys.stderr),
                                  on_event=show, simulate_error=SimulatedError(args.simulate) if args.simulate else None,
                                  **({"clock": clock} if clock else {}))
        except LLMError as e:
            print(f"error: {e}", file=sys.stderr)
            return 1
    print(rag.normalize_citations(run.answer) or "(the model returned no final text)", flush=True)
    print("--")
    print(f"stop: {run.stop_reason} | steps: {run.steps} | tool calls: {run.tool_calls} | LLM calls: {run.llm_calls}"
          f" | model: {model} | tools: {'local (Ch7)' if args.local else 'MCP'}" + (" | plan-first" if args.plan else ""))
    if run.drafts:
        where = toolbox.outbox if args.local else Path(run.drafts[0]).parent
        print(f"drafts saved (not sent): {len(run.drafts)} in {where}")
    if run.trace_file:
        print(f"trace: {run.trace_file}")
    print(footer(run).removeprefix("--\n"))
    return 0 if run.finished else 3


def cmd_mcp(args, mcp_factory=None):
    """desk mcp list | call: inspect MCP servers and call one tool by hand (no LLM)."""
    import json

    args.local, args.plan = False, False
    opened, problem = open_toolbox(args, mcp_factory=mcp_factory)
    if problem:
        print(f"error: {problem}", file=sys.stderr)
        return 1
    with opened as toolbox:
        if args.action == "list":
            for name, server in toolbox.servers.items():
                print(f"== {name} ({server['transport']})" + (f" - {server['instructions'][:110]}..."
                                                              if server["instructions"] else ""))
                for t in server["tools"]:
                    a = t.annotations
                    hints = [h for h, on in (("read-only", a and a.read_only_hint), ("destructive", a and a.destructive_hint),
                                             ("idempotent", a and a.idempotent_hint)) if on]
                    print(f"   tool     {t.name}" + (f"  [{', '.join(hints)}]" if hints else ""))
                for r in server["resources"]:
                    print(f"   resource {r.uri}  {r.title or r.name}")
                for pr in server["prompts"]:
                    print(f"   prompt   {pr.name}({', '.join(x.name for x in pr.arguments or [])})")
            for name, why in toolbox.failed.items():
                print(f"== {name}: NOT CONNECTED - {why}")
            print(f"total: {len(toolbox.routes)} tools from {len(toolbox.servers)} server(s); tool definitions "
                  f"~{toolbox.definition_tokens():,} tokens sent before the user has typed anything")
            return 0 if toolbox.servers else 1
        if not toolbox.servers:
            print("error: no MCP server could be reached", file=sys.stderr)
            return 1
        if not args.tool:
            print("error: desk mcp call needs a tool name, e.g. desk mcp call check_stock '{\"sku\": \"MS-VLV0200\"}'",
                  file=sys.stderr)
            return 2
        result = toolbox.run(args.tool, args.arguments or "{}")
        print(json.dumps(result, indent=2, ensure_ascii=False))
        return 0 if result["ok"] else 1


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
        s.add_argument("--model", help="overrides this command's model in .env (DESK_MODEL_RAG for ask, DESK_MODEL_CHAT for chat)")
        s.add_argument("--temperature", type=temperature, help="sampling temperature 0-2, e.g. 0 or 1")
        s.add_argument("--simulate", choices=["429", "timeout", "network"], help="force a failure to test retry logic without calling the API")
    e = sub.add_parser("extract", help="extract an OrderRequest from every email into data/results.jsonl")
    e.add_argument("--model", help="overrides DESK_MODEL_EXTRACT (set in .env)")
    sub.add_parser("ingest", help="chunk the policy docs by heading into the local Chroma index (safe to re-run)")
    ev = sub.add_parser("eval-rag", help="Recall@5 / MRR ablation + LLM-judged faithfulness on the golden questions")
    ev.add_argument("--model", help="answer model; overrides DESK_MODEL_RAG (judge: DESK_MODEL_JUDGE, rewrites: DESK_MODEL_REWRITE)")
    ev.add_argument("--limit", type=int, help="only the first N golden questions (quick runs)")
    ev.add_argument("--no-faithfulness", action="store_true", help="retrieval metrics only (no answer/judge calls)")
    ev.add_argument("--catalog", action="store_true", help="give the model catalog.csv rows for SKUs, as desk ask --catalog")
    sub.add_parser("accuracy", help="field-level accuracy of data/results.jsonl against the 15 hand labels")
    t = sub.add_parser("tools", help="answer with tools: live orders, stock, catalog, calculator, policies "
                                     "(one round of tool calls)")
    t.add_argument("question")
    t.add_argument("--debug", action="store_true", help="print every tool result sent back to the model")
    t.add_argument("--fail-tool", action="append", choices=TOOL_NAMES, metavar="TOOL",
                   help="make this tool crash, to see the failure go back to the model as a structured error "
                        f"(repeatable; one of {', '.join(TOOL_NAMES)})")
    t.add_argument("--simulate", choices=["429", "timeout", "network"], help="force an LLM failure to test retries")
    et = sub.add_parser("eval-tools", help="tool-selection check on chat/tool_test_prompts.json (15 prompts)")
    et.add_argument("--limit", type=int, help="only the first N prompts")
    for s in (t, et):
        s.add_argument("--model", help="overrides DESK_MODEL_TOOLS (set in .env)")
        s.add_argument("--vague", action="store_true",
                       help="stretch: give check_stock a deliberately vague description and watch tool choice degrade")
    ag = sub.add_parser("agent", help="Challenge 7 agent loop: the model calls tools until it is finished or a "
                                      "limit stops it (exit 3 = stopped early)")
    ag.add_argument("task", nargs="?", help="what to do (or use --preset)")
    ag.add_argument("--preset", choices=list(AGENT_PRESETS),
                    help="a task from chat/c07_agent_tasks.json: apex, impossible or repeat")
    ag.add_argument("--model", help="overrides DESK_MODEL_AGENT (set in .env)")
    ag.add_argument("--plan", action="store_true", help="stretch: add the todo tool and make the agent plan first")
    ag.add_argument("--max-steps", type=int, default=25, help="model turns before stopping (default 25)")
    ag.add_argument("--max-tokens", type=int, default=500_000, help="input+output token budget (default 500000)")
    ag.add_argument("--max-cost", type=float, default=0.50,
                    help="USD budget (default 0.50; enforced only when DESK_PRICE_IN/OUT are set)")
    ag.add_argument("--timeout", type=float, default=300.0, help="wall-clock seconds (default 300)")
    ag.add_argument("--show-results", action="store_true", help="also print each tool result in the trace")
    ag.add_argument("--fail-tool", action="append", choices=TOOL_NAMES + ["list_orders", "draft_email"], metavar="TOOL",
                    help="make this tool crash, to see the agent cope with a failure (repeatable)")
    ag.add_argument("--simulate", choices=["429", "timeout", "network"], help="force an LLM failure to test retries")
    ag.add_argument("--local", action="store_true",
                    help="Challenge 7 mode: in-process tools instead of MCP servers (needed for --fail-tool)")
    ag.add_argument("--prompt", help="use a server prompt as the task, e.g. delay-apology (with --arg name=value)")
    ag.add_argument("--arg", action="append", metavar="NAME=VALUE", help="argument for --prompt (repeatable)")
    mc = sub.add_parser("mcp", help="Challenge 8: list the tools/resources/prompts of the MCP servers, or call one tool")
    mc.add_argument("action", choices=["list", "call"])
    mc.add_argument("tool", nargs="?", help="tool to call (desk mcp call TOOL '{json}')")
    mc.add_argument("arguments", nargs="?", help="JSON arguments for the tool")
    for s in (ag, mc):
        s.add_argument("--servers", default="mcp_servers.json", help="MCP server config file (default mcp_servers.json)")
        s.add_argument("--only", action="append", metavar="NAME",
                       help="connect only to this server from the config, even if disabled (repeatable)")
    return p


def main(argv=None):
    from dotenv import load_dotenv

    load_dotenv()
    utf8_output()
    args = build_parser().parse_args(argv)
    sys.exit({"ask": cmd_ask, "chat": cmd_chat, "ingest": cmd_ingest, "eval-rag": cmd_eval_rag, "extract": cmd_extract,
              "accuracy": cmd_accuracy, "tools": cmd_tools, "eval-tools": cmd_eval_tools, "agent": cmd_agent,
              "mcp": cmd_mcp}[args.cmd](args))


if __name__ == "__main__":
    main()
