"""30 customer emails + hand labels + description->SKU map. Data-driven against orders.db so IDs/lines are real."""
import sqlite3, json, os, random, datetime as dt, csv

import os as _os; OUT = _os.environ.get("MERIDIAN_OUT", _os.path.join(_os.path.dirname(_os.path.abspath(__file__)), "..", "meridian_desk_data"))
os.makedirs(OUT + "/emails", exist_ok=True)
random.seed(77)
con = sqlite3.connect(OUT + "/orders.db")
con.row_factory = sqlite3.Row
CAT = {r["sku"]: r for r in csv.DictReader(open(OUT + "/catalog.csv"))}
CUST = {r["id"]: dict(r) for r in con.execute("select * from customers")}
MM = json.load(open(OUT + "/ground_truth/planted_mismatches.json"))
SPECIAL_IDS = {r["order_id"] for r in map(lambda x: x, [])}
used = set()
D = dt.date


def lines(oid):
    return [dict(r) for r in con.execute("select sku,quantity,unit,unit_price_charged from order_lines where order_id=? order by id", (oid,))]


def pick(where, params=(), cust=None):
    q = "select * from orders where " + where + (" and customer_id=?" if cust else "") + " and id not in (%s) order by id" % ",".join("'%s'" % u for u in used) if used else "select * from orders where " + where + (" and customer_id=?" if cust else "") + " order by id"
    rows = con.execute(q, tuple(params) + ((cust,) if cust else ())).fetchall()
    assert rows, where
    r = dict(rows[random.randrange(len(rows))])
    used.add(r["id"])
    return r


NORMAL = "customer_id != 'CUST-9001' and id not in ('MS-10422') and status != 'delayed' and notes not like '%test%'"
EMAILS = []


def cname(cid):
    return CUST[cid]["contact_name"]


def sig(cid):
    c = CUST[cid]
    return f"Regards,\n{c['contact_name']}\n{c['name']}, {c['city']}\n+91 00000 {random.randint(10000, 99999)}"


def add(n, cid, date, subject, body, label, sender=None, resolved=None):
    EMAILS.append(dict(n=n, cid=cid, date=date, subject=subject, body=body.strip() + "\n\n" + (sig(cid) if cid else sender["sig"]), label=label, sender=sender, resolved=resolved or []))


def L(sku=None, desc=None, qty=None, unit=None):
    return dict(sku_mentioned=sku, description=desc, quantity=qty, unit=unit)


# ---------------------------------------------------------------- ORDERS (8)
add(1, "CUST-0003", D(2026, 10, 1), "Order request - valves and elbows",
    "Hi team,\n\nPlease supply the following and confirm the delivery date:\n\n1. MS-VLV0100 - 40 nos\n2. MS-FIT0110 - 25 nos\n\nWe need this at our Bengaluru plant by 12 Oct. Please share the proforma invoice.",
    dict(intent="order", secondary_intents=[], order_ids_referenced=[], line_items=[dict(sku="MS-VLV0100", description=None, quantity=40, unit="pcs"), dict(sku="MS-FIT0110", description=None, quantity=25, unit="pcs")], requested_date="2026-10-12", urgency="normal"))
add(2, "CUST-0002", D(2026, 10, 1), "valves - need asap",
    "Hello,\n\nwe need 12 nos of the 2-inch brass valve, same as last time, and also 8 brass gate valve 2 inch. Pls deliver before end of October. Also send latest price list if possible.",
    dict(intent="order", secondary_intents=["other"], order_ids_referenced=[], line_items=[dict(sku_mentioned=None, description="2-inch brass valve", quantity=12, unit="pcs", resolved_sku="MS-VLV0200", ambiguous=True, candidates=["MS-VLV0200", "MS-VLV0201"]),
                                                                                           dict(sku_mentioned=None, description="brass gate valve 2 inch", quantity=8, unit="pcs", resolved_sku="MS-VLV0201", ambiguous=False)], requested_date="2026-10-31", urgency="normal"),
    resolved=[dict(email_id="email_02", description="2-inch brass valve", resolved_sku="MS-VLV0200", ambiguous=True, candidates=["MS-VLV0200 (ball valve)", "MS-VLV0201 (gate valve)"], note="Customer says 'same as last time' - cannot be resolved from the email alone"),
              dict(email_id="email_02", description="brass gate valve 2 inch", resolved_sku="MS-VLV0201", ambiguous=False)])
add(3, "CUST-0008", D(2026, 10, 2), "URGENT shutdown order",
    "Team,\n\nplant shutdown starts 9 Oct so we need these latest by 8 Oct:\n\nMS-FAS0105 x 20 boxes\nMS-FAS0110 x 20 boxes\n\nPlease confirm stock today. Our PO number is PO-4471.",
    dict(intent="order", secondary_intents=[], order_ids_referenced=[], line_items=[dict(sku="MS-FAS0105", description=None, quantity=20, unit="box"), dict(sku="MS-FAS0110", description=None, quantity=20, unit="box")], requested_date="2026-10-08", urgency="high"))
add(4, "CUST-0011", D(2026, 10, 2), "bearings bulk order",
    "Hi,\n\nWe want to place an order for 300 pcs of 6205 2RS sealed bearings. Since this is a big quantity, does the bulk discount apply and what would the unit price be? If yes, go ahead and book it, deliver to Chakan by 20th.",
    dict(intent="order", secondary_intents=["other"], order_ids_referenced=[], line_items=[dict(sku_mentioned=None, description="6205 2RS sealed bearings", quantity=300, unit="pcs", resolved_sku="MS-BRG0101", ambiguous=False)], requested_date="2026-10-20", urgency="normal"),
    resolved=[dict(email_id="email_04", description="6205 2RS sealed bearings", resolved_sku="MS-BRG0101", ambiguous=False)])
add(5, "CUST-0015", D(2026, 10, 2), "small order",
    "Dear Sir,\n\nKindly send 1 torque wrench (the 40 to 200 Nm one), 5 pairs of nitrile coated gloves size large and 2 yellow safety helmets. Need before next Friday.",
    dict(intent="order", secondary_intents=[], order_ids_referenced=[], line_items=[dict(sku_mentioned=None, description="torque wrench 40 to 200 Nm", quantity=1, unit="pcs", resolved_sku="MS-TLS0110", ambiguous=False),
                                                                                    dict(sku_mentioned=None, description="nitrile coated gloves size large", quantity=5, unit="pair", resolved_sku="MS-PPE0101", ambiguous=False),
                                                                                    dict(sku_mentioned=None, description="yellow safety helmets", quantity=2, unit="pcs", resolved_sku="MS-PPE0120", ambiguous=False)], requested_date="2026-10-09", urgency="normal"),
    resolved=[dict(email_id="email_05", description="torque wrench 40 to 200 Nm", resolved_sku="MS-TLS0110", ambiguous=False), dict(email_id="email_05", description="nitrile coated gloves size large", resolved_sku="MS-PPE0101", ambiguous=False),
              dict(email_id="email_05", description="yellow safety helmets", resolved_sku="MS-PPE0120", ambiguous=False)])
add(6, "CUST-0005", D(2026, 10, 3), "Acetone and HCl order",
    "Hello,\n\nPlease book 10 drums of MS-HAZ0101 (acetone 20 L) and 4 carboys of the hydrochloric acid 30%. We need delivery before 14 Oct. Can we send our own transporter to pick up instead of your courier? Our truck is available on Monday.",
    dict(intent="order", secondary_intents=["other"], order_ids_referenced=[], line_items=[dict(sku="MS-HAZ0101", description="acetone 20 L", quantity=10, unit="drum"), dict(sku_mentioned=None, description="hydrochloric acid 30%", quantity=4, unit="carboy", resolved_sku="MS-HAZ0105", ambiguous=False)], requested_date="2026-10-14", urgency="normal"),
    resolved=[dict(email_id="email_06", description="hydrochloric acid 30%", resolved_sku="MS-HAZ0105", ambiguous=False)])
o7 = pick(NORMAL + " and status='delivered'", cust="CUST-0017")
add(7, "CUST-0017", D(2026, 10, 3), f"Repeat of {o7['id']}",
    f"Hi,\n\nPlease repeat order {o7['id']} exactly as before, same quantities. Same delivery address. We'd like it by 15th October if possible.",
    dict(intent="order", secondary_intents=[], order_ids_referenced=[o7["id"]], line_items=[], reorder_of=o7["id"], reorder_lines=[dict(sku=l["sku"], quantity=l["quantity"], unit=l["unit"]) for l in lines(o7["id"])], requested_date="2026-10-15", urgency="normal"))
add(8, "CUST-0022", D(2026, 10, 4), "Quote + order motors",
    "Dear team,\n\nWe need 2 nos of 3-phase motor 7.5 kW and 4 contactors 32A for a pump skid. Please send the quote first with delivery time. If delivery is within 10 days, treat this mail as our order.",
    dict(intent="order", secondary_intents=["other"], order_ids_referenced=[], line_items=[dict(sku_mentioned=None, description="3-phase motor 7.5 kW", quantity=2, unit="pcs", resolved_sku="MS-ELC0410", ambiguous=False), dict(sku_mentioned=None, description="contactors 32A", quantity=4, unit="pcs", resolved_sku="MS-ELC0420", ambiguous=False)], requested_date="2026-10-14", urgency="normal"),
    resolved=[dict(email_id="email_08", description="3-phase motor 7.5 kW", resolved_sku="MS-ELC0410", ambiguous=False), dict(email_id="email_08", description="contactors 32A", resolved_sku="MS-ELC0420", ambiguous=False)])

# ---------------------------------------------------------------- STATUS (7)
add(9, "CUST-0001", D(2026, 10, 5), "WHERE ARE MS-10388 and MS-10401??",
    "Hello,\n\nBoth MS-10388 and MS-10401 were promised for last week and nothing has arrived. Our maintenance crew is waiting. Give me exact status and a firm date today.",
    dict(intent="status", secondary_intents=[], order_ids_referenced=["MS-10388", "MS-10401"], line_items=[], requested_date=None, urgency="high"))
o10 = pick(NORMAL + " and status='delivered' and delivered_date >= '2026-09-20'")
add(10, o10["customer_id"], D(2026, 10, 1), f"tracking for {o10['id']}",
    f"Hi,\n\nThe tracking link for {o10['id']} ({o10['carrier']}) shows no update since it was dispatched. Could you check whether it was delivered? Our store says nothing has come.",
    dict(intent="status", secondary_intents=[], order_ids_referenced=[o10["id"]], line_items=[], requested_date=None, urgency="normal"))
add(11, "CUST-0003", D(2026, 10, 5), "Order MS-10422",
    "Hi, where is order MS-10422 right now? Planning to unload on Wednesday, need to know if the truck will make it.",
    dict(intent="status", secondary_intents=[], order_ids_referenced=["MS-10422"], line_items=[], requested_date="2026-10-07", urgency="normal"))
add(12, "CUST-0007", D(2026, 10, 2), "my order",
    "Hello, I placed an order last week for some valves and fittings for our Nashik unit but I do not have the order number with me. Can you tell me when it will reach? Thanks.",
    dict(intent="status", secondary_intents=[], order_ids_referenced=[], line_items=[], requested_date=None, urgency="low"))
o13a = pick(NORMAL + " and status='delivered' and delivered_date >= '2026-09-25'", cust="CUST-0012") if con.execute("select 1 from orders where customer_id='CUST-0012' and status='delivered' and delivered_date>='2026-09-25'").fetchone() else pick(NORMAL + " and status='delivered'", cust="CUST-0012")
o13b = pick(NORMAL + " and status='delivered'", cust="CUST-0012")
add(13, "CUST-0012", D(2026, 10, 3), f"{o13a['id']} / {o13b['id']} status + invoice",
    f"Dear team,\n\nPlease confirm if {o13a['id']} and {o13b['id']} have both been delivered. Also please email us a copy of the invoice for {o13b['id']} - accounts department needs it for payment.",
    dict(intent="status", secondary_intents=["other"], order_ids_referenced=[o13a["id"], o13b["id"]], line_items=[], requested_date=None, urgency="normal"))
add(14, "CUST-0013", D(2026, 10, 5), "need by Thursday",
    "Hi,\n\nStatus of MS-10372 please. We need it by Thursday for the Kanpur line, otherwise the machine stays down.",
    dict(intent="status", secondary_intents=[], order_ids_referenced=["MS-10372"], line_items=[], requested_date="2026-10-08", urgency="high"))
o15 = pick(NORMAL, cust="CUST-0014")
add(15, "CUST-0014", D(2026, 10, 4), f"{o15['id']} - dispatch?",
    f"Hello,\n\nCan you tell me if {o15['id']} has been dispatched? If it has not left your warehouse by Friday please cancel it, we'll buy locally. Also what is your credit period for us?",
    dict(intent="status", secondary_intents=["other"], order_ids_referenced=[o15["id"]], line_items=[], requested_date="2026-10-09", urgency="normal"))

# ---------------------------------------------------------------- COMPLAINTS (6)
add(16, "CUST-0001", D(2026, 10, 5), "Unacceptable delay - MS-10433",
    "To whoever manages this account,\n\nMS-10433 is now almost two weeks late. This is the third time this year. We expect compensation as per your shipping policy and a firm dispatch date. Separately, the invoice for MS-10401 shows the wrong GST number, please correct it.",
    dict(intent="complaint", secondary_intents=["other"], order_ids_referenced=["MS-10433", "MS-10401"], line_items=[], requested_date=None, urgency="high"))
o17 = pick(NORMAL + " and status='delivered' and delivered_date >= '2026-09-15'", cust="CUST-0004") if con.execute("select 1 from orders where customer_id='CUST-0004' and status='delivered' and delivered_date>='2026-09-15'").fetchone() else pick(NORMAL + " and status='delivered' and delivered_date >= '2026-09-10'")
l17 = lines(o17["id"])[0]
add(17, o17["customer_id"], D(2026, 10, 2), f"Wrong material received - {o17['id']}",
    f"Hi,\n\nFor {o17['id']} we ordered {CAT[l17['sku']]['name']} ({l17['sku']}) x {l17['quantity']} but what arrived is a different item. This is the wrong material for our application. Please arrange the correct item and take back the wrong one.",
    dict(intent="complaint", secondary_intents=["return"], order_ids_referenced=[o17["id"]], line_items=[dict(sku=l17["sku"], description=None, quantity=l17["quantity"], unit=l17["unit"])], requested_date=None, urgency="normal"))
o18 = pick(NORMAL + " and status='delivered' and delivered_date >= '2026-09-24'")
add(18, o18["customer_id"], D(2026, 10, 3), f"Damaged packaging {o18['id']}",
    f"Dear team,\n\nWe received {o18['id']} yesterday. Two cartons were crushed and the carrier's delivery receipt has a remark. Photos attached. Please let us know the next steps.",
    dict(intent="complaint", secondary_intents=[], order_ids_referenced=[o18["id"]], line_items=[], requested_date=None, urgency="normal"))
m19 = MM[0]
o19 = con.execute("select customer_id from orders where id=?", (m19["order_id"],)).fetchone()["customer_id"]
add(19, o19, D(2026, 10, 1), f"Price mismatch on {m19['order_id']}",
    f"Hello,\n\nThe invoice for {m19['order_id']} charges {m19['sku']} at Rs {m19['charged_price']:.2f} per unit. The price on your catalogue is Rs {m19['catalog_price']:.0f}. Please check and issue a credit note for the difference.",
    dict(intent="complaint", secondary_intents=["other"], order_ids_referenced=[m19["order_id"]], line_items=[dict(sku=m19["sku"], description=None, quantity=m19["quantity"], unit=None)], requested_date=None, urgency="normal", note="Planted overcharge - see ground_truth/planted_mismatches.json"))
add(20, "CUST-0016", D(2026, 10, 4), "No reply from support",
    "Hi,\n\nI have written to your support address three times in the last 10 days about our open quotation and nobody has replied. Please have someone call me. This is poor service for a customer of 4 years.",
    dict(intent="complaint", secondary_intents=[], order_ids_referenced=[], line_items=[], requested_date=None, urgency="normal"))
add(21, "CUST-0019", D(2026, 10, 2), "Gaskets leaking",
    "Dear Sir,\n\nThe spiral wound gaskets DN80 that you supplied in September are leaking at the flange after only a few weeks of operation. We think there is a manufacturing defect. Do you cover this under warranty? If so we will send them back.",
    dict(intent="complaint", secondary_intents=["return"], order_ids_referenced=[], line_items=[dict(sku_mentioned=None, description="spiral wound gaskets DN80", quantity=None, unit=None, resolved_sku="MS-GSK0101", ambiguous=False)], requested_date=None, urgency="normal"),
    resolved=[dict(email_id="email_21", description="spiral wound gaskets DN80", resolved_sku="MS-GSK0101", ambiguous=False)])

# ---------------------------------------------------------------- RETURNS (6)
def ret(n, o, subject, body_fmt, extra_sec=None, date=None, cid=None):
    l = lines(o["id"])[0]
    qty = max(1, l["quantity"] // 2) if l["quantity"] > 4 else l["quantity"]
    body = body_fmt.format(oid=o["id"], name=CAT[l["sku"]]["name"], sku=l["sku"], qty=qty, unit=l["unit"], dd=o["delivered_date"])
    add(n, o["customer_id"], date or D(2026, 10, 3), subject.format(oid=o["id"]), body,
        dict(intent="return", secondary_intents=extra_sec or [], order_ids_referenced=[o["id"]], line_items=[dict(sku=l["sku"], description=None, quantity=qty, unit=l["unit"])], requested_date=None, urgency="normal"))


o22 = pick(NORMAL + " and status='delivered' and delivered_date between '2026-09-12' and '2026-09-24'")
ret(22, o22, "Return request {oid}", "Hello,\n\nWe would like to return {qty} {unit} of {name} ({sku}) from {oid}. The goods are unopened and in original packaging; ordering error on our side. Please share the RMA.")
o23 = pick(NORMAL + " and status='delivered' and delivered_date between '2026-09-20' and '2026-09-25'")
d23 = D.fromisoformat(o23["delivered_date"]) + dt.timedelta(days=10)
ret(23, o23, "Return of opened item - {oid}", "Hi,\n\nWe opened the pack of {name} ({sku}) from {oid} that was delivered on {dd} and found we ordered the wrong size. It is 10 days since delivery. We would like to return {qty} {unit}. Is this still possible and is there any charge?", extra_sec=["other"], date=d23)
o24 = pick(NORMAL + " and status='delivered' and delivered_date between '2026-09-15' and '2026-09-28'")
ret(24, o24, "Return + credit note ({oid})", "Dear team,\n\nPlease arrange return of {qty} {unit} of {name} from {oid}, unopened. We would prefer a credit note against our next invoice rather than a refund to the bank. Kindly confirm the process.", extra_sec=["other"])
o25 = pick(NORMAL + " and status='delivered' and delivered_date between '2026-09-26' and '2026-10-01'")
ret(25, o25, "Damaged on arrival - {oid}", "Hi,\n\n{name} ({sku}) from {oid} arrived damaged, the casing is cracked. Photos attached. Want to send it back and get a replacement.", extra_sec=["complaint"], date=D(2026, 10, 4))
oq = con.execute("select o.* from orders o join order_lines l on l.order_id=o.id where o.status='delivered' and l.quantity>=100 and o.delivered_date < '2026-09-01' and o.customer_id!='CUST-9001' and o.id not in (select id from orders where notes like '%test%') order by o.id").fetchall()
o26 = dict(oq[len(oq) // 2])
used.add(o26["id"])
l26 = [l for l in lines(o26["id"]) if l["quantity"] >= 100][0]
add(26, o26["customer_id"], D(2026, 10, 2), f"Excess stock return {o26['id']}",
    f"Hello,\n\nWe over-ordered on {o26['id']}. We would like to return {l26['quantity'] // 2} {l26['unit']} of {l26['sku']} - unopened and still on the pallet. It was delivered around {o26['delivered_date']}. Please issue RMA.",
    dict(intent="return", secondary_intents=[], order_ids_referenced=[o26["id"]], line_items=[dict(sku=l26["sku"], description=None, quantity=l26["quantity"] // 2, unit=l26["unit"])], requested_date=None, urgency="low"))
hz = con.execute("select o.*, l.sku hsku, l.quantity hq, l.unit hu from orders o join order_lines l on l.order_id=o.id where l.sku like 'MS-HAZ%' and o.status='delivered' and o.customer_id!='CUST-9001' and o.id not in (select order_id from (select 'x' order_id where 0)) order by o.delivered_date desc").fetchall()
h = dict(random.choice([r for r in hz if r["id"] not in used][:15]))
used.add(h["id"])
add(27, h["customer_id"], D(2026, 10, 5), f"Return - {h['id']} chemicals",
    f"Hi,\n\nFrom {h['id']} we received {h['hq']} {h['hu']} of {h['hsku']}. We opened one container and need to return the whole lot, we ordered more than needed. Please send your pickup.",
    dict(intent="return", secondary_intents=[], order_ids_referenced=[h["id"]], line_items=[dict(sku=h["hsku"], description=None, quantity=h["hq"], unit=h["hu"])], requested_date=None, urgency="normal"))

# ---------------------------------------------------------------- OTHER (3)
EMAILS_NEW = dict(name="Rohan Mehta", company="Triveni Enterprises", email="rohan@triveni-ent.example", sig="Thanks,\nRohan Mehta\nTriveni Enterprises, Indore\n+91 00000 " + str(random.randint(10000, 99999)))
add(28, None, D(2026, 10, 1), "New customer enquiry - credit and catalogue",
    "Hello,\n\nWe run a fabrication unit in Indore and are looking for a regular supplier of valves, fittings and PPE. Could you send your latest catalogue and explain what credit terms you offer to new customers?",
    dict(intent="other", secondary_intents=[], order_ids_referenced=[], line_items=[], requested_date=None, urgency="low"), sender=EMAILS_NEW)
add(29, "CUST-0008", D(2026, 10, 3), "Credit limit review",
    "Hi,\n\nOur monthly purchases have grown and we would like to increase our credit limit from 50 lakh to 80 lakh rupees. Who should we speak to and what documents do you need?",
    dict(intent="other", secondary_intents=[], order_ids_referenced=[], line_items=[], requested_date=None, urgency="low"))
add(30, "CUST-0020", D(2026, 10, 4), "MSDS and invoice copies",
    "Dear team,\n\nPlease send the safety data sheet for MS-HAZ0101 (acetone) - our auditors are asking for it - and duplicate GST invoice copies for our September purchases.",
    dict(intent="other", secondary_intents=[], order_ids_referenced=[], line_items=[dict(sku="MS-HAZ0101", description=None, quantity=None, unit=None)], requested_date=None, urgency="low"))

# ---------------------------------------------------------------- write
EMAILS.sort(key=lambda e: e["n"])
labels = []
for e in EMAILS:
    eid = f"email_{e['n']:02d}"
    if e["cid"]:
        c = CUST[e["cid"]]
        frm, cname_, cid = f"{c['contact_name']} <{c['email']}>", c["name"], c["id"]
    else:
        frm, cname_, cid = f"{e['sender']['name']} <{e['sender']['email']}>", e["sender"]["company"], None
    hour, minute = random.randint(8, 18), random.randint(0, 59)
    dtm = dt.datetime.combine(e["date"], dt.time(hour, minute))
    hdr = f"From: {frm}\nTo: orders@meridiansupply.example\nDate: {dtm.strftime('%a, %d %b %Y %H:%M:%S')} +0530\nSubject: {e['subject']}\nMessage-ID: <{eid}.{random.randint(10**6, 10**7)}@mail.example>\n\n"
    open(f"{OUT}/emails/{eid}.txt", "w", encoding="utf-8").write(hdr + e["body"] + "\n")
    labels.append(dict(email_id=eid, customer_id=cid, customer_name=cname_, **e["label"]))
HAND15 = [1, 2, 4, 5, 6, 9, 12, 13, 15, 16, 17, 19, 23, 27, 28]
json.dump(dict(convention="intent = primary action the sender needs from Meridian; any other intents go in secondary_intents. Conditional/side requests (quote, invoice copy, cancel-if-late) count as 'other' unless they are a core return/complaint. Description-only products are resolved in ground_truth/description_to_sku_map.json.",
               labels=[l for l in labels if int(l["email_id"][-2:]) in HAND15]), open(OUT + "/ground_truth/email_labels.json", "w"), indent=2)
json.dump(dict(labels=labels), open(OUT + "/ground_truth/email_labels_all30.json", "w"), indent=2)
json.dump([r for e in EMAILS for r in e["resolved"]], open(OUT + "/ground_truth/description_to_sku_map.json", "w"), indent=2)
print(len(EMAILS), "emails; hand-labelled:", len(HAND15), "; orders referenced:", sorted(used))
