"""Challenge 6: the tools Desk can call, described with JSON-schema parameters.

Five read-only tools: get_order (orders.db), search_catalog / check_stock (catalog.csv), calculate (a safe
arithmetic evaluator - never eval), search_policies (the Challenge 5 retriever). `Toolbox.run(name, raw_args)`
validates the arguments against the schema, runs the tool and ALWAYS returns a dict: {"ok": true, "result": ...}
or {"ok": false, "error": {"code", "message"}}. A bad argument, a missing order or a crashed tool goes back to
the model as data, never as an exception.
"""
import ast
import csv
import json
import math
import operator
import re
import sqlite3
import time
from datetime import date
from pathlib import Path

ORDER_ID = r"^MS-\d{5}$"
SKU_ID = r"^MS-[A-Z]{3}\d{4}$"
CATEGORIES = ["Bearings", "Electrical", "Fasteners", "Fittings", "Gaskets & Seals", "Hazardous Goods", "Lubricants",
              "Pipes", "PPE", "Tools", "Valves"]
CATALOG_LIMIT = 10  # rows returned by search_catalog
POLICY_TEXT_CHARS = 1500  # per policy section returned by search_policies
MAX_EXPRESSION_CHARS = 200

# Written like docs for a new teammate: what it does, when to use it, what it returns, what it can't do.
TOOLS = [
    {"type": "function", "function": {
        "name": "get_order",
        "description": (
            "Look up ONE customer order in Meridian's live order database by its order id. Returns the current "
            "status (pending, processing, shipped, delivered, delayed, cancelled, refunded), the order / promised / "
            "shipped / delivered dates, carrier and tracking number, the order total in INR, notes, the customer "
            "(name, contact, tier, city), every order line (SKU, product name, quantity, unit, unit price charged, "
            "line total) and days_late (days past the promised date, 0 if on time). Use it for any question about a "
            "specific order: where it is, when it arrives, what is in it, what was charged. Order ids look like "
            "MS-10422 (MS- and 5 digits); product SKUs such as MS-VLV0200 are NOT order ids. If no such order "
            "exists it returns the error order_not_found. Read-only: it cannot cancel or change an order."),
        "parameters": {"type": "object", "additionalProperties": False, "required": ["order_id"], "properties": {
            "order_id": {"type": "string", "pattern": ORDER_ID,
                         "description": "The order id exactly as written, e.g. MS-10422."}}}}},
    {"type": "function", "function": {
        "name": "search_catalog",
        "description": (
            "Search Meridian's product catalog (200 industrial products) by words in the product name, category "
            "or specs (size, material, connection...), or by an exact SKU. Returns up to 10 best matches, each with "
            "sku, name, category, unit_price (INR per unit of sale, list price before any bulk discount), unit "
            "(pcs, box, drum...) and stock. Use it to find products ('stainless steel ball valves'), to get a "
            "product's list price, or to turn a description into a SKU. 'Stainless steel' items are named SS304 / "
            "SS316. Optional filters narrow the results; leave them out when not needed."),
        "parameters": {"type": "object", "additionalProperties": False, "required": ["query"], "properties": {
            "query": {"type": "string", "minLength": 1, "maxLength": 200,
                      "description": "Words or a SKU to look for, e.g. 'brass ball valve 2 inch' or 'MS-VLV0200'."},
            "filters": {"type": "object", "additionalProperties": False,
                        "description": "Optional. Every filter given must match.", "properties": {
                            "category": {"type": "string", "enum": CATEGORIES, "description": "Only this category."},
                            "max_unit_price": {"type": "number", "minimum": 0,
                                               "description": "Only products whose unit_price (INR) is at most this."},
                            "min_stock": {"type": "integer", "minimum": 0,
                                          "description": "Only products with at least this many units in stock."}}}}}}},
    {"type": "function", "function": {
        "name": "check_stock",
        "description": (
            "Return the current stock level of ONE product SKU: units on hand, the unit of sale (pcs, box, drum...) "
            "and in_stock (true/false). Use it for 'do we have', 'any left', 'how many units' questions. SKUs look "
            "like MS-VLV0200 (MS-, 3 capital letters, 4 digits). For several SKUs, call it once per SKU in the "
            "same turn. It does not return prices or product details - use search_catalog for those. Unknown SKUs "
            "return the error sku_not_found."),
        "parameters": {"type": "object", "additionalProperties": False, "required": ["sku"], "properties": {
            "sku": {"type": "string", "pattern": SKU_ID, "description": "The product SKU, e.g. MS-VLV0200."}}}}},
    {"type": "function", "function": {
        "name": "calculate",
        "description": (
            "Evaluate one arithmetic expression exactly and return the number. Use it for EVERY calculation - "
            "totals, discounts, differences, percentages, averages - never do arithmetic yourself. Supports numbers, "
            "+ - * / // ( ), ** (exponent at most 10), % (remainder, NOT percent), and round(x, n), min, max, abs. "
            "Write percentages as decimals: 12% off 340 units at 1875 each is 340 * 1875 * (1 - 0.12). No thousands "
            "separators (1875, not 1,875), no currency symbols or units, no variables."),
        "parameters": {"type": "object", "additionalProperties": False, "required": ["expression"], "properties": {
            "expression": {"type": "string", "minLength": 1, "maxLength": MAX_EXPRESSION_CHARS,
                           "description": "The expression, e.g. '1875 * (1 - 0.12)'."}}}}},
    {"type": "function", "function": {
        "name": "search_policies",
        "description": (
            "Search Meridian's policy documents (returns, opened items, shipping, delays, bulk pricing, warranty, "
            "cancellations, credit terms, customer tiers, damaged goods, refunds, hazardous goods, international "
            "shipping, data privacy) and return the 5 most relevant sections. Each result has an id such as "
            "returns.md#3 - cite it in square brackets in your answer - plus the document title, section heading, "
            "effective date and text. When two sections give different rules, the later effective date applies; a "
            "'note' field marks the newer and older rule. Use it for any question about rules, time windows, fees, "
            "discount tiers, eligibility or procedures. Send a specific standalone query, e.g. 'bulk discount tiers "
            "by quantity'."),
        "parameters": {"type": "object", "additionalProperties": False, "required": ["query"], "properties": {
            "query": {"type": "string", "minLength": 1, "maxLength": 300,
                      "description": "What to look for in the policies."}}}}},
]

STATUSES = ["pending", "processing", "shipped", "delivered", "delayed", "cancelled", "refunded"]
EMAIL = r"^[^@\s]+@[^@\s]+\.[^@\s]+$"
MAX_ORDERS = 50

# Challenge 7: extra tools for the agent loop (desk agent). desk tools (Ch6) keeps the five above.
AGENT_TOOLS = [
    {"type": "function", "function": {
        "name": "list_orders",
        "description": (
            "List ONE customer's orders from the live order database, newest first. Find the customer by any part "
            "of the company name (case-insensitive, e.g. 'Apex') or by customer id (e.g. CUST-0001). Optional "
            "filters: status, and min_days_late (only orders at least this many days past the promised date; "
            "days_late counts to the delivery date, or to today if not delivered). Each order has order_id, status, "
            "order/promised/delivered dates, days_late, total_inr and its lines (sku, quantity, unit). The customer "
            "block includes the contact name and email address. Use it when the user names a customer instead of "
            "an order id; use get_order for full details of one order. At most 50 orders are returned ('truncated' "
            "says if more matched). Errors: customer_not_found, ambiguous_customer (lists the candidates)."),
        "parameters": {"type": "object", "additionalProperties": False, "required": ["customer"], "properties": {
            "customer": {"type": "string", "minLength": 2, "maxLength": 100,
                         "description": "Company name or part of it, or a customer id like CUST-0001."},
            "status": {"type": "string", "enum": STATUSES, "description": "Only orders with this status."},
            "min_days_late": {"type": "integer", "minimum": 0,
                              "description": "Only orders at least this many days late, e.g. 6 for 'more than 5 days'."}}}}},
    {"type": "function", "function": {
        "name": "draft_email",
        "description": (
            "Save an email DRAFT to the outbox folder for a Meridian staff member to review and send. It does NOT "
            "send anything and nobody receives it until a person does. Returns the saved file path. Use one call per "
            "message. Write the complete email: greeting, the facts (order ids, dates, amounts from tool results), "
            "any policy clause quoted with its id, and a sign-off from 'Meridian Supply Co. Customer Operations'. "
            "Never tell the user an email was sent - say it was drafted."),
        "parameters": {"type": "object", "additionalProperties": False, "required": ["to", "subject", "body"],
                       "properties": {
                           "to": {"type": "string", "pattern": EMAIL, "maxLength": 200,
                                  "description": "Recipient email address, e.g. the customer's email from list_orders."},
                           "subject": {"type": "string", "minLength": 1, "maxLength": 200, "description": "Subject line."},
                           "body": {"type": "string", "minLength": 1, "maxLength": 8000,
                                    "description": "Plain-text email body."}}}}},
]

# Stretch (desk agent --plan): a checklist the agent writes before it starts.
TODO_TOOL = {"type": "function", "function": {
    "name": "todo",
    "description": (
        "Record your plan as a short checklist. Call it FIRST, before any other tool, with every step 'pending'; "
        "then call it again with the full list whenever steps change status ('in_progress', 'done'). It only "
        "records the plan and returns it - it does not do any of the work."),
    "parameters": {"type": "object", "additionalProperties": False, "required": ["items"], "properties": {
        "items": {"type": "array", "minItems": 1, "maxItems": 20, "description": "The whole plan, in order.",
                  "items": {"type": "object", "additionalProperties": False, "required": ["step", "status"],
                            "properties": {
                                "step": {"type": "string", "minLength": 1, "maxLength": 200},
                                "status": {"type": "string", "enum": ["pending", "in_progress", "done"]}}}}}}}}


def agent_tool_specs(plan=False):
    """Tools for desk agent: the five Ch6 tools + list_orders + draft_email (+ todo with --plan)."""
    return json.loads(json.dumps(TOOLS + AGENT_TOOLS + ([TODO_TOOL] if plan else [])))


# Stretch: one deliberately vague description, to watch tool selection degrade (desk tools --vague).
VAGUE = {"check_stock": {"description": "Gets product data.", "sku": "an id"}}


def tool_specs(vague=False):
    """The tool list sent to the model; vague=True swaps in the bad check_stock description."""
    specs = json.loads(json.dumps(TOOLS))
    if vague:
        for spec in specs:
            bad = VAGUE.get(spec["function"]["name"])
            if bad:
                spec["function"]["description"] = bad["description"]
                spec["function"]["parameters"]["properties"]["sku"]["description"] = bad["sku"]
    return specs


SCHEMAS = {t["function"]["name"]: t["function"]["parameters"] for t in TOOLS + AGENT_TOOLS + [TODO_TOOL]}


class ToolError(Exception):
    """An expected failure, reported to the model as {"ok": false, "error": {"code", "message"}}."""

    def __init__(self, code, message):
        super().__init__(message)
        self.code, self.message = code, message


# ---- argument validation (the subset of JSON Schema the tools use) ---------

_TYPES = {"object": dict, "string": str, "integer": int, "number": (int, float), "boolean": bool, "array": list}


def validate(schema, value, path="arguments"):
    """List of problems with `value` against `schema` (empty = valid)."""
    kind = schema.get("type")
    if kind:
        ok = isinstance(value, _TYPES[kind]) and not (kind in ("integer", "number") and isinstance(value, bool))
        if kind == "integer" and isinstance(value, float) and value.is_integer():
            ok = True
        if not ok:
            return [f"{path} must be {'an' if kind[0] in 'aeiou' else 'a'} {kind}"]
    problems = []
    if "enum" in schema and value not in schema["enum"]:
        problems.append(f"{path} must be one of {schema['enum']}")
    if kind == "object":
        props = schema.get("properties", {})
        problems += [f"{path}.{k} is required" for k in schema.get("required", []) if k not in value]
        if schema.get("additionalProperties") is False:
            problems += [f"{path}.{k} is not a known parameter (allowed: {', '.join(props)})" for k in value if k not in props]
        for k, v in value.items():
            if k in props:
                problems += validate(props[k], v, f"{path}.{k}")
    if kind == "string":
        if len(value) < schema.get("minLength", 0):
            problems.append(f"{path} must not be empty")
        if len(value) > schema.get("maxLength", math.inf):
            problems.append(f"{path} is longer than {schema['maxLength']} characters")
        if "pattern" in schema and not re.fullmatch(schema["pattern"], value):
            problems.append(f"{path} {value[:60]!r} does not match the format {schema['pattern']}")
    if kind == "array":
        if len(value) < schema.get("minItems", 0):
            problems.append(f"{path} needs at least {schema['minItems']} item(s)")
        if len(value) > schema.get("maxItems", math.inf):
            problems.append(f"{path} has more than {schema['maxItems']} items")
        if "items" in schema:
            for i, item in enumerate(value):
                problems += validate(schema["items"], item, f"{path}[{i}]")
    if kind in ("integer", "number"):
        if value < schema.get("minimum", -math.inf):
            problems.append(f"{path} must be at least {schema['minimum']}")
        if value > schema.get("maximum", math.inf):
            problems.append(f"{path} must be at most {schema['maximum']}")
    return problems


def _drop_nulls(value):
    """Models often send optional parameters as null; treat that as 'not given'."""
    if isinstance(value, dict):
        return {k: _drop_nulls(v) for k, v in value.items() if v is not None}
    return value


# ---- calculate: a safe arithmetic evaluator ---------------------------------

_BINARY = {ast.Add: operator.add, ast.Sub: operator.sub, ast.Mult: operator.mul, ast.Div: operator.truediv,
           ast.FloorDiv: operator.floordiv, ast.Mod: operator.mod, ast.Pow: operator.pow}
_UNARY = {ast.UAdd: operator.pos, ast.USub: operator.neg}
_FUNCS = {"round": round, "min": min, "max": max, "abs": abs}
_LIMIT = 1e15
_ALLOWED = "only numbers, + - * / // % ** ( ) and round, min, max, abs are allowed"


def safe_eval(expression):
    """Evaluate arithmetic by walking the parsed tree; names, attributes, strings, imports and calls to anything
    other than round/min/max/abs are rejected before anything runs."""
    expr = (expression or "").strip()
    if len(expr) > MAX_EXPRESSION_CHARS:
        raise ToolError("expression_too_long", f"expression is longer than {MAX_EXPRESSION_CHARS} characters")
    try:
        tree = ast.parse(expr, mode="eval")
    except SyntaxError:
        raise ToolError("invalid_expression", f"could not parse {expr!r}: use * to multiply, decimals for percentages "
                                              "(12% -> 0.12) and no thousands separators or units") from None
    try:
        value = _eval(tree.body)
    except ZeroDivisionError:
        raise ToolError("division_by_zero", "the expression divides by zero") from None
    except (OverflowError, ValueError, TypeError) as e:
        raise ToolError("invalid_expression", str(e)) from None
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ToolError("invalid_expression", "the result is not a finite number")
        value = round(value, 6)
        if value.is_integer():
            value = int(value)
    return value


def _check(value):
    if abs(value) > _LIMIT:
        raise ToolError("number_too_large", f"numbers above {_LIMIT:g} are not supported")
    return value


def _eval(node):
    if isinstance(node, ast.Constant) and type(node.value) in (int, float):
        return _check(node.value)
    if isinstance(node, ast.BinOp) and type(node.op) in _BINARY:
        left, right = _eval(node.left), _eval(node.right)
        if isinstance(node.op, ast.Pow) and abs(right) > 10:
            raise ToolError("number_too_large", "exponents above 10 are not supported")
        return _check(_BINARY[type(node.op)](left, right))
    if isinstance(node, ast.UnaryOp) and type(node.op) in _UNARY:
        return _UNARY[type(node.op)](_eval(node.operand))
    if (isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id in _FUNCS
            and not node.keywords and node.args):
        args = [_eval(a) for a in node.args]
        if node.func.id == "round" and (len(args) > 2 or (len(args) == 2 and not float(args[1]).is_integer())):
            raise ToolError("invalid_expression", "round takes a number and a whole number of digits")
        if node.func.id == "round" and len(args) == 2:
            args[1] = int(args[1])
        return _check(_FUNCS[node.func.id](*args))
    if isinstance(node, ast.Tuple):
        raise ToolError("unsupported_expression", "commas are not allowed - write 1875, not 1,875")
    raise ToolError("unsupported_expression", f"{type(node).__name__} is not allowed: {_ALLOWED}")


# ---- the toolbox -------------------------------------------------------------

def _words(text):
    """Search tokens: lower-case words, plural 's' dropped, 'stainless' -> 'ss' (catalog names say SS304/SS316)."""
    out = []
    for w in re.findall(r"[a-z0-9]+", (text or "").lower()):
        w = "ss" if w == "stainless" else w
        out.append(w[:-1] if len(w) > 3 and w.endswith("s") and not w.endswith("ss") else w)
    return [w for w in out if w not in {"the", "a", "an", "of", "for", "and", "with", "find", "any", "me", "show"}]


class Toolbox:
    """Runs the tools. `retrieve(query) -> [rag.Hit]` backs search_policies (default: the Ch5 pipeline over .chroma)."""

    def __init__(self, db_path, catalog_path, retrieve=None, fail=None, outbox=None):
        self.db_path, self.catalog_path = Path(db_path), Path(catalog_path)
        self.outbox = Path(outbox) if outbox else self.db_path.parent / "outbox"  # draft_email writes only here
        self._retrieve, self._catalog, self._today = retrieve, None, None
        self.fail = set(fail or [])  # tool names forced to crash (desk tools --fail-tool), to test error handling
        self.plan, self.drafts = [], []  # todo checklist and saved draft paths, for this run
        self.handlers = {"get_order": self.get_order, "search_catalog": self.search_catalog,
                         "check_stock": self.check_stock, "calculate": self.calculate,
                         "search_policies": self.search_policies, "list_orders": self.list_orders,
                         "draft_email": self.draft_email, "todo": self.todo}

    def run(self, name, raw_args):
        """Validate + execute one tool call. Never raises: returns {"ok": ..., "result" | "error": ...}."""
        if name not in self.handlers:
            return _error("unknown_tool", f"there is no tool named {name!r}; tools: {', '.join(self.handlers)}")
        args = raw_args
        if raw_args is None or (isinstance(raw_args, str) and not raw_args.strip()):
            args = {}
        elif isinstance(raw_args, str):
            try:
                args = json.loads(raw_args)
            except ValueError:
                return _error("invalid_json", f"arguments are not valid JSON: {raw_args[:120]!r}")
        args = _drop_nulls(args)
        problems = validate(SCHEMAS[name], args)
        if problems:
            return _error("invalid_arguments", "; ".join(problems))
        try:
            if name in self.fail:
                raise RuntimeError(f"simulated outage of {name}")
            return {"ok": True, "result": self.handlers[name](**args)}
        except ToolError as e:
            return _error(e.code, e.message)
        except Exception as e:  # noqa: BLE001 - any crash becomes a structured result the model can explain
            return _error("tool_failed", f"{name} failed: {type(e).__name__}: {e}")

    # -- orders.db (opened read-only) --

    def _connect(self):
        if not self.db_path.is_file():
            raise ToolError("database_unavailable", f"order database {self.db_path} not found")
        con = sqlite3.connect(f"{self.db_path.resolve().as_uri()}?mode=ro", uri=True)
        con.row_factory = sqlite3.Row
        return con

    def today(self):
        """'Today' in the order data (orders.db meta.as_of_date), else the real date."""
        if self._today is None:
            try:
                con = self._connect()
                try:
                    row = con.execute("SELECT value FROM meta WHERE key = 'as_of_date'").fetchone()
                finally:
                    con.close()
                self._today = row["value"] if row else date.today().isoformat()
            except (ToolError, sqlite3.Error):
                self._today = date.today().isoformat()
        return self._today

    def get_order(self, order_id):
        con = self._connect()
        try:
            order = con.execute("SELECT * FROM orders WHERE id = ?", (order_id,)).fetchone()
            if order is None:
                raise ToolError("order_not_found", f"no order with id {order_id} in the order database")
            cust = con.execute("SELECT id, name, contact_name, tier, city FROM customers WHERE id = ?",
                               (order["customer_id"],)).fetchone()
            lines = con.execute("SELECT sku, quantity, unit, unit_price_charged FROM order_lines WHERE order_id = ? "
                                "ORDER BY id", (order_id,)).fetchall()
        finally:
            con.close()
        names = {r["sku"]: r["name"] for r in self.catalog()}
        o = dict(order)
        return {
            "order_id": o["id"], "status": o["status"], "order_date": o["order_date"],
            "promised_date": o["promised_date"], "shipped_date": o["shipped_date"],
            "delivered_date": o["delivered_date"], "carrier": o["carrier"], "tracking_no": o["tracking_no"],
            "total_inr": o["total"], "notes": o["notes"] or "", "refund_amount_inr": o["refund_amount"],
            "days_late": self._days_late(o), "data_as_of": self.today(),
            "customer": dict(cust) if cust else {"id": o["customer_id"]},
            "lines": [{"sku": l["sku"], "name": names.get(l["sku"], ""), "quantity": l["quantity"], "unit": l["unit"],
                       "unit_price_charged_inr": l["unit_price_charged"],
                       "line_total_inr": round(l["quantity"] * l["unit_price_charged"], 2)} for l in lines],
        }

    def list_orders(self, customer, status=None, min_days_late=None):
        con = self._connect()
        try:
            key = customer.strip()
            found = con.execute("SELECT id, name, contact_name, email, tier, city FROM customers WHERE id = ? COLLATE NOCASE "
                                "OR name LIKE ? ORDER BY id", (key, f"%{key}%")).fetchall()
            if not found:
                raise ToolError("customer_not_found", f"no customer matches {customer!r}")
            if len(found) > 1:
                names = ", ".join(f"{c['name']} ({c['id']})" for c in found[:10])
                raise ToolError("ambiguous_customer", f"{len(found)} customers match {customer!r}: {names} - "
                                                      "call again with the customer id")
            cust = dict(found[0])
            orders = [dict(o) for o in con.execute("SELECT * FROM orders WHERE customer_id = ? ORDER BY order_date DESC, id DESC",
                                                   (cust["id"],))]
            lines = {}
            for l in con.execute("SELECT order_id, sku, quantity, unit FROM order_lines WHERE order_id IN "
                                 "(SELECT id FROM orders WHERE customer_id = ?) ORDER BY id", (cust["id"],)):
                lines.setdefault(l["order_id"], []).append({"sku": l["sku"], "quantity": l["quantity"], "unit": l["unit"]})
        finally:
            con.close()
        out = []
        for o in orders:
            late = self._days_late(o)
            if status and o["status"] != status:
                continue
            if min_days_late is not None and (late is None or late < min_days_late):
                continue
            out.append({"order_id": o["id"], "status": o["status"], "order_date": o["order_date"],
                        "promised_date": o["promised_date"], "delivered_date": o["delivered_date"], "days_late": late,
                        "total_inr": o["total"], "lines": lines.get(o["id"], [])})
        return {"customer": cust, "data_as_of": self.today(), "count": len(out[:MAX_ORDERS]),
                "truncated": len(out) > MAX_ORDERS, "orders": out[:MAX_ORDERS]}

    def _days_late(self, o):
        if o["status"] in ("cancelled", "refunded") or not o["promised_date"]:
            return None
        end = date.fromisoformat(o["delivered_date"] or self.today())
        return max(0, (end - date.fromisoformat(o["promised_date"])).days)

    # -- catalog.csv --

    def catalog(self):
        if self._catalog is None:
            if not self.catalog_path.is_file():
                raise ToolError("catalog_unavailable", f"catalog {self.catalog_path} not found")
            with open(self.catalog_path, newline="", encoding="utf-8") as f:
                self._catalog = list(csv.DictReader(f))
        return self._catalog

    @staticmethod
    def _product(row):
        try:
            unit = json.loads(row.get("specs") or "{}").get("unit", "")
        except ValueError:
            unit = ""
        return {"sku": row["sku"], "name": row["name"], "category": row["category"],
                "unit_price_inr": float(row["unit_price"]), "unit": unit, "stock": int(row["stock"])}

    def search_catalog(self, query, filters=None):
        f = filters or {}
        skus = {s.upper() for s in re.findall(r"\bMS-[A-Za-z]{3}\d{4}\b", query)}
        wanted = _words(re.sub(r"\bMS-[A-Za-z]{3}\d{4}\b", " ", query))
        scored = []
        for row in self.catalog():
            p = self._product(row)
            if f.get("category") and p["category"] != f["category"]:
                continue
            if "max_unit_price" in f and p["unit_price_inr"] > f["max_unit_price"]:
                continue
            if "min_stock" in f and p["stock"] < f["min_stock"]:
                continue
            have = _words(f"{row['sku']} {row['name']} {row['category']} {row.get('specs', '')}")
            score = 100 if p["sku"] in skus else sum(any(h == w or (w == "ss" and h.startswith("ss")) for h in have)
                                                     for w in wanted)
            if score:
                scored.append((score, p))
        scored.sort(key=lambda sp: (-sp[0], sp[1]["sku"]))
        exact = [p for s, p in scored if s == 100]
        if exact:  # a SKU was named: return just those products
            return {"count": len(exact), "results": exact}
        if not scored:
            return {"count": 0, "results": [], "message": f"no products match {query!r}" +
                    (f" with filters {f}" if f else "")}
        best = scored[0][0]
        top = [p for s, p in scored if s >= min(best, max(1, math.ceil(len(wanted) / 2)))][:CATALOG_LIMIT]
        return {"count": len(top), "results": top}

    def check_stock(self, sku):
        for row in self.catalog():
            if row["sku"] == sku:
                p = self._product(row)
                return {"sku": sku, "name": p["name"], "stock": p["stock"], "unit": p["unit"],
                        "in_stock": p["stock"] > 0}
        raise ToolError("sku_not_found", f"no product with SKU {sku} in the catalog")

    # -- drafts (the only tool that writes, and only into the outbox folder) --

    def draft_email(self, to, subject, body):
        self.outbox.mkdir(parents=True, exist_ok=True)
        slug = re.sub(r"[^a-z0-9]+", "-", subject.lower()).strip("-")[:50] or "draft"
        stamp = time.strftime("%Y%m%d-%H%M%S")
        n = len(list(self.outbox.glob("*.txt"))) + 1
        path = self.outbox / f"{stamp}_{n:03d}_{slug}.txt"
        text = (f"To: {to}\nSubject: {subject}\nStatus: DRAFT - NOT SENT (saved by Desk for staff review)\n"
                f"Saved: {time.strftime('%Y-%m-%d %H:%M:%S')}\n\n{body.strip()}\n")
        path.write_text(text, encoding="utf-8")
        self.drafts.append(str(path))
        return {"saved": str(path), "sent": False, "to": to, "subject": subject, "body_chars": len(body)}

    # -- todo (stretch) --

    def todo(self, items):
        self.plan = items
        done = sum(i["status"] == "done" for i in items)
        return {"plan": items, "done": done, "total": len(items)}

    # -- calculator --

    def calculate(self, expression):
        return {"expression": expression, "result": safe_eval(expression)}

    # -- policies (Challenge 5 retriever) --

    def search_policies(self, query):
        if self._retrieve is None:
            self._retrieve = default_policy_retriever()
        hits = self._retrieve(query)
        if not hits:
            raise ToolError("index_empty", "no policy sections are indexed - run `desk ingest`")
        out = []
        for h in hits:
            m = h.meta or {}
            text = h.text if len(h.text) <= POLICY_TEXT_CHARS else h.text[:POLICY_TEXT_CHARS] + " ..."
            out.append({"id": h.id, "title": m.get("title", ""), "section": m.get("heading", ""),
                        "effective_date": m.get("effective_date", ""), "note": h.note, "text": text})
        return {"count": len(out), "results": out}


def default_policy_retriever():
    """Challenge 5 hybrid search over the .chroma index, built once."""
    from . import rag

    try:
        corpus = rag.Corpus(rag.open_collection())
    except ImportError as e:
        raise ToolError("index_unavailable", f"{e.name or 'chromadb / rank-bm25'} is not installed") from None
    if not corpus.ids:
        raise ToolError("index_empty", "no policy sections are indexed - run `desk ingest`")
    return lambda query: rag.search(corpus, query)


def _error(code, message):
    return {"ok": False, "error": {"code": code, "message": message}}


def timed_run(toolbox, name, raw_args):
    """(result dict, milliseconds)."""
    t0 = time.perf_counter()
    result = toolbox.run(name, raw_args)
    return result, (time.perf_counter() - t0) * 1000
