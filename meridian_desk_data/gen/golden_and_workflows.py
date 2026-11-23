"""Golden retrieval set (40), refund workflow requests, fraud signals, approver decisions."""
import json, os, sqlite3, random, csv
import os as _os; OUT = _os.environ.get("MERIDIAN_OUT", _os.path.join(_os.path.dirname(_os.path.abspath(__file__)), "..", "meridian_desk_data"))
for d in ("evals", "workflows"):
    os.makedirs(f"{OUT}/{d}", exist_ok=True)
random.seed(5)

G = []


def g(q, cat, secs, ans, newer=False, hist=None):
    G.append(dict(id=f"g{len(G) + 1:03d}", question=q, category=cat, expected_sections=secs, expected_answer=ans, must_cite_newer_doc=newer, chat_history=hist or []))


g("How long do I have to return unopened goods?", "plain", ["returns.md#2"], "30 days from delivery for a full refund or credit note.")
g("Is there a restocking fee for returning unopened items?", "table", ["returns.md#6"], "No, unopened unused items carry no restocking fee.")
g("What is the return window for opened items?", "contradiction", ["returns_opened_items.md#2", "returns.md#3"], "7 days per the updated terms effective 2026-03-01; this supersedes the older 14-day window in the Returns Policy (effective 2025-01-01).", True)
g("What restocking fee applies when I return opened items?", "contradiction", ["returns_opened_items.md#2", "returns.md#3"], "20% of line value per the updated terms effective 2026-03-01 (older policy said 10%).", True)
g("How do I raise a return request?", "plain", ["returns.md#4"], "Email returns@meridiansupply.example with order ID, SKU, quantity and reason; an RMA number is issued within 2 business days.")
g("Who pays return freight if Meridian shipped the wrong item?", "plain", ["returns.md#4"], "Meridian pays return freight when the return is caused by a Meridian error.")
g("How long does a refund take after the returned goods are inspected?", "plain", ["returns.md#5"], "Within 7 business days of inspection.")
g("What is the warranty period for bearings?", "table", ["warranty.md#1"], "6 months from delivery.")
g("What warranty applies to MS-ELC0410?", "exact_sku", ["warranty.md#1"], "24 months - it is an Electrical product (3-phase motor 7.5 kW).")
g("What does the warranty not cover?", "plain", ["warranty.md#2"], "Misuse, incorrect installation, use outside rated limits, lack of maintenance, normal wear and tear, consequential losses.")
g("How quickly must a warranty claim be raised?", "plain", ["warranty.md#3"], "Within 14 days of discovering the defect.")
g("What is the standard transit time to the North-East?", "table", ["shipping.md#2"], "6-8 business days (Remote zone).")
g("What is the freight charge on an order worth 18,000 rupees?", "table", ["shipping.md#3"], "450 rupees (10,000 to 24,999 band).")
g("When is freight free?", "table", ["shipping.md#3"], "For orders of 25,000 rupees and above.")
g("Which carriers does Meridian use?", "plain", ["shipping.md#4"], "BlueDart, Delhivery, DTDC, VRL Logistics and SafeHaul Logistics.")
g("How long are customer order records retained?", "plain", ["data_privacy.md#3"], "7 years for order and invoice records.")
g("An order arrived 8 days late. What compensation does the customer get?", "table", ["shipping_delays.md#3"], "Written apology and a credit of 3% of the order value (6-10 days late band).")
g("What compensation applies if delivery is 25 days late?", "table", ["shipping_delays.md#3"], "Written apology, 8% credit of order value and the right to cancel without charge.")
g("How soon must Meridian tell a customer about a delay?", "plain", ["shipping_delays.md#2"], "Within 1 business day of identifying the delay.")
g("Which carriers may ship hazardous goods?", "plain", ["hazardous_goods.md#2"], "Only SafeHaul Logistics and VRL Logistics; never by air.")
g("What is MS-HAZ0105?", "exact_sku", ["hazardous_goods.md#1"], "Hydrochloric Acid 30% 25 L Carboy, hazard class 8 (corrosive).")
g("Can hazardous goods be returned?", "plain", ["hazardous_goods.md#4"], "Not once dispatched, except wrong item or damage reported within 48 hours; opened containers never.")
g("Do I need approval to order 20 drums of MS-HAZ0101?", "plain", ["hazardous_goods.md#5"], "Yes - more than 15 units of one hazardous SKU needs written Compliance approval.")
g("What discount applies to 340 units on a single order line?", "table", ["bulk_pricing.md#2"], "12% (250 to 499 tier).")
g("What bulk discount do I get for 60 units of one SKU?", "table", ["bulk_pricing.md#2"], "3% (50 to 99 tier).")
g("What is the bulk price for 120 units of MS-VLV0200?", "exact_sku", ["bulk_pricing.md#4"], "6% off 1,875 = 1,762.50 per unit; line value 211,500 rupees.")
g("Can bulk discounts be combined with customer tier benefits?", "plain", ["bulk_pricing.md#3", "customer_tiers.md#2"], "No - bulk discounts cannot be combined with tier benefits or promotions.")
g("What is the credit limit for a Silver customer?", "table", ["credit_terms.md#1"], "1,500,000 rupees, Net 45.")
g("How many days of credit do Gold customers get?", "table", ["credit_terms.md#1"], "Net 60 days.")
g("What interest is charged on late payments?", "plain", ["credit_terms.md#3"], "1.5% per month after the due date.")
g("Who needs to approve a refund of 7,500 rupees?", "table", ["refund_approval.md#2"], "A manager (5,001 to 10,000 band).")
g("Can Desk process a refund of 12,000 rupees?", "table", ["refund_approval.md#2"], "No - above 10,000 rupees only the Finance Controller can approve.")
g("When is a customer flagged for fraud review on refunds?", "plain", ["refund_approval.md#5"], "More than 3 refunds in the previous 90 days; further refunds need manager approval regardless of amount.")
g("Can I cancel an order that has already shipped?", "plain", ["cancellations.md#2"], "No - shipped orders cannot be cancelled; raise a return after delivery.")
g("Can custom-cut items be cancelled?", "plain", ["cancellations.md#3"], "Not once production has started.")
g("How many days do I have to report transit damage?", "plain", ["damaged_goods.md#2", "returns.md#6"], "Within 7 days of delivery (48 hours for hazardous goods).")
g("What are the annual spend thresholds for Silver and Gold tiers?", "table", ["customer_tiers.md#1"], "Silver: 2,500,000 to 10,000,000 rupees; Gold: above 10,000,000 rupees.")
g("Which countries does Meridian export to?", "plain", ["international_shipping.md#1"], "Nepal, Bhutan, Bangladesh and Sri Lanka.")
g("and for 30,000?", "vague_followup", ["shipping.md#3"], "Free freight (25,000 rupees and above). Standalone query: 'freight charge for an order worth 30,000 rupees'.",
  hist=[dict(role="user", content="What is the freight charge on an order worth 18,000 rupees?"), dict(role="assistant", content="450 rupees, per the 10,000 to 24,999 band [shipping.md#3].")])
g("what about for bulk?", "vague_followup", ["returns.md#1"], "Bulk lines (250+ units of one SKU) need prior written approval from the account manager before return. Standalone query: 'returns of bulk orders'.",
  hist=[dict(role="user", content="What is the return window for unopened goods?"), dict(role="assistant", content="30 days from delivery [returns.md#2].")])
assert len(G) == 40
with open(f"{OUT}/evals/golden_retrieval.jsonl", "w") as f:
    for r in G:
        f.write(json.dumps(r) + "\n")
json.dump(dict(note="Chunk IDs depend on your chunker. Section IDs here are '<file>#<top-level section number>'. Map them to your own chunk IDs when scoring recall@5 / MRR.", count=40), open(f"{OUT}/evals/golden_retrieval_README.json", "w"), indent=2)

# ----------------------------------------------------------- refund workflow requests
con = sqlite3.connect(OUT + "/orders.db")
con.row_factory = sqlite3.Row
AS_OF = "2026-10-05"
SKIP = "customer_id!='CUST-9001' and notes not like '%test%' and id not in (select order_id from order_lines where unit_price_charged*quantity>0 and 0)"
CAT = {r["sku"]: r for r in csv.DictReader(open(OUT + "/catalog.csv"))}
mm = {m["order_id"] for m in json.load(open(OUT + "/ground_truth/planted_mismatches.json"))}
vi = {m["order_id"] for m in json.load(open(OUT + "/ground_truth/planted_policy_violations.json"))}
used = set()


def find(delivered_from, delivered_to, vmin, vmax, status="delivered", haz=False):
    rows = con.execute("select o.*, l.sku lsku, l.quantity lq, l.unit lu, l.unit_price_charged lp from orders o join order_lines l on l.order_id=o.id where o.status=? and o.delivered_date between ? and ? and o.customer_id!='CUST-9001' and o.notes not like '%test%'", (status, delivered_from, delivered_to)).fetchall()
    rows = [dict(r) for r in rows if r["id"] not in used | mm | vi and vmin <= r["lq"] * r["lp"] <= vmax and ((r["lsku"].startswith("MS-HAZ")) == haz)]
    random.shuffle(rows)
    r = rows[0]
    used.add(r["id"])
    return r


REQ = []
FRAUD = {}
DEC = []


def req(r, reason, msg, route, amount, why, scope="line", fraction=1.0, flag=None, extra=None):
    rid = f"RF-{len(REQ) + 1:03d}"
    line_val = round(r["lq"] * r["lp"], 2)
    total = con.execute("select total from orders where id=?", (r["id"],)).fetchone()["total"]
    requested = round(line_val * fraction, 2) if scope == "line" else total
    REQ.append(dict(request_id=rid, customer_id=r["customer_id"], order_id=r["id"], sku=r["lsku"] if scope == "line" else None, reason=reason, message=msg.format(oid=r["id"], sku=r["lsku"], name=CAT[r["lsku"]]["name"], qty=r["lq"]), requested_amount=requested,
                    order_total=total, delivered_date=r["delivered_date"], expected_route=route, expected_amount=amount if amount is not None else requested, reasoning=why, fraud_flag=flag, **(extra or {})))
    return rid


# AUTO (<=5000)
for reason, msg, why in [("damaged", "{name} ({sku}) from {oid} arrived damaged. Please refund this line.", "Damaged in transit, reported inside 7 days, value <= 5,000"),
                         ("wrong_item", "We received the wrong item against {sku} on {oid}. Refund please.", "Meridian error, within 60 days, value <= 5,000"),
                         ("changed_mind", "Unopened {name} from {oid} - we no longer need it, returning for a credit note.", "Unopened, within 30 days, value <= 5,000"),
                         ("damaged", "Cracked casing on {sku} from {oid}, photos attached.", "Damaged in transit, value <= 5,000"),
                         ("wrong_item", "{oid} line {sku} is not what we ordered.", "Meridian error, value <= 5,000"),
                         ("changed_mind", "Return of unopened {name} from {oid}.", "Unopened within 30 days, value <= 5,000")]:
    r = find("2026-09-15", "2026-09-29", 300, 4800)
    req(r, reason, msg, "auto_approve", None, why)
# HUMAN (5001-10000)
for reason, msg, why in [("damaged", "{name} ({sku}) from {oid} was damaged on arrival. Please refund this line.", "Value 5,001-10,000 requires manager approval"),
                         ("wrong_item", "Wrong item received for {sku} on {oid}.", "Meridian error but value 5,001-10,000 requires manager approval"),
                         ("changed_mind", "Unopened {name} from {oid}, want a refund.", "Unopened, inside window, value 5,001-10,000 requires manager approval")]:
    r = find("2026-09-15", "2026-09-29", 5200, 9900)
    req(r, reason, msg, "human_approval", None, why)
# opened item, inside new 7-day window: 20% fee, net amount between 5,000 and 10,000 -> human approval
r = find("2026-09-28", "2026-10-01", 6500, 12000)
lv = round(r["lq"] * r["lp"], 2)
req(r, "opened_item", "We opened {name} ({sku}) from {oid} two days ago but it does not fit. Want to return it.", "human_approval", round(lv * 0.8, 2), "Opened item inside 7 days: 20% restocking fee applies (updated terms); net amount needs manager approval", extra=dict(restocking_fee_percent=20))
# approver edit scenario
r = find("2026-09-15", "2026-09-29", 6000, 9000)
rid_edit = req(r, "damaged", "Part of the consignment {sku} on {oid} was damaged, refund full line please.", "human_approval", None, "Value 5,001-10,000 requires manager approval; approver will edit amount to a partial refund")
# fraud-flag scenario (<=5000 but flagged)
r = find("2026-09-15", "2026-09-29", 300, 4500)
rid_fraud = req(r, "changed_mind", "Unopened {name} from {oid}, please refund.", "human_approval", None, "Customer has >3 refunds in 90 days: manager approval required regardless of amount", flag="repeat_refunds")
FRAUD[r["customer_id"]] = dict(customer_id=r["customer_id"], refunds_last_90d=5, account_age_days=420, ship_to_address_changed_recently=True, chargebacks_last_year=1, risk="high")
# REJECTS
r = find("2026-07-10", "2026-08-15", 300, 4800)
req(r, "changed_mind", "We would like to return unopened {name} from {oid}.", "reject", 0, "Delivered more than 30 days ago (policy: 30 days for unopened)")
r = find("2026-09-15", "2026-09-29", 11000, 60000)
req(r, "damaged", "{name} from {oid} damaged. Refund the full line.", "reject", 0, "Above 10,000 rupees - Desk must not process; escalate to Finance Controller", extra=dict(escalate_to="Finance Controller"))
r = find("2026-09-12", "2026-09-26", 300, 6000)
req(r, "changed_mind", "Please refund the whole order {oid} plus freight; we want 50% extra for the trouble.", "reject", 0, "Requested amount exceeds order value / not a policy remedy", scope="order", extra=dict(requested_amount_override=None))
REQ[-1]["requested_amount"] = round(REQ[-1]["order_total"] * 1.5, 2)
r = find("2026-09-12", "2026-09-26", 300, 40000, haz=True)
req(r, "changed_mind", "We opened one container of {sku} on {oid}, please refund the line.", "reject", 0, "Opened hazardous goods are never returnable (hazardous_goods.md#4)")
r = find("2026-09-20", "2026-09-24", 300, 4800)
req(r, "opened_item", "Opened {name} ({sku}) from {oid}; 10 days since delivery, want to return.", "reject", 0, "Opened item outside the 7-day window (updated terms effective 2026-03-01 supersede the 14-day window)")
sr = con.execute("select * from orders where status='shipped' and id != 'MS-10422' limit 1").fetchone()
if sr is None:
    sr = con.execute("select * from orders where status='shipped' limit 1").fetchone()
rid = f"RF-{len(REQ) + 1:03d}"
REQ.append(dict(request_id=rid, customer_id=sr["customer_id"], order_id=sr["id"], sku=None, reason="not_received", message=f"Please refund {sr['id']}, it has not arrived.", requested_amount=sr["total"], order_total=sr["total"], delivered_date=None,
                expected_route="reject", expected_amount=0, reasoning="Order not delivered (status shipped): no refund; advise tracking / delay policy", fraud_flag=None))
rf = con.execute("select * from orders where status='refunded' and refund_amount is not null and id not in ('MS-10403','MS-10413') limit 1").fetchone()
REQ.append(dict(request_id=f"RF-{len(REQ) + 1:03d}", customer_id=rf["customer_id"], order_id=rf["id"], sku=None, reason="damaged", message=f"Refund for {rf['id']} again please, the first one was not enough.", requested_amount=rf["total"], order_total=rf["total"], delivered_date=rf["delivered_date"],
                expected_route="reject", expected_amount=0, reasoning="Order already refunded - duplicate request", fraud_flag=None))
r = find("2026-09-15", "2026-09-29", 300, 4800)
req(r, "late_delivery", "{oid} was late. The shipping policy says we get compensation - please credit us.", "auto_approve", None, "Late-delivery credit handled via shipping_delays.md; placeholder amount = requested", extra=dict(note="Edge case: compensation, not a return. Expected credit depends on days late; amount here is the customer's ask."))
assert len(REQ) == 20, len(REQ)

for q in REQ:
    q.pop("requested_amount_override", None)
json.dump(REQ, open(f"{OUT}/workflows/refund_requests.json", "w"), indent=2)
for c in {q["customer_id"] for q in REQ} - set(FRAUD):
    FRAUD[c] = dict(customer_id=c, refunds_last_90d=random.choice([0, 0, 1, 1, 2]), account_age_days=random.randint(300, 2500), ship_to_address_changed_recently=False, chargebacks_last_year=0, risk="low")
json.dump(list(FRAUD.values()), open(f"{OUT}/workflows/fraud_signals.json", "w"), indent=2)
edit_req = next(q for q in REQ if q["request_id"] == rid_edit)
decisions = []
for q in REQ:
    if q["expected_route"] == "human_approval":
        if q["request_id"] == rid_edit:
            decisions.append(dict(request_id=q["request_id"], decision="edit", edited_amount=round(q["requested_amount"] * 0.6, 2), approver="mgr.sharma", responds_after_hours=20, comment="Only part of the consignment was damaged"))
        elif q["request_id"] == rid_fraud:
            decisions.append(dict(request_id=q["request_id"], decision="reject", approver="mgr.iyer", responds_after_hours=6, comment="Fraud review: repeated refunds"))
        else:
            decisions.append(dict(request_id=q["request_id"], decision="approve", approver=random.choice(["mgr.sharma", "mgr.iyer"]), responds_after_hours=random.choice([2, 5, 20, 26]), comment=""))
json.dump(decisions, open(f"{OUT}/workflows/approver_decisions.json", "w"), indent=2)
json.dump(dict(refund_executor="MOCK - write to orders.db.refund_ledger with idempotency_key = request_id + ':' + order_id; second write with same key must be a no-op",
               email_critique_cases=[dict(case="draft too casual, promises more than policy allows", expected_critique="Remove promise of full refund; cite refund_approval.md#2"),
                                     dict(case="draft omits RMA instructions", expected_critique="Add RMA process from returns.md#4")]),
          open(f"{OUT}/workflows/mock_payment_and_critique_cases.json", "w"), indent=2)
print("golden", len(G), "refund requests", len(REQ), "decisions", len(decisions))
from collections import Counter
print(Counter(q["expected_route"] for q in REQ))
