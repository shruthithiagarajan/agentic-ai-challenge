"""Challenge 4 (naive RAG) - offline: a fake Chroma collection and a fake OpenAI client."""
import contextlib, io, os, re, tempfile, unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from desk import cli, rag
from desk.prompts import RAG_SYSTEM_PROMPT


class FakeCollection:
    """The slice of the Chroma collection API rag.py uses; query ranks by word overlap."""

    def __init__(self):
        self.rows = {}
        self.queries = []

    def get(self, include=None):
        return {"ids": list(self.rows)}

    def delete(self, ids):
        for i in ids:
            self.rows.pop(i, None)

    def upsert(self, ids, documents, metadatas):
        for i, d, m in zip(ids, documents, metadatas):
            self.rows[i] = (d, m)

    def count(self):
        return len(self.rows)

    def query(self, query_texts, n_results, include):
        self.queries.append((query_texts, n_results))
        q = set(re.findall(r"\w+", query_texts[0].lower()))
        scored = sorted(((1 - len(q & set(re.findall(r"\w+", d.lower()))) / max(len(q), 1), i, d)
                         for i, (d, _) in self.rows.items()))[:n_results]
        return {"ids": [[i for _, i, _ in scored]], "documents": [[d for _, _, d in scored]],
                "distances": [[s for s, _, _ in scored]]}


def write(folder, name, text):
    (Path(folder) / name).write_text(text, encoding="utf-8")


def response(text, pin=300, pout=40):
    msg = SimpleNamespace(content=text)
    return SimpleNamespace(choices=[SimpleNamespace(message=msg)],
                           usage=SimpleNamespace(prompt_tokens=pin, completion_tokens=pout))


def fake_client(create):
    return SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))


HITS = [rag.Hit("returns_opened_items.md#1", "Opened items: 7 days, 20% restocking fee.", 0.21),
        rag.Hit("returns.md#1", "Opened items may be returned within 14 days, 10% fee.", 0.34)]


class ChunkingTests(unittest.TestCase):
    def test_short_text_is_one_chunk(self):
        self.assertEqual(rag.chunk_text("  hello world \n"), ["hello world"])

    def test_empty_text_has_no_chunks(self):
        self.assertEqual(rag.chunk_text(" \n "), [])

    def test_fixed_size_with_200_char_overlap(self):
        text = "".join(chr(65 + i % 26) for i in range(5000))
        chunks = rag.chunk_text(text)
        self.assertEqual([len(c) for c in chunks], [2000, 2000, 1400])
        for a, b in zip(chunks, chunks[1:]):
            self.assertEqual(a[-200:], b[:200])
        self.assertEqual(chunks[0] + "".join(c[200:] for c in chunks[1:]), text)  # nothing lost

    def test_exact_fit_has_no_tail_chunk(self):
        self.assertEqual(len(rag.chunk_text("x" * 2000)), 1)
        self.assertEqual(len(rag.chunk_text("x" * 2001)), 2)

    def test_defaults_are_about_500_and_50_tokens(self):
        self.assertEqual((rag.CHUNK_CHARS, rag.OVERLAP_CHARS, rag.TOP_K), (2000, 200, 4))

    def test_overlap_must_be_smaller_than_size(self):
        with self.assertRaises(ValueError):
            rag.chunk_text("abc", size=100, overlap=100)

    def test_ids_are_one_based_per_file_and_only_md_files(self):
        with tempfile.TemporaryDirectory() as d:
            write(d, "returns.md", "r" * 4000)
            write(d, "warranty.md", "w" * 10)
            write(d, "notes.txt", "ignored")
            files, ids, docs, metas = rag.load_chunks(d)
        self.assertEqual(ids, ["returns.md#1", "returns.md#2", "returns.md#3", "warranty.md#1"])
        self.assertEqual(metas[1], {"source": "returns.md", "chunk": 2})
        self.assertEqual(len(files), 2)

    def test_empty_folder_is_an_error(self):
        with tempfile.TemporaryDirectory() as d, self.assertRaises(FileNotFoundError):
            rag.load_chunks(d)


class IngestTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = self.tmp.name
        self.col = FakeCollection()

    def tearDown(self):
        self.tmp.cleanup()

    def test_rerun_is_idempotent(self):
        write(self.dir, "returns.md", "r" * 4000)
        write(self.dir, "shipping.md", "ship by ground")
        first = rag.ingest(self.col, self.dir)
        snapshot = dict(self.col.rows)
        second = rag.ingest(self.col, self.dir)
        self.assertEqual((first["chunks"], first["new"], first["total"]), (4, 4, 4))
        self.assertEqual((second["new"], second["removed"], second["total"]), (0, 0, 4))
        self.assertEqual(self.col.rows, snapshot)

    def test_shrunk_file_drops_stale_chunks_and_updates_text(self):
        write(self.dir, "returns.md", "r" * 4000)
        rag.ingest(self.col, self.dir)
        write(self.dir, "returns.md", "now short")
        s = rag.ingest(self.col, self.dir)
        self.assertEqual(sorted(self.col.rows), ["returns.md#1"])
        self.assertEqual(self.col.rows["returns.md#1"][0], "now short")
        self.assertEqual((s["removed"], s["total"]), (2, 1))

    def test_deleted_file_drops_its_chunks(self):
        write(self.dir, "returns.md", "returns")
        write(self.dir, "old.md", "old policy")
        rag.ingest(self.col, self.dir)
        os.remove(Path(self.dir) / "old.md")
        rag.ingest(self.col, self.dir)
        self.assertEqual(sorted(self.col.rows), ["returns.md#1"])

    def test_retrieve_asks_for_top_4(self):
        write(self.dir, "returns.md", "opened items return window")
        rag.ingest(self.col, self.dir)
        hits = rag.retrieve(self.col, "return window for opened items")
        self.assertEqual(self.col.queries[-1][1], 4)
        self.assertEqual(hits[0].id, "returns.md#1")
        self.assertAlmostEqual(hits[0].distance, 0.2)


class CitationTests(unittest.TestCase):
    def test_valid_citations(self):
        self.assertEqual(rag.citation_problems("7 days [returns_opened_items.md#1].", HITS), [])

    def test_missing_citations_flagged(self):
        self.assertIn("no [file.md#N] citations", rag.citation_problems("Opened items: 7 days.", HITS)[0])

    def test_invented_chunk_flagged(self):
        p = rag.citation_problems("7 days [returns.md#1] and free pickup [shipping.md#9].", HITS)
        self.assertEqual(p, ["cites chunks that were not retrieved: shipping.md#9"])

    def test_refusal_variants_need_no_citation(self):
        for a in ("not in the documents", "Not in the documents.", '"not in the documents"', "**Not in the documents**"):
            self.assertEqual(rag.citation_problems(a, HITS), [], a)
        self.assertFalse(rag.is_refusal("That is not in the documents, but usually 30 days."))

    def test_lenticular_and_grouped_citations_are_normalized(self):
        cases = {
            "30 days【returns.md#1】【returns.md#2】.": "30 days[returns.md#1][returns.md#2].",
            "6 months 【warranty.md#1†L3-L5】": "6 months [warranty.md#1]",
            "7 days [returns.md#1, returns_opened_items.md#1]": "7 days [returns.md#1][returns_opened_items.md#1]",
            "already fine [returns.md#1].": "already fine [returns.md#1].",
            "no citation [sic] here": "no citation [sic] here",
        }
        for raw, want in cases.items():
            self.assertEqual(rag.normalize_citations(raw), want, raw)

    def test_context_labels_every_chunk_with_its_id(self):
        ctx = rag.build_context("Return window?", HITS)
        self.assertIn("[returns_opened_items.md#1]\nOpened items", ctx)
        self.assertIn("[returns.md#1]\n", ctx)
        self.assertTrue(ctx.endswith("Question: Return window?"))

    def test_prompt_demands_citations_and_exact_refusal(self):
        for s in ("ONLY", "[returns.md#3]", "Only cite ids that appear", "exactly: not in the documents"):
            self.assertIn(s, RAG_SYSTEM_PROMPT)


class AskCliTests(unittest.TestCase):
    def run_ask(self, argv, create=None, hits=HITS, sleeps=None):
        args = cli.build_parser().parse_args(["ask", *argv])
        out, err = io.StringIO(), io.StringIO()
        self.err = err
        client = fake_client(create) if create else None
        with mock.patch.dict(os.environ, {}, clear=True), contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = cli.cmd_ask(args, sleep=(sleeps.append if sleeps is not None else None), client=client,
                               retrieve=lambda q: hits)
        return code, out.getvalue(), err.getvalue()

    def test_sends_rag_prompt_and_chunks_and_prints_footer(self):
        seen = {}
        code, out, err = self.run_ask(["Return window for opened items?"],
                                      lambda **kw: seen.update(kw) or response("7 days [returns_opened_items.md#1]."))
        self.assertEqual(code, 0)
        system, user = seen["messages"]
        self.assertEqual(system["content"], RAG_SYSTEM_PROMPT)
        self.assertIn("[returns.md#1]", user["content"])
        self.assertIn("Question: Return window for opened items?", user["content"])
        self.assertIn("7 days [returns_opened_items.md#1].", out)
        for s in ("input tokens: 300", "output tokens: 40", "latency:", "est. cost: n/a"):
            self.assertIn(s, out)
        self.assertNotIn("[citations]", err)
        self.assertNotIn("[debug]", err)

    def test_debug_prints_ids_scores_snippets_before_the_llm_call(self):
        at_call = []
        code, _, err = self.run_ask(["Return window?", "--debug"],
                                    lambda **kw: at_call.append(self.err.getvalue()) or response("7 days [returns.md#1]."))
        self.assertEqual(code, 0)
        self.assertIn("[returns_opened_items.md#1] distance 0.210 (similarity 0.790) | Opened items: 7 days", at_call[0])
        self.assertIn("[returns.md#1] distance 0.340", at_call[0])

    def test_uncited_answer_warns(self):
        code, out, err = self.run_ask(["Return window?"], lambda **kw: response("Opened items: 30 days."))
        self.assertEqual(code, 0)
        self.assertIn("Opened items: 30 days.", out)
        self.assertIn("[citations] warning: answer has no [file.md#N] citations", err)

    def test_lenticular_citations_printed_as_brackets_without_warning(self):
        code, out, err = self.run_ask(["Return window?"], lambda **kw: response("7 days【returns_opened_items.md#1】"))
        self.assertEqual(code, 0)
        self.assertEqual(out.splitlines()[0], "7 days[returns_opened_items.md#1]")
        self.assertNotIn("[citations]", err)

    def test_invented_citation_warns(self):
        _, _, err = self.run_ask(["Return window?"], lambda **kw: response("30 days [policy.md#7]."))
        self.assertIn("not retrieved: policy.md#7", err)

    def test_refusal_is_printed_without_warning(self):
        code, out, err = self.run_ask(["What is the CEO's birthday?"], lambda **kw: response("not in the documents"))
        self.assertEqual(code, 0)
        self.assertEqual(out.splitlines()[0], "not in the documents")
        self.assertNotIn("[citations]", err)

    def test_empty_index_tells_you_to_ingest_without_calling_llm(self):
        create = mock.Mock()
        code, out, err = self.run_ask(["Return window?"], create, hits=[])
        self.assertEqual((code, out), (1, ""))
        self.assertIn("desk ingest", err)
        create.assert_not_called()

    def test_missing_chromadb_is_a_clear_error(self):
        args = cli.build_parser().parse_args(["ask", "hi"])
        err = io.StringIO()

        def boom(q):
            raise ImportError("chromadb")

        with contextlib.redirect_stderr(err):
            self.assertEqual(cli.cmd_ask(args, retrieve=boom), 1)
        self.assertIn("pip install -e .", err.getvalue())

    def test_simulate_429_still_retries(self):
        sleeps = []
        code, out, err = self.run_ask(["hi", "--simulate", "429"], sleeps=sleeps)
        self.assertEqual((code, out, sleeps), (1, "", [1.0, 2.0]))
        self.assertIn("after 3 attempts", err)

    def test_ch1_flags_still_parse(self):
        a = cli.build_parser().parse_args(["ask", "q", "--model", "m", "--temperature", "0", "--debug"])
        self.assertEqual((a.model, a.temperature, a.debug), ("m", 0.0, True))


class IngestCliTests(unittest.TestCase):
    def run_ingest(self, data_dir, col):
        out, err = io.StringIO(), io.StringIO()
        with mock.patch.dict(os.environ, {"DESK_DATA_DIR": data_dir}), contextlib.redirect_stdout(out), \
                contextlib.redirect_stderr(err):
            code = cli.cmd_ingest(cli.build_parser().parse_args(["ingest"]), collection=col)
        return code, out.getvalue(), err.getvalue()

    def test_summary_and_rerun(self):
        with tempfile.TemporaryDirectory() as d:
            os.mkdir(Path(d) / "policies")
            write(Path(d) / "policies", "returns.md", "r" * 4000)
            write(Path(d) / "policies", "warranty.md", "w")
            col = FakeCollection()
            code, out, _ = self.run_ingest(d, col)
            self.assertEqual(code, 0)
            self.assertIn("loaded 2 files", out)
            self.assertIn("stored 4 chunks (4 new, 0 stale removed)", out)
            _, out2, _ = self.run_ingest(d, col)
            self.assertIn("stored 4 chunks (0 new, 0 stale removed)", out2)
            self.assertIn("now holds 4 chunks", out2)

    def test_missing_policies_folder(self):
        with tempfile.TemporaryDirectory() as d:
            code, _, err = self.run_ingest(d, FakeCollection())
        self.assertEqual(code, 1)
        self.assertIn("DESK_DATA_DIR", err)


if __name__ == "__main__":
    unittest.main()
