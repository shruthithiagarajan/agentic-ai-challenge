"""Challenge 3 - structured outputs: schema, repair loop, needs_review routing, batch, accuracy. Offline (fake SDK)."""
import io
import json
import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from pydantic import ValidationError

from desk import cli
from desk.extract import (MAX_REPAIRS, Extractor, accuracy, field_correct, format_report, item_matches, run_batch)
from desk.schemas import OrderRequest, response_schema


def order(**over):
    base = dict(customer="Kaveri Engineering Works", intent="order", secondary_intents=[],
                line_items=[{"sku": "MS-VLV0100", "description": "MS-VLV0100", "quantity": 40, "unit": "pcs"}],
                requested_date="2026-10-12", urgency="normal", confidence=0.9)
    base.update(over)
    return base


def response(content, p=100, c=50):
    return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=content))],
                           usage=SimpleNamespace(prompt_tokens=p, completion_tokens=c))


class FakeAPIError(Exception):
    def __init__(self, status_code, body):
        super().__init__(f"Error code: {status_code} - {body}")
        self.status_code, self.body = status_code, body


class BadRequestError(FakeAPIError):  # name-matches openai.BadRequestError (not retried)
    pass


def scripted(*outputs, calls=None):
    """Fake client: each create() returns/raises the next scripted output and records its kwargs."""
    queue = list(outputs)

    def create(**kw):
        if calls is not None:
            calls.append({**kw, "messages": [dict(m) for m in kw["messages"]]})
        nxt = queue.pop(0)
        if isinstance(nxt, Exception):
            raise nxt
        return response(nxt if isinstance(nxt, str) else json.dumps(nxt))

    return SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))


class SchemaTests(unittest.TestCase):
    def test_valid_order_parses(self):
        o = OrderRequest.model_validate_json(json.dumps(order()))
        self.assertEqual((o.intent, o.line_items[0].sku, o.line_items[0].quantity), ("order", "MS-VLV0100", 40))

    def test_multi_intent_primary_plus_secondary(self):
        o = OrderRequest.model_validate(order(intent="complaint", secondary_intents=["return", "other"]))
        self.assertEqual((o.intent, o.secondary_intents), ("complaint", ["return", "other"]))

    def test_multi_intent_rule_rejects_primary_repeated_or_duplicates(self):
        for sec in (["order"], ["other", "other"]):
            with self.subTest(sec=sec), self.assertRaises(ValidationError):
                OrderRequest.model_validate(order(secondary_intents=sec))

    def test_invalid_values_rejected(self):
        bad = [dict(intent="refund"), dict(urgency="medium"), dict(confidence=1.5), dict(customer=""),
               dict(requested_date="2026-02-30"), dict(requested_date=""), dict(extra_field=1),
               dict(line_items=[{"sku": None, "description": "valve", "quantity": 0, "unit": "pcs"}]),
               dict(line_items=[{"sku": None, "description": "valve", "quantity": "twelve", "unit": "pcs"}]),
               dict(line_items=[{"sku": "brass valve", "description": "valve", "quantity": 1, "unit": "pcs"}])]
        for over in bad:
            with self.subTest(over=over), self.assertRaises(ValidationError):
                OrderRequest.model_validate(order(**over))

    def test_described_product_keeps_null_sku_and_relative_date_allowed(self):
        o = OrderRequest.model_validate(order(requested_date="sometime next month",
                                              line_items=[{"sku": None, "description": "2-inch brass valve", "quantity": 12, "unit": "pcs"}]))
        self.assertIsNone(o.line_items[0].sku)

    def test_json_schema_lists_intents(self):
        s = json.dumps(response_schema())
        for v in ("order", "status", "complaint", "return", "other", "secondary_intents"):
            self.assertIn(v, s)


class RepairLoopTests(unittest.TestCase):
    def extractor(self, client, **kw):
        return Extractor(client, "m", sleep=lambda s: None, **kw)

    def test_first_try_valid_uses_json_schema_mode(self):
        calls = []
        x = self.extractor(scripted(order(), calls=calls)).extract_one("email_01", "Email text")
        self.assertEqual((x.attempts, x.error, x.input_tokens), (1, None, 100))
        fmt = calls[0]["response_format"]
        self.assertEqual((fmt["type"], fmt["json_schema"]["name"]), ("json_schema", "OrderRequest"))
        self.assertEqual(calls[0]["temperature"], 0.0)

    def test_malformed_first_response_fixed_on_repair_1(self):
        calls = []
        bad = "Sure! Here is the JSON: " + json.dumps(order())  # wrapped in commentary
        x = self.extractor(scripted(bad, order(), calls=calls)).extract_one("email_01", "Email text")
        self.assertIsNotNone(x.order)
        self.assertEqual((x.attempts, x.input_tokens, x.output_tokens), (2, 200, 100))
        repair = calls[1]["messages"]
        self.assertEqual(repair[-2], {"role": "assistant", "content": bad})
        self.assertIn("failed validation", repair[-1]["content"])
        self.assertIn("Invalid JSON", repair[-1]["content"])  # pydantic's own message is passed back

    def test_schema_violation_message_sent_back(self):
        calls = []
        x = self.extractor(scripted(order(urgency="medium"), order(), calls=calls)).extract_one("e", "t")
        self.assertEqual(x.attempts, 2)
        self.assertIn("urgency", calls[1]["messages"][-1]["content"])

    def test_gives_up_after_two_repairs(self):
        calls = []
        x = self.extractor(scripted("nope", "still nope", order(intent="refund"), calls=calls)).extract_one("e", "t")
        self.assertIsNone(x.order)
        self.assertEqual((x.attempts, len(calls)), (MAX_REPAIRS + 1, 3))
        self.assertEqual(x.raw, json.dumps(order(intent="refund")))
        self.assertIn("intent", x.error)

    def test_groq_json_validate_failed_is_treated_as_repairable(self):
        err = BadRequestError(400, {"error": {"code": "json_validate_failed", "message": "bad", "failed_generation": "{oops"}})
        calls = []
        x = self.extractor(scripted(err, order(), calls=calls)).extract_one("e", "t")
        self.assertEqual(x.attempts, 2)
        self.assertEqual(calls[1]["messages"][-2], {"role": "assistant", "content": "{oops"})

    def test_falls_back_to_json_object_when_json_schema_unsupported(self):
        err = BadRequestError(400, {"error": {"message": "response_format json_schema is not supported by this model"}})
        calls, logs = [], []
        ex = self.extractor(scripted(err, order(), calls=calls), log=logs.append)
        x = ex.extract_one("e", "t")
        self.assertEqual((x.attempts, ex.mode), (1, "json_object"))
        self.assertEqual(calls[1]["response_format"], {"type": "json_object"})
        self.assertIn("JSON Schema", calls[1]["messages"][0]["content"])
        self.assertTrue(logs)

    def test_api_error_goes_to_review_without_raising(self):
        err = BadRequestError(401, {"error": {"message": "invalid api key"}})
        x = self.extractor(scripted(err)).extract_one("e", "t")
        self.assertIsNone(x.order)
        self.assertTrue(x.error.startswith("API error"))


class BatchTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        (root / "emails").mkdir()
        for i, body in enumerate(["Please send MS-VLV0100 x 40 nos", "the 2-inch brass valve x 12", "where is my order?"], 1):
            (root / "emails" / f"email_{i:02d}.txt").write_text(body, encoding="utf-8")
        (root / "catalog.csv").write_text("sku,name\nMS-VLV0100,Ball valve\nMS-VLV0200,Brass valve\n", encoding="utf-8")
        self.root, self.out = root, root / "out"

    def tearDown(self):
        self.tmp.cleanup()

    def test_every_email_lands_in_results_or_needs_review(self):
        invented = order(line_items=[{"sku": "MS-VLV9999", "description": "2-inch brass valve", "quantity": 12, "unit": "pcs"}])
        client = scripted(order(), invented, "x", "y", "z")  # email_03 fails 3 times
        s = run_batch(Extractor(client, "m", sleep=lambda s: None), self.root / "emails", self.out, self.root / "catalog.csv")
        results = [json.loads(line) for line in (self.out / "results.jsonl").read_text(encoding="utf-8").splitlines()]
        review = [json.loads(line) for line in (self.out / "needs_review.jsonl").read_text(encoding="utf-8").splitlines()]
        self.assertEqual(([r["email_id"] for r in results], [r["email_id"] for r in review]), (["email_01", "email_02"], ["email_03"]))
        self.assertEqual((s["validated"], s["needs_review"], s["total"]), (2, 1, 3))
        self.assertEqual((review[0]["attempts"], review[0]["raw"]), (3, "z"))
        self.assertIn("error", review[0])
        self.assertEqual(s["sku_not_in_catalog"], ["email_02:MS-VLV9999"])
        self.assertEqual(s["sku_not_in_email"], ["email_02:MS-VLV9999"])
        self.assertEqual(s["input_tokens"], 500)

    def test_stale_needs_review_removed_when_all_validate(self):
        self.out.mkdir()
        (self.out / "needs_review.jsonl").write_text("old\n", encoding="utf-8")
        s = run_batch(Extractor(scripted(order(), order(), order()), "m"), self.root / "emails", self.out, self.root / "catalog.csv")
        self.assertEqual((s["validated"], s["needs_review"]), (3, 0))
        self.assertFalse((self.out / "needs_review.jsonl").exists())


class AccuracyTests(unittest.TestCase):
    LABELS = {"labels": [
        {"email_id": "email_01", "customer_name": "Kaveri Engineering Works", "intent": "order", "secondary_intents": [],
         "line_items": [{"sku": "MS-VLV0100", "description": None, "quantity": 40, "unit": "pcs"}],
         "requested_date": "2026-10-12", "urgency": "normal"},
        {"email_id": "email_02", "customer_name": "Bharat Pumps & Valves", "intent": "order", "secondary_intents": ["other"],
         "line_items": [{"sku_mentioned": None, "description": "2-inch brass valve", "quantity": 12, "unit": "pcs", "resolved_sku": "MS-VLV0200"}],
         "requested_date": "2026-10-31", "urgency": "normal"},
        {"email_id": "email_03", "customer_name": "X Ltd", "intent": "status", "secondary_intents": [], "line_items": [],
         "requested_date": None, "urgency": "low"},
    ]}

    def write(self, tmp, results):
        lp, rp = Path(tmp) / "labels.json", Path(tmp) / "results.jsonl"
        lp.write_text(json.dumps(self.LABELS), encoding="utf-8")
        rp.write_text("".join(json.dumps({"email_id": k, "model": "m", "order": v}) + "\n" for k, v in results.items()), encoding="utf-8")
        return rp, lp

    def test_field_level_scores_and_missing_counts_as_wrong(self):
        with tempfile.TemporaryDirectory() as tmp:
            rp, lp = self.write(tmp, {
                "email_01": order(customer="Kaveri Engineering Works, Bengaluru",
                                  line_items=[{"sku": "MS-VLV0100", "description": "valve", "quantity": 40, "unit": "nos"}]),
                "email_02": order(customer="Bharat Pumps and Valves", secondary_intents=[], requested_date="2026-10-31",
                                  line_items=[{"sku": "MS-VLV0200", "description": "2-inch brass valve", "quantity": 12, "unit": "pcs"}]),
            })  # email_03 missing (e.g. needs_review)
            r = accuracy(rp, lp)
        self.assertEqual(r["n"], 3)
        self.assertEqual(r["correct"], {"customer": 2, "intent": 2, "secondary_intents": 1, "line_items": 1, "requested_date": 2, "urgency": 2})
        self.assertAlmostEqual(r["overall"], 10 / 18)
        self.assertEqual(r["missing"], ["email_03"])
        self.assertIn(("email_02", "line_items"), [(m[0], m[1]) for m in r["misses"]])  # guessed SKU for a described product
        report = format_report(r)
        self.assertIn("overall", report)
        self.assertIn("55.6%", report)

    def test_item_matching_rules(self):
        self.assertTrue(item_matches({"sku": "MS-A", "quantity": 4, "unit": "carboy"}, {"sku": "MS-A", "quantity": 4, "unit": "carboys"}))
        self.assertFalse(item_matches({"sku": "MS-A", "quantity": 4, "unit": "pcs"}, {"sku": "MS-A", "quantity": 5, "unit": "pcs"}))
        self.assertTrue(item_matches({"sku_mentioned": None, "quantity": 2, "unit": None}, {"sku": None, "quantity": 2, "unit": "box"}))
        self.assertTrue(field_correct("secondary_intents", {"secondary_intents": ["other", "return"]}, {"secondary_intents": ["return", "other"]}))
        self.assertFalse(field_correct("customer", {"customer_name": "Acme"}, {"customer": "Zenith"}))

    def test_cli_accuracy_one_command(self):
        with tempfile.TemporaryDirectory() as tmp:
            rp, lp = self.write(tmp, {"email_01": order()})
            (Path(tmp) / "ground_truth").mkdir()
            lp.rename(Path(tmp) / "ground_truth" / "email_labels.json")
            out = io.StringIO()
            with mock.patch.dict(os.environ, {"DESK_DATA_DIR": tmp}), mock.patch.object(cli, "OUT_DIR", Path(tmp)), mock.patch("sys.stdout", out):
                code = cli.cmd_accuracy(cli.build_parser().parse_args(["accuracy"]))
        self.assertEqual(code, 0)
        self.assertIn("Field-level accuracy on 3", out.getvalue())

    def test_cli_accuracy_without_results_fails_clearly(self):
        with tempfile.TemporaryDirectory() as tmp, mock.patch.object(cli, "OUT_DIR", Path(tmp)), mock.patch("sys.stderr", io.StringIO()) as err:
            self.assertEqual(cli.cmd_accuracy(None), 1)
        self.assertIn("desk extract", err.getvalue())


if __name__ == "__main__":
    unittest.main()
