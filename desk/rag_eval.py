"""Challenge 5: measure the RAG pipeline on the golden question set (evals/golden_retrieval.jsonl).

Retrieval: Recall@5 and MRR@5 for each step of an ablation, from the Ch4 baseline to the full Ch5 pipeline,
scored against the golden '<file>#<section number>' ids. Generation: an LLM judge grades every full-pipeline
answer for faithfulness to the retrieved context and for agreement with the reference answer.
The reference answers only ever go to the judge, never to the answering model.
"""
import json
import re
import time
from dataclasses import dataclass
from pathlib import Path

from . import rag
from .extract import BATCH_RETRY
from .llm import LLMError, call_with_retry
from .prompts import JUDGE_PROMPT, RAG_SYSTEM_PROMPT

K = rag.FINAL_K
_DENSE_ONLY = rag.Pipeline(hybrid=False, rerank=False, recency=False)
# (label, which corpus, retrieval pipeline, rewrite follow-ups?) - each step adds one technique.
STEPS = [
    ("Ch4 baseline: fixed 2000-char chunks, dense", "fixed", _DENSE_ONLY, False),
    ("+ heading-aware chunks with headers", "sections", _DENSE_ONLY, False),
    ("+ BM25 hybrid (RRF)", "sections", rag.Pipeline(rerank=False, recency=False), False),
    ("+ rerank top-20 -> 5", "sections", rag.Pipeline(recency=False), False),
    ("+ query rewrite for follow-ups", "sections", rag.Pipeline(recency=False), True),
    ("+ latest effective_date preference", "sections", rag.Pipeline(), True),
]


def load_golden(path, limit=None):
    rows = [json.loads(line) for line in Path(path).read_text(encoding="utf-8").splitlines() if line.strip()]
    return rows[:limit] if limit else rows


def section_of(chunk_id):
    """'returns.md#3-2' (part 2 of section 3) -> 'returns.md#3'."""
    name, _, rest = chunk_id.partition("#")
    return f"{name}#{rest.split('-')[0]}"


def fixed_chunk_sections(text, filename):
    """For the Ch4 fixed-size chunks of one file: the set of '## N.' sections each chunk overlaps."""
    text = text.strip()
    marks = [(m.start(), m.group(1)) for m in rag.HEADING.finditer(text)]
    step = rag.CHUNK_CHARS - rag.OVERLAP_CHARS
    out = []
    for i, _ in enumerate(rag.chunk_text(text)):
        start, end = i * step, i * step + rag.CHUNK_CHARS
        active = [num for pos, num in marks if pos <= start]
        nums = set(active[-1:] or ["0"]) | {num for pos, num in marks if start < pos < end}
        out.append({f"{filename}#{n}" for n in nums})
    return out


def fixed_section_map(policy_dir):
    """{'returns.md#1': {'returns.md#1', ..., 'returns.md#6'}, ...} for the Ch4 baseline chunks."""
    return {f"{f.name}#{i}": secs for f in rag.policy_files(policy_dir)
            for i, secs in enumerate(fixed_chunk_sections(f.read_text(encoding="utf-8"), f.name), 1)}


def effective_dates(policy_dir):
    return {f.name: rag.parse_front_matter(f.read_text(encoding="utf-8"))[0].get("effective_date", "")
            for f in rag.policy_files(policy_dir)}


def recall_at_k(ranked, expected, k=K):
    """ranked: one set of section ids per retrieved chunk, best first. Share of expected sections in the top k."""
    found = set().union(*ranked[:k])
    return len(found & set(expected)) / len(expected)


def reciprocal_rank(ranked, expected, k=K):
    """1 / rank of the first top-k chunk that covers an expected section, else 0."""
    for rank, secs in enumerate(ranked[:k], 1):
        if secs & set(expected):
            return 1.0 / rank
    return 0.0


def newer_first(ranked, expected, dates, k=K):
    """For contradiction questions: is the first relevant chunk from the most recently effective document?"""
    latest = max((section_of(s).split("#")[0] for s in expected), key=lambda name: dates.get(name, ""))
    for secs in ranked[:k]:
        if secs & set(expected):
            return any(s.startswith(latest + "#") for s in secs)
    return False


@dataclass
class Row:
    step: str
    recall: float
    mrr: float
    followup: tuple  # (questions whose expected sections were all found, follow-up questions)
    newer: tuple  # (contradiction questions with the newer doc ranked first, contradiction questions)
    ctx_tokens: float  # average context size sent to the model (~chars/4)


def build_corpora(policy_dir, chroma_client=None):
    """In-memory Chroma collections for both chunkings, so the eval never touches the .chroma/ index."""
    import chromadb
    from chromadb.config import Settings

    client = chroma_client or chromadb.EphemeralClient(settings=Settings(anonymized_telemetry=False))
    corpora = {}
    for key, loader in (("fixed", rag.load_chunks), ("sections", rag.load_sections)):
        name = f"eval_{key}"
        try:
            client.delete_collection(name)
        except Exception:  # noqa: BLE001 - absent on first run; chromadb's error type differs by version
            pass
        col = client.create_collection(name, configuration={"hnsw": {"space": "cosine"}})
        rag.ingest(col, policy_dir, loader=loader)
        corpora[key] = rag.Corpus(col)
    return corpora


def run_ablation(corpora, golden, fixed_map, dates, rewrite, steps=STEPS):
    """rewrite(question, chat_history) -> standalone query (called once per follow-up question)."""
    rewrites, rows = {}, []
    for label, key, pipeline, use_rewrite in steps:
        recalls, rrs, follow, newer, ctx = [], [], [0, 0], [0, 0], []
        for item in golden:
            query = item["question"]
            if use_rewrite and item.get("chat_history"):
                if item["id"] not in rewrites:
                    rewrites[item["id"]] = rewrite(item["question"], item["chat_history"])
                query = rewrites[item["id"]]
            hits = rag.search(corpora[key], query, pipeline)
            ranked = [fixed_map.get(h.id, set()) if key == "fixed" else {section_of(h.id)} for h in hits]
            expected = item["expected_sections"]
            recalls.append(recall_at_k(ranked, expected))
            rrs.append(reciprocal_rank(ranked, expected))
            ctx.append(sum(len(h.text) for h in hits) / 4)
            if item.get("chat_history"):
                follow[1] += 1
                follow[0] += recall_at_k(ranked, expected) == 1.0
            if item.get("must_cite_newer_doc"):
                newer[1] += 1
                newer[0] += newer_first(ranked, expected, dates)
        n = len(golden) or 1
        rows.append(Row(label, sum(recalls) / n, sum(rrs) / n, tuple(follow), tuple(newer), sum(ctx) / n))
    return rows, rewrites


def format_ablation(rows, n):
    head = f"{'step':<46}{'Recall@5':>9}{'MRR@5':>8}{'follow-ups':>12}{'newer-first':>13}{'ctx tokens':>12}"
    lines = [f"Retrieval ablation on {n} golden questions (k={K})", head, "-" * len(head)]
    for r in rows:
        lines.append(f"{r.step:<46}{r.recall:>9.3f}{r.mrr:>8.3f}{f'{r.followup[0]}/{r.followup[1]}':>12}"
                     f"{f'{r.newer[0]}/{r.newer[1]}':>13}{r.ctx_tokens:>12.0f}")
    lines.append("follow-ups = vague follow-up questions with every expected section in the top 5; "
                 "newer-first = contradiction questions whose first relevant chunk is the newer policy")
    return "\n".join(lines)


class GroqLLM:
    """complete(system, user, json_mode=False) -> text, with batch-friendly retries and token counting."""

    def __init__(self, client, model, sleep=time.sleep, log=lambda m: None):
        self.client, self.model, self.sleep, self.log = client, model, sleep, log
        self.input_tokens = self.output_tokens = self.calls = 0

    def __call__(self, system, user, json_mode=False):
        if self.client is None:
            raise LLMError("no Groq client (GROQ_API_KEY not set)", 0)
        kwargs = dict(model=self.model, temperature=0,
                      messages=[{"role": "system", "content": system}, {"role": "user", "content": user}])
        if json_mode:
            kwargs["response_format"] = {"type": "json_object"}
        resp, _ = call_with_retry(lambda: self.client.chat.completions.create(**kwargs), sleep=self.sleep,
                                  log=self.log, **BATCH_RETRY)
        self.calls += 1
        if resp.usage:
            self.input_tokens += resp.usage.prompt_tokens or 0
            self.output_tokens += resp.usage.completion_tokens or 0
        return (resp.choices[0].message.content or "").strip()


def parse_verdict(text):
    """Judge reply -> {'faithful': bool|None, 'correct': bool|None, 'reason': str}."""
    match = re.search(r"\{.*\}", text or "", re.S)
    try:
        data = json.loads(match.group(0) if match else text)
    except (TypeError, ValueError):
        return {"faithful": None, "correct": None, "reason": f"unparseable judge reply: {(text or '')[:80]}"}
    as_bool = lambda v: v if isinstance(v, bool) else (str(v).lower() == "true" if v is not None else None)  # noqa: E731
    return {"faithful": as_bool(data.get("faithful")), "correct": as_bool(data.get("correct")),
            "reason": str(data.get("reason", ""))}


def run_faithfulness(corpus, golden, rewrites, llm, catalog_path=None, pipeline=None, log=lambda m: None):
    """Answer every question with the full pipeline, then have the judge grade the answer."""
    results = []
    for item in golden:
        query = rewrites.get(item["id"], item["question"])
        hits = rag.search(corpus, query, pipeline or rag.Pipeline())
        if catalog_path:
            hits += rag.catalog_hits(query, catalog_path)
        context = rag.build_context(query, hits)
        result = {"id": item["id"], "question": item["question"], "answer": "", "refusal": False}
        try:
            result["answer"] = rag.normalize_citations(llm(RAG_SYSTEM_PROMPT, context))
            result["refusal"] = rag.is_refusal(result["answer"])
            judge_input = (f"CONTEXT:\n{context}\n\nQUESTION: {query}\n\nANSWER: {result['answer']}\n\n"
                           f"REFERENCE: {item['expected_answer']}")
            result.update(parse_verdict(llm(JUDGE_PROMPT, judge_input, json_mode=True)))
        except LLMError as e:
            result.update({"faithful": None, "correct": None, "reason": f"LLM error: {e}"})
        log(f"{item['id']}: faithful={result['faithful']} correct={result['correct']}")
        results.append(result)
    return results


def format_faithfulness(results, llm=None):
    n = len(results)
    pct = lambda x: f"{x}/{n} ({100 * x / n:.1f}%)" if n else "0/0"  # noqa: E731
    faithful = sum(r["faithful"] is True for r in results)
    correct = sum(r["correct"] is True for r in results)
    errors = sum(r["faithful"] is None for r in results)
    lines = [f"Faithfulness (LLM judge{f', {llm.model}' if llm else ''}) on {n} full-pipeline answers",
             f"faithful to context: {pct(faithful)} | agrees with reference: {pct(correct)} | "
             f"refusals: {sum(r['refusal'] for r in results)} | judge/LLM errors: {errors}"]
    if llm:
        lines.append(f"LLM calls: {llm.calls} | tokens: in {llm.input_tokens} / out {llm.output_tokens}")
    bad = [r for r in results if r["faithful"] is not True or r["correct"] is not True]
    if bad:
        lines.append("Not faithful or not correct:")
        for r in bad:
            lines.append(f"  {r['id']} faithful={r['faithful']} correct={r['correct']} - {r['question']}")
            lines.append(f"      judge: {r['reason']}")
            lines.append(f"      answer: {rag.snippet(r['answer'], 160)}")
    return "\n".join(lines)
