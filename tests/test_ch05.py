"""Challenge 5 (advanced RAG, measured) - offline: fake Chroma collection, fake OpenAI client."""
import contextlib, io, json, os, re, tempfile, unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from desk import cli, rag, rag_eval
from desk.llm import LLMError
from desk.prompts import JUDGE_PROMPT, RAG_SYSTEM_PROMPT, REWRITE_PROMPT

RETURNS = """---
doc_id: POL-RET-001
title: Returns Policy
effective_date: 2025-01-01
---
# Returns Policy

## 1. Scope
1.1 Bulk lines of 250 units or more need written approval from the account manager.

## 2. Return window for unopened goods
2.1 Unopened goods may be returned within 30 days of the delivery date.

## 3. Opened items
3.1 Opened items may be returned within 14 days of the delivery date.
3.2 Opened items attract a restocking fee of 10% of the line value.

## 6. Return eligibility summary

| Condition | Return window | Restocking fee |
|---|---|---|
| Unopened | 30 days | None |
| Opened | 14 days | 10% |
"""

OPENED = """---
doc_id: POL-RET-002
title: Returns of Opened Items - Updated Terms
effective_date: 2026-03-01
---
# Returns of Opened Items - Updated Terms

## 2. Opened-item return window and fee
2.1 Opened items may be returned within 7 days of the delivery date.
2.2 A restocking fee of 20% of the line value applies to opened items.
"""

HAZARD = """---
doc_id: POL-HAZ-001
title: Hazardous Goods Handling Policy
effective_date: 2025-03-01
---
# Hazardous Goods Handling Policy

## 1. Hazardous SKUs
| SKU | Product | Class |
|---|---|---|
| MS-HAZ0105 | Hydrochloric Acid 30% 25 L Carboy | 8 |

## 2. Carriers
2.1 Hazardous goods ship only with SafeHaul Logistics and VRL Logistics, never by air.
"""


class FakeCollection:
    """Enough of a Chroma collection: dense 'similarity' is word overlap unless `dense_order` pins a ranking."""

    def __init__(self, dense_order=None):
        self.rows = {}
        self.dense_order = dense_order

    def upsert(self, ids, documents, metadatas):
        for i, d, m in zip(ids, documents, metadatas):
            self.rows[i] = (d, m)

    def delete(self, ids):
        for i in ids:
            self.rows.pop(i, None)

    def count(self):
        return len(self.rows)

    def get(self, include=None):
        ids = list(self.rows)
        return {"ids": ids, "documents": [self.rows[i][0] for i in ids], "metadatas": [self.rows[i][1] for i in ids]}

    def query(self, query_texts, n_results, include):
        if self.dense_order:
            ranked = [(0.1 * n, i) for n, i in enumerate(self.dense_order) if i in self.rows]
        else:
            q = set(re.findall(r"\w+", query_texts[0].lower()))
            ranked = sorted((1 - len(q & set(re.findall(r"\w+", d.lower()))) / max(len(q), 1), i)
                            for i, (d, _) in self.rows.items())
        ranked = ranked[:n_results]
        return {"ids": [[i for _, i in ranked]], "distances": [[d for d, _ in ranked]],
                "documents": [[self.rows[i][0] for _, i in ranked]]}


def policy_dir(tmp, files=None):
    folder = Path(tmp) / "policies"
    folder.mkdir(parents=True, exist_ok=True)
    for name, text in (files or {"returns.md": RETURNS, "returns_opened_items.md": OPENED,
                                 "hazardous_goods.md": HAZARD}).items():
        (folder / name).write_text(text, encoding="utf-8")
    return folder


def corpus_for(tmp, dense_order=None, loader=rag.load_sections):
    col = FakeCollection(dense_order)
    rag.ingest(col, policy_dir(tmp), loader=loader)
    return rag.Corpus(col)


def hit(cid, text, date, doc_id, score=1.0):
    return rag.Hit(cid, text, 0.2, score, {"source": cid.split("#")[0], "effective_date": date, "doc_id": doc_id})


def response(text, pin=100, pout=20):
    return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=text))],
                           usage=SimpleNamespace(prompt_tokens=pin, completion_tokens=pout))


def fake_client(replies, seen=None):
    replies = list(replies)

    def create(**kw):
        if seen is not None:
            seen.append(kw)
        return response(replies.pop(0))

    return SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))


class StructureChunkingTests(unittest.TestCase):
    def test_one_chunk_per_heading_with_title_section_date_header(self):
        chunks = rag.chunk_markdown(RETURNS, "returns.md")
        self.assertEqual([c[0] for c in chunks], ["returns.md#1", "returns.md#2", "returns.md#3", "returns.md#6"])
        cid, text, meta = chunks[2]
        self.assertEqual(text.splitlines()[0], "[Returns Policy | 3. Opened items | Effective 2025-01-01]")
        self.assertIn("3.2 Opened items attract a restocking fee", text)
        self.assertNotIn("## 3", text)
        self.assertEqual((meta["section"], meta["effective_date"], meta["doc_id"]), ("3", "2025-01-01", "POL-RET-001"))

    def test_table_section_kept_whole(self):
        text = rag.chunk_markdown(RETURNS, "returns.md")[-1][1]
        self.assertIn("| Condition | Return window | Restocking fee |", text)
        self.assertIn("| Opened | 14 days | 10% |", text)

    def test_long_section_splits_between_blocks_but_never_inside_a_table(self):
        table = "\n".join(["| a | b |", "|---|---|"] + [f"| row {i} | {'x' * 40} |" for i in range(60)])
        doc = f"## 1. Big\n{'para one ' * 150}\n\n{table}\n\n{'para two ' * 150}\n"
        chunks = rag.chunk_markdown(doc, "big.md")
        self.assertEqual([c[0] for c in chunks], ["big.md#1-1", "big.md#1-2", "big.md#1-3"])
        tables = [c[1] for c in chunks if "| a | b |" in c[1]]
        self.assertEqual(len(tables), 1)
        self.assertGreater(len(table), rag.MAX_SECTION_CHARS)
        self.assertIn("| row 59 |", tables[0])

    def test_no_front_matter_or_headings_falls_back(self):
        chunks = rag.chunk_markdown("Just some text.", "notes.md")
        self.assertEqual(chunks[0][0], "notes.md#0")
        self.assertEqual(chunks[0][1].splitlines()[0], "[notes | Section 0 | Effective unknown]")

    def test_split_blocks_separates_tables_from_paragraphs(self):
        self.assertEqual(rag.split_blocks("text\n| a |\n| b |\nmore"), ["text", "| a |\n| b |", "more"])

    def test_ingest_with_sections_is_idempotent(self):
        with tempfile.TemporaryDirectory() as d:
            col = FakeCollection()
            first = rag.ingest(col, policy_dir(d), loader=rag.load_sections)
            second = rag.ingest(col, policy_dir(d), loader=rag.load_sections)
        self.assertEqual((first["chunks"], first["new"]), (7, 7))  # returns 4 + opened 1 + hazardous 2
        self.assertEqual((second["new"], second["removed"], second["total"]), (0, 0, 7))


class HybridSearchTests(unittest.TestCase):
    def test_tokens(self):
        self.assertEqual(rag.tokens("What is the fee on 18,000 rupees?"), ["fee", "18000", "rupee"])
        self.assertIn("ms-haz0105", rag.tokens("Is MS-HAZ0105 corrosive?"))

    def test_rrf_math(self):
        fused = dict(rag.rrf([["a", "b", "c"], ["b", "d"]]))
        self.assertAlmostEqual(fused["b"], 1 / 62 + 1 / 61)
        self.assertAlmostEqual(fused["a"], 1 / 61)
        self.assertAlmostEqual(fused["d"], 1 / 62)
        self.assertEqual([i for i, _ in rag.rrf([["a", "b", "c"], ["b", "d"]])], ["b", "a", "d", "c"])

    def test_bm25_finds_what_dense_ranks_last(self):
        with tempfile.TemporaryDirectory() as d:
            ids = [i for i in rag.load_sections(policy_dir(d))[1]]
            dense = sorted(ids, key=lambda i: i == "hazardous_goods.md#2")  # carriers section ranked last
            corpus = corpus_for(d, dense_order=dense)
            only_dense = rag.search(corpus, "Which carriers ship hazardous goods by air?",
                                    rag.Pipeline(hybrid=False, rerank=False, recency=False, k=3))
            hybrid = rag.search(corpus, "Which carriers ship hazardous goods by air?",
                                rag.Pipeline(rerank=False, recency=False, k=3))
        self.assertNotIn("hazardous_goods.md#2", [h.id for h in only_dense])
        self.assertIn("hazardous_goods.md#2", [h.id for h in hybrid])

    def test_exact_sku_boost_wins_the_rerank(self):
        with tempfile.TemporaryDirectory() as d:
            ids = rag.load_sections(policy_dir(d))[1]
            dense = sorted(ids, key=lambda i: i == "hazardous_goods.md#1")  # SKU table ranked last by dense
            corpus = corpus_for(d, dense_order=dense)
            hits = rag.search(corpus, "What is MS-HAZ0105?", rag.Pipeline(recency=False))
        self.assertEqual(hits[0].id, "hazardous_goods.md#1")
        self.assertIsNotNone(hits[0].score)

    def test_rerank_score_features(self):
        base = rag.rerank_score("opened items fee", "opened items fee", "Opened items", 0.0)
        self.assertAlmostEqual(base, 0.5 * 1 + 0.3 * (2 / 3))
        self.assertAlmostEqual(rag.rerank_score("MS-HAZ0105", "row MS-HAZ0105", "", 0.0), 0.5 + 1.0)


class RecencyTests(unittest.TestCase):
    OLD = hit("returns.md#3", "[R | 3 | 2025]\nOpened items may be returned within 14 days of delivery, fee 10% of line value.",
              "2025-01-01", "POL-RET-001")
    NEW = hit("returns_opened_items.md#2", "[RO | 2 | 2026]\nOpened items may be returned within 7 days of delivery, fee 20% of line value.",
              "2026-03-01", "POL-RET-002")
    OTHER = hit("warranty.md#1", "[W | 1 | 2025]\nBearings carry a 6 month warranty.", "2025-01-01", "POL-WAR-001")

    def fresh(self, *hits):
        return [rag.Hit(h.id, h.text, h.distance, h.score, dict(h.meta)) for h in hits]

    def test_newer_rule_moves_above_older_and_both_get_dated_notes(self):
        old, other, new = self.fresh(self.OLD, self.OTHER, self.NEW)
        final = rag.prefer_latest([old, other, new], k=3)
        self.assertEqual([h.id for h in final], ["returns_opened_items.md#2", "returns.md#3", "warranty.md#1"])
        self.assertIn("NEWER rule, effective 2026-03-01", final[0].note)
        self.assertIn("OLDER rule, effective 2025-01-01", final[1].note)

    def test_newer_rule_never_moves_down_and_lists_every_rule_it_replaces(self):
        table = hit("returns.md#6", "[R | 6 | 2025]\nOpened items returned within 14 days of delivery, fee 10% of line value.",
                    "2025-01-01", "POL-RET-001")
        new, old, tbl = self.fresh(self.NEW, self.OLD, table)
        final = rag.prefer_latest([new, old, tbl], k=3)
        self.assertEqual([h.id for h in final], ["returns_opened_items.md#2", "returns.md#3", "returns.md#6"])
        self.assertIn("replaces returns.md#3 (effective 2025-01-01), returns.md#6 (effective 2025-01-01)", final[0].note)

    def test_newer_rule_outside_top_k_is_pulled_in(self):
        old, other, new = self.fresh(self.OLD, self.OTHER, self.NEW)
        final = rag.prefer_latest([old, other, new], k=2)
        self.assertEqual([h.id for h in final], ["returns_opened_items.md#2", "returns.md#3"])

    def test_different_family_or_topic_is_left_alone(self):
        old, other = self.fresh(self.OLD, self.OTHER)
        self.assertEqual([h.id for h in rag.prefer_latest([other, old], k=2)], ["warranty.md#1", "returns.md#3"])
        unrelated = rag.Hit("returns_opened_items.md#1", "[RO | 1 | 2026]\nThis document sets out its purpose.",
                            meta={"source": "returns_opened_items.md", "effective_date": "2026-03-01",
                                  "doc_id": "POL-RET-002"})
        self.assertFalse(rag.supersedes(unrelated, old))

    def test_notes_reach_the_model_context(self):
        old, new = self.fresh(self.OLD, self.NEW)
        ctx = rag.build_context("Opened return window?", rag.prefer_latest([old, new], k=2))
        self.assertIn("[returns_opened_items.md#2]\nNOTE: NEWER rule, effective 2026-03-01", ctx)

    def test_full_pipeline_on_real_shaped_docs(self):
        with tempfile.TemporaryDirectory() as d:
            hits = rag.search(corpus_for(d), "What is the return window for opened items?")
        ids = [h.id for h in hits]
        self.assertLess(ids.index("returns_opened_items.md#2"), ids.index("returns.md#3"))

    def test_prompt_requires_stating_the_effective_date(self):
        for s in ("LATEST effective date", "state that date explicitly", "NOTE line", "not in the documents"):
            self.assertIn(s, RAG_SYSTEM_PROMPT)


class RewriteTests(unittest.TestCase):
    HISTORY = [{"role": "user", "content": "What is the freight charge on an order worth 18,000 rupees?"},
               {"role": "assistant", "content": "450 rupees [shipping.md#3]."}]

    def test_follow_up_is_rewritten_with_the_conversation(self):
        calls = []
        q = rag.rewrite_query("and for 30,000?", self.HISTORY,
                              lambda system, user: calls.append((system, user)) or "freight charge for a 30,000 rupee order")
        self.assertEqual(q, "freight charge for a 30,000 rupee order")
        self.assertEqual(calls[0][0], REWRITE_PROMPT)
        self.assertIn("18,000 rupees", calls[0][1])
        self.assertIn("Follow-up: and for 30,000?", calls[0][1])

    def test_no_history_no_llm_call(self):
        self.assertEqual(rag.rewrite_query("When is freight free?", [], lambda s, u: self.fail("called")),
                         "When is freight free?")

    def test_llm_failure_falls_back_to_previous_question(self):
        def boom(system, user):
            raise LLMError("rate limited", 3)

        self.assertEqual(rag.rewrite_query("and for 30,000?", self.HISTORY, boom),
                         "What is the freight charge on an order worth 18,000 rupees? and for 30,000?")


class AskCliTests(unittest.TestCase):
    def run_ask(self, argv, replies, hits, env=None):
        args = cli.build_parser().parse_args(["ask", *argv])
        out, err, seen, queries = io.StringIO(), io.StringIO(), [], []
        with mock.patch.dict(os.environ, env or {}, clear=True), contextlib.redirect_stdout(out), \
                contextlib.redirect_stderr(err):
            code = cli.cmd_ask(args, client=fake_client(replies, seen), retrieve=lambda q: queries.append(q) or hits)
        return code, out.getvalue(), err.getvalue(), seen, queries

    def test_prev_rewrites_before_retrieval(self):
        hits = [rag.Hit("shipping.md#3", "[Shipping | 3. Freight | Effective 2025-06-01]\nFree above 25,000.", 0.2, 0.9)]
        code, out, err, seen, queries = self.run_ask(
            ["and for 30,000?", "--prev", "What is the freight charge on an order worth 18,000 rupees?", "--debug"],
            ["freight charge for an order worth 30,000 rupees", "Freight is free [shipping.md#3]."], hits)
        self.assertEqual(code, 0)
        self.assertEqual(queries, ["freight charge for an order worth 30,000 rupees"])
        self.assertIn("Question: freight charge for an order worth 30,000 rupees", seen[1]["messages"][1]["content"])
        self.assertIn("follow-up rewritten to: freight charge", err)
        self.assertIn("[shipping.md#3] score 0.900 (distance 0.200) | Free above 25,000.", err)

    def test_catalog_flag_adds_sku_rows(self):
        with tempfile.TemporaryDirectory() as d:
            Path(d, "catalog.csv").write_text("sku,name,category\nMS-ELC0410,Motor 7.5 kW,Electrical\n", encoding="utf-8")
            hits = [rag.Hit("warranty.md#1", "[Warranty | 1 | 2025]\nElectrical: 24 months.", 0.3, 0.8)]
            code, out, err, seen, _ = self.run_ask(["What warranty applies to MS-ELC0410?", "--catalog"],
                                                   ["24 months [warranty.md#1][catalog.csv#MS-ELC0410]."], hits,
                                                   env={"DESK_DATA_DIR": d})
        self.assertEqual(code, 0)
        self.assertIn("[catalog.csv#MS-ELC0410]\n[Product catalog | MS-ELC0410]", seen[0]["messages"][1]["content"])
        self.assertNotIn("[citations]", err)

    def test_citations_accept_section_parts_and_catalog_ids(self):
        hits = [rag.Hit("big.md#1-2", "x"), rag.Hit("catalog.csv#MS-ELC0410", "y")]
        self.assertEqual(rag.citation_problems("A [big.md#1-2]. B [catalog.csv#MS-ELC0410].", hits), [])
        self.assertEqual(rag.normalize_citations("fee【returns.md#3.2】"), "fee[returns.md#3]")  # clause -> section


class MetricTests(unittest.TestCase):
    def test_recall_and_mrr(self):
        ranked = [{"a.md#1"}, {"b.md#2"}, {"c.md#3"}, {"d.md#1"}, {"e.md#1"}, {"f.md#9"}]
        self.assertEqual(rag_eval.recall_at_k(ranked, ["b.md#2", "f.md#9"]), 0.5)  # f is rank 6 - outside top 5
        self.assertEqual(rag_eval.reciprocal_rank(ranked, ["c.md#3", "b.md#2"]), 0.5)
        self.assertEqual(rag_eval.reciprocal_rank(ranked, ["f.md#9"]), 0.0)
        self.assertEqual(rag_eval.recall_at_k([], ["a.md#1"]), 0.0)

    def test_section_of_and_fixed_chunk_mapping(self):
        self.assertEqual(rag_eval.section_of("returns.md#3-2"), "returns.md#3")
        text = "## 1. A\n" + "a" * 1900 + "\n## 2. B\n" + "b" * 1900 + "\n## 3. C\nc"
        secs = rag_eval.fixed_chunk_sections(text, "x.md")
        self.assertEqual(secs[0], {"x.md#1", "x.md#2"})
        self.assertEqual(secs[-1], {"x.md#2", "x.md#3"})

    def test_newer_first(self):
        dates = {"returns.md": "2025-01-01", "returns_opened_items.md": "2026-03-01"}
        expected = ["returns_opened_items.md#2", "returns.md#3"]
        self.assertTrue(rag_eval.newer_first([{"returns_opened_items.md#2"}, {"returns.md#3"}], expected, dates))
        self.assertFalse(rag_eval.newer_first([{"returns.md#3"}, {"returns_opened_items.md#2"}], expected, dates))

    def test_parse_verdict(self):
        self.assertEqual(rag_eval.parse_verdict('ok {"faithful": true, "correct": "false", "reason": "r"}'),
                         {"faithful": True, "correct": False, "reason": "r"})
        self.assertIsNone(rag_eval.parse_verdict("nope")["faithful"])


GOLDEN = [
    {"id": "g1", "question": "What is the return window for opened items?", "category": "contradiction",
     "expected_sections": ["returns_opened_items.md#2", "returns.md#3"], "expected_answer": "7 days (2026 terms).",
     "must_cite_newer_doc": True, "chat_history": []},
    {"id": "g2", "question": "Which carriers may ship hazardous goods?", "category": "plain",
     "expected_sections": ["hazardous_goods.md#2"], "expected_answer": "SafeHaul and VRL.",
     "must_cite_newer_doc": False, "chat_history": []},
    {"id": "g3", "question": "what about for bulk?", "category": "vague_followup",
     "expected_sections": ["returns.md#1"], "expected_answer": "Account manager approval.",
     "must_cite_newer_doc": False,
     "chat_history": [{"role": "user", "content": "What is the return window for unopened goods?"}]},
]


class EvalHarnessTests(unittest.TestCase):
    def test_ablation_table_and_rewrites(self):
        with tempfile.TemporaryDirectory() as d:
            folder = policy_dir(d)
            corpora = {"fixed": corpus_for(d, loader=rag.load_chunks), "sections": corpus_for(d)}
            calls = []
            rows, rewrites = rag_eval.run_ablation(
                corpora, GOLDEN, rag_eval.fixed_section_map(folder), rag_eval.effective_dates(folder),
                lambda q, h: calls.append(q) or "returns of bulk orders account manager approval")
        self.assertEqual(len(rows), len(rag_eval.STEPS))
        self.assertEqual(calls, ["what about for bulk?"])  # rewritten once, reused by later steps
        self.assertEqual(rewrites, {"g3": "returns of bulk orders account manager approval"})
        full = rows[-1]
        self.assertEqual(full.recall, 1.0)
        self.assertEqual((full.followup, full.newer), ((1, 1), (1, 1)))
        table = rag_eval.format_ablation(rows, 3)
        for s in ("Recall@5", "MRR@5", "Ch4 baseline", "+ latest effective_date preference"):
            self.assertIn(s, table)

    def test_faithfulness_answers_then_judges(self):
        with tempfile.TemporaryDirectory() as d:
            corpus = corpus_for(d)
            seen = []
            llm = rag_eval.ChatLLM(fake_client([
                "7 days under the terms effective 2026-03-01【returns_opened_items.md#2】",
                '{"faithful": true, "correct": true, "reason": "supported"}',
                "Only SafeHaul [hazardous_goods.md#2] and DHL.",
                '{"faithful": false, "correct": false, "reason": "DHL is not in the context"}',
            ], seen), "judge-model")
            results = rag_eval.run_faithfulness(corpus, GOLDEN[:2], {}, llm)
        self.assertEqual(seen[0]["messages"][0]["content"], RAG_SYSTEM_PROMPT)
        self.assertEqual(seen[1]["messages"][0]["content"], JUDGE_PROMPT)
        self.assertEqual(seen[1]["response_format"], {"type": "json_object"})
        self.assertIn("REFERENCE: 7 days (2026 terms).", seen[1]["messages"][1]["content"])
        self.assertNotIn("7 days (2026 terms)", seen[0]["messages"][1]["content"])  # answer key never reaches the answerer
        self.assertIn("[returns_opened_items.md#2]", results[0]["answer"])
        report = rag_eval.format_faithfulness(results, llm)
        self.assertIn("faithful to context: 1/2 (50.0%)", report)
        self.assertIn("DHL is not in the context", report)
        self.assertIn("answers (judge-model): 4 LLM calls", report)

    def test_separate_answer_and_judge_models(self):
        with tempfile.TemporaryDirectory() as d:
            corpus = corpus_for(d)
            a_seen, j_seen = [], []
            answerer = rag_eval.ChatLLM(fake_client(["7 days [returns_opened_items.md#2]"], a_seen), "claude-sonnet-5-5")
            judge = rag_eval.ChatLLM(fake_client(['{"faithful": true, "correct": true, "reason": "ok"}'], j_seen),
                                     "gpt-6.2-sol")
            results = rag_eval.run_faithfulness(corpus, GOLDEN[:1], {}, answerer, judge=judge)
        self.assertEqual(([k["model"] for k in a_seen], [k["model"] for k in j_seen]),
                         (["claude-sonnet-5-5"], ["gpt-6.2-sol"]))
        self.assertEqual(j_seen[0]["messages"][0]["content"], JUDGE_PROMPT)
        report = rag_eval.format_faithfulness(results, answerer, judge)
        self.assertIn("answers by claude-sonnet-5-5, judged by gpt-6.2-sol", report)
        self.assertIn("answers (claude-sonnet-5-5): 1 LLM calls", report)
        self.assertIn("judge (gpt-6.2-sol): 1 LLM calls", report)

    def test_eval_rag_cli_retrieval_only(self):
        with tempfile.TemporaryDirectory() as d:
            policy_dir(d)
            Path(d, "evals").mkdir()
            Path(d, "evals", "golden_retrieval.jsonl").write_text(
                "\n".join(json.dumps(g) for g in GOLDEN[:2]), encoding="utf-8")
            corpora = {"fixed": corpus_for(d, loader=rag.load_chunks), "sections": corpus_for(d)}
            out, err = io.StringIO(), io.StringIO()
            args = cli.build_parser().parse_args(["eval-rag", "--no-faithfulness"])
            with mock.patch.dict(os.environ, {"DESK_DATA_DIR": d}, clear=True), contextlib.redirect_stdout(out), \
                    contextlib.redirect_stderr(err):
                code = cli.cmd_eval_rag(args, corpora=corpora)
        self.assertEqual(code, 0)
        self.assertIn("Retrieval ablation on 2 golden questions", out.getvalue())
        self.assertNotIn("Faithfulness", out.getvalue())


if __name__ == "__main__":
    unittest.main()
