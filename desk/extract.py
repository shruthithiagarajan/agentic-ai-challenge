"""Challenge 3: email -> OrderRequest with Groq structured outputs, a validation repair loop,
a batch runner (results.jsonl / needs_review.jsonl) and a field-level accuracy report.

Ground-truth labels are only read by `accuracy()` for scoring; they are never sent to the model.
"""
import csv
import json
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from pydantic import ValidationError

from .llm import LLMError, call_with_retry
from .schemas import OrderRequest, response_schema

MAX_REPAIRS = 2
BATCH_RETRY = dict(max_attempts=6, base_delay=2.0)  # 2+4+8+16+32s: rides out Groq's per-minute token cap
FIELDS = ["customer", "intent", "secondary_intents", "line_items", "requested_date", "urgency"]

EXTRACT_PROMPT = """You turn customer emails sent to Meridian Supply Co. (industrial supplies distributor) into JSON matching the OrderRequest schema. Reply with the JSON object only.

Rules:
- customer: the sender's company name from the signature (the person's name only if there is no company).
- intent = the ONE primary thing the sender needs: order (new or repeat purchase), status (where is my order / ETA), complaint (problem with goods or service received), return (send goods back / refund), other (quotes, invoices, documents, account or logistics questions, anything else).
- secondary_intents: every other request in the same email, never repeating the primary; [] if none. Side requests such as a quote, invoice copy, pickup arrangement or "cancel if late" count as other.
- line_items: products the email names for ordering, returning or complaining about; [] if none.
  - sku: copy it exactly ONLY if the email writes it (format MS-ABC1234). If the product is only described in words, sku is null. Never guess a SKU.
  - unit: "pcs" for nos/pieces/units/numbers; otherwise the singular unit as written (box, pair, drum, carboy, m).
- requested_date: the delivery/need-by date as YYYY-MM-DD, resolving phrases like "by 12 Oct" or "next Friday" from the email's Date header; keep the phrase if it cannot be resolved; null if no date is requested.
- urgency: high = urgent/ASAP/production stopped/very tight deadline; low = no time pressure (general enquiry, "whenever convenient"); normal = everything else.
- confidence: 0-1, how sure you are of the whole extraction."""

REPAIR_PROMPT = "Your JSON failed validation against the OrderRequest schema:\n{error}\nReturn the corrected JSON object only, no commentary."


def validation_message(err):
    """Pydantic's own error messages, one per line, without doc URLs."""
    return "\n".join(f"- {'.'.join(map(str, e['loc'])) or '(root)'}: {e['msg']}" for e in err.errors(include_url=False))


def _error_body(exc):
    body = getattr(exc, "body", None)
    if isinstance(body, dict) and isinstance(body.get("error"), dict):
        body = body["error"]
    return body if isinstance(body, dict) else {}


def unsupported_format(exc):
    """True if the API rejected json_schema mode itself (then fall back to json_object)."""
    msg = f"{_error_body(exc).get('message', '')} {exc}".lower()
    return getattr(exc, "status_code", None) == 400 and "json_validate_failed" not in msg and (
        "json_schema" in msg or "response_format" in msg)


def failed_generation(exc):
    """Groq returns 400 json_validate_failed with the model's bad output: treat it as a repairable answer."""
    body = _error_body(exc)
    if body.get("code") == "json_validate_failed" or "json_validate_failed" in str(exc):
        return body.get("failed_generation") or ""
    return None


@dataclass
class Extraction:
    email_id: str
    order: Optional[OrderRequest] = None
    attempts: int = 0
    raw: Optional[str] = None
    error: Optional[str] = None
    input_tokens: int = 0
    output_tokens: int = 0
    latency_s: float = 0.0


@dataclass
class Extractor:
    client: object
    model: str
    temperature: float = 0.0
    sleep: object = time.sleep
    log: object = lambda m: None
    mode: str = "json_schema"  # switches to "json_object" (schema in prompt) if the model rejects json_schema
    _schema: dict = field(default_factory=response_schema)

    def system_prompt(self):
        if self.mode == "json_schema":
            return EXTRACT_PROMPT
        return EXTRACT_PROMPT + "\n\nJSON Schema:\n" + json.dumps(self._schema)

    def _create(self, messages):
        fmt = ({"type": "json_schema", "json_schema": {"name": "OrderRequest", "schema": self._schema, "strict": False}}
               if self.mode == "json_schema" else {"type": "json_object"})
        kwargs = dict(model=self.model, messages=messages, temperature=self.temperature, response_format=fmt)
        return call_with_retry(lambda: self.client.chat.completions.create(**kwargs), sleep=self.sleep, log=self.log, **BATCH_RETRY)[0]

    def extract_one(self, email_id, text):
        """Validate or give up after MAX_REPAIRS re-prompts. Never raises for model or API errors."""
        x = Extraction(email_id)
        messages = [{"role": "system", "content": self.system_prompt()}, {"role": "user", "content": f"Email:\n{text}"}]
        t0 = time.perf_counter()
        while x.attempts <= MAX_REPAIRS:
            x.attempts += 1
            try:
                resp = self._create(messages)
            except LLMError as e:
                cause = e.__cause__
                if self.mode == "json_schema" and unsupported_format(cause):
                    self.mode = "json_object"
                    self.log(f"{self.model} rejected json_schema mode; falling back to json_object + schema in prompt")
                    messages[0]["content"] = self.system_prompt()
                    x.attempts -= 1
                    continue
                bad = failed_generation(cause)
                if bad is None:  # not the model's output at fault (rate limit exhausted, auth, network...)
                    x.error = f"API error: {e}"
                    break
                x.raw, x.error = bad, "the API rejected the output: it did not match the JSON schema"
            else:
                usage = getattr(resp, "usage", None)
                x.input_tokens += getattr(usage, "prompt_tokens", 0) or 0
                x.output_tokens += getattr(usage, "completion_tokens", 0) or 0
                x.raw = resp.choices[0].message.content or ""
                try:
                    x.order, x.error = OrderRequest.model_validate_json(x.raw), None
                    break
                except ValidationError as ve:
                    x.error = validation_message(ve)
            if x.attempts <= MAX_REPAIRS:
                messages += [{"role": "assistant", "content": x.raw or ""},
                             {"role": "user", "content": REPAIR_PROMPT.format(error=x.error)}]
        x.latency_s = time.perf_counter() - t0
        return x


def catalog_skus(path):
    with open(path, newline="", encoding="utf-8") as f:
        return {row["sku"] for row in csv.DictReader(f)}


def run_batch(extractor, emails_dir, out_dir, catalog_path, log=lambda m: None):
    """Extract every email_*.txt. Each one lands in results.jsonl OR needs_review.jsonl (files are rewritten)."""
    emails = sorted(Path(emails_dir).glob("email_*.txt"))
    if not emails:
        raise FileNotFoundError(f"no email_*.txt files in {emails_dir}")
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    results_path, review_path = out_dir / "results.jsonl", out_dir / "needs_review.jsonl"
    known = catalog_skus(catalog_path)
    s = dict(total=len(emails), validated=0, repaired=0, needs_review=0, input_tokens=0, output_tokens=0,
             latency_s=0.0, sku_not_in_catalog=[], sku_not_in_email=[], results=str(results_path), review=None)
    reviews = []
    with open(results_path, "w", encoding="utf-8") as out:
        for path in emails:
            text = path.read_text(encoding="utf-8")
            x = extractor.extract_one(path.stem, text)
            s["input_tokens"] += x.input_tokens
            s["output_tokens"] += x.output_tokens
            s["latency_s"] += x.latency_s
            meta = dict(email_id=x.email_id, model=extractor.model, attempts=x.attempts)
            if x.order is None:
                reviews.append({**meta, "error": x.error, "raw": x.raw})
                log(f"{x.email_id}: needs_review after {x.attempts} attempt(s): {(x.error or '').splitlines()[0][:100]}")
                continue
            s["validated"] += 1
            s["repaired"] += x.attempts > 1
            for item in x.order.line_items:
                if item.sku and item.sku not in known:
                    s["sku_not_in_catalog"].append(f"{x.email_id}:{item.sku}")
                if item.sku and item.sku not in text:
                    s["sku_not_in_email"].append(f"{x.email_id}:{item.sku}")
            out.write(json.dumps({**meta, "input_tokens": x.input_tokens, "output_tokens": x.output_tokens,
                                  "latency_s": round(x.latency_s, 2), "order": x.order.model_dump()}, ensure_ascii=False) + "\n")
            log(f"{x.email_id}: ok ({x.attempts} attempt{'s' if x.attempts > 1 else ''})")
    s["needs_review"] = len(reviews)
    if reviews:
        review_path.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in reviews), encoding="utf-8")
        s["review"] = str(review_path)
    elif review_path.exists():
        review_path.unlink()  # stale file from an earlier run
    return s


# ---- accuracy -------------------------------------------------------------------

UNIT_ALIASES = {"nos": "pcs", "no": "pcs", "pc": "pcs", "piece": "pcs", "pieces": "pcs", "unit": "pcs", "units": "pcs",
                "numbers": "pcs", "ea": "pcs", "each": "pcs", "boxes": "box", "pairs": "pair", "drums": "drum",
                "carboys": "carboy", "meter": "m", "meters": "m", "metre": "m", "metres": "m"}


def norm_unit(u):
    u = (u or "").strip().lower().rstrip(".")
    return UNIT_ALIASES.get(u, u)


def norm_name(s):
    return re.sub(r"[^a-z0-9]+", " ", (s or "").lower().replace("&", " and ")).strip()


def item_matches(label, pred):
    if pred.get("quantity") != label.get("quantity"):
        return False
    if label.get("unit") and norm_unit(label["unit"]) != norm_unit(pred.get("unit")):
        return False
    label_sku = label.get("sku") or label.get("sku_mentioned")
    return pred.get("sku") == label_sku  # described-only products must keep sku null


def items_match(labels, preds):
    if len(labels) != len(preds):
        return False
    left = list(preds)
    for lab in labels:
        hit = next((p for p in left if item_matches(lab, p)), None)
        if hit is None:
            return False
        left.remove(hit)
    return True


def field_correct(name, label, pred):
    if name == "customer":
        a, b = norm_name(label.get("customer_name")), norm_name(pred.get("customer"))
        return bool(a and b) and (a == b or a in b or b in a)
    if name == "secondary_intents":
        return set(label.get(name) or []) == set(pred.get(name) or [])
    if name == "line_items":
        return items_match(label.get(name) or [], pred.get(name) or [])
    return label.get(name) == pred.get(name)


def load_labels(path):
    """{email_id: label} from a JSON file ({"labels": [...]} or a list) or JSONL."""
    text = Path(path).read_text(encoding="utf-8")
    try:
        data = json.loads(text)
        rows = data["labels"] if isinstance(data, dict) else data
    except json.JSONDecodeError:
        rows = [json.loads(line) for line in text.splitlines() if line.strip()]
    return {r["email_id"]: r for r in rows}


def load_results(path):
    with open(path, encoding="utf-8") as f:
        return {r["email_id"]: r for r in map(json.loads, filter(str.strip, f))}


def accuracy(results_path, labels_path):
    """Field-level accuracy over the labelled emails. Labelled emails with no validated result score 0."""
    labels, results = load_labels(labels_path), load_results(results_path)
    correct = {f: 0 for f in FIELDS}
    misses, missing = [], []
    for eid, label in sorted(labels.items()):
        pred = (results.get(eid) or {}).get("order")
        if pred is None:
            missing.append(eid)
            continue
        for f in FIELDS:
            if field_correct(f, label, pred):
                correct[f] += 1
            else:
                exp = label.get("customer_name") if f == "customer" else label.get(f)
                misses.append((eid, f, exp, pred.get(f)))
    n = len(labels)
    models = sorted({r.get("model") for r in results.values()} - {None})
    return dict(n=n, correct=correct, overall=sum(correct.values()) / (n * len(FIELDS)) if n else 0.0,
                missing=missing, misses=misses, models=models)


def format_report(r):
    lines = [f"Field-level accuracy on {r['n']} hand-labelled emails (model: {', '.join(r['models']) or '?'})", ""]
    for f in FIELDS:
        c = r["correct"][f]
        lines.append(f"  {f:<18} {c:>2}/{r['n']}  {c / r['n']:6.1%}" if r["n"] else f"  {f}: n/a")
    lines += ["  " + "-" * 34, f"  {'overall':<18} {r['overall']:13.1%}"]
    if r["missing"]:
        lines.append(f"\nNo validated result (scored 0): {', '.join(r['missing'])}")
    if r["misses"]:
        lines.append("\nMismatches:")
        lines += [f"  {eid} {f}: expected {json.dumps(exp, ensure_ascii=False)} | got {json.dumps(got, ensure_ascii=False)}"
                  for eid, f, exp, got in r["misses"]]
    return "\n".join(lines)
