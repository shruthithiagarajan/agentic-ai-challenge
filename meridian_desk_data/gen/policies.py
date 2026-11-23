import os
import os as _os; OUT = _os.path.join(_os.environ.get("MERIDIAN_OUT", _os.path.join(_os.path.dirname(_os.path.abspath(__file__)), "..", "meridian_desk_data")), "policies")
os.makedirs(OUT, exist_ok=True)

P = {}

P["returns.md"] = """---
doc_id: POL-RET-001
title: Returns Policy
effective_date: 2025-01-01
version: 3.1
owner: Customer Operations
---
# Returns Policy

## 1. Scope
1.1 This policy applies to all catalogue products sold by Meridian Supply Co. except hazardous goods (see the Hazardous Goods Handling Policy) and custom-cut or made-to-order items.
1.2 Returns are accepted only for goods purchased directly from Meridian Supply Co.
1.3 Returns of bulk lines (250 units or more of a single SKU in one order) require prior written approval from the customer's account manager.

## 2. Return window for unopened goods
2.1 Unopened, unused goods in their original sealed packaging may be returned within 30 days of the delivery date for a full refund or credit note.
2.2 Return requests received after 30 days are declined, except for warranty claims handled under the Warranty Policy.

## 3. Opened items
3.1 Items whose packaging has been opened may be returned within 14 days of the delivery date.
3.2 Opened items attract a restocking fee of 10% of the line value.
3.3 Opened items must be unused, undamaged and complete with all accessories, labels and documentation.

## 4. Return process
4.1 Raise a return request by email to returns@meridiansupply.example quoting the order ID, SKU, quantity and reason.
4.2 Meridian issues a Return Merchandise Authorisation (RMA) number within 2 business days. Goods sent back without an RMA number are not accepted.
4.3 Return freight is paid by Meridian when the return is caused by a Meridian error (wrong item shipped or damaged in transit). In all other cases the customer pays return freight.

## 5. Refunds
5.1 Approved refunds are paid to the original payment method, or issued as a credit note, within 7 business days of Meridian's inspection of the returned goods.
5.2 Refund approvals follow the thresholds in the Refund Approval Policy.

## 6. Return eligibility summary

| Condition | Return window | Restocking fee |
|---|---|---|
| Unopened, unused | 30 days from delivery | None |
| Opened | 14 days from delivery | 10% of line value |
| Wrong item shipped (Meridian error) | 60 days from delivery | None |
| Damaged in transit | Report within 7 days of delivery | None |
"""

P["returns_opened_items.md"] = """---
doc_id: POL-RET-002
title: Returns of Opened Items - Updated Terms
effective_date: 2026-03-01
version: 1.0
owner: Customer Operations
---
# Returns of Opened Items - Updated Terms

## 1. Purpose
1.1 This document sets out the terms on which Meridian Supply Co. accepts returns of catalogue items whose packaging has been opened.

## 2. Opened-item return window and fee
2.1 Opened items may be returned within 7 days of the delivery date.
2.2 A restocking fee of 20% of the line value applies to every opened-item return.
2.3 Opened-item returns with a line value above 50,000 rupees require manager approval before an RMA number is issued.
2.4 Opened items must be unused, undamaged and returned with all original accessories.

## 3. Unopened items
3.1 Unopened items continue to be returned within 30 days of delivery without a restocking fee.
"""

P["warranty.md"] = """---
doc_id: POL-WAR-001
title: Warranty Policy
effective_date: 2025-01-01
version: 2.0
owner: Quality Assurance
---
# Warranty Policy

## 1. Warranty periods
1.1 Meridian warrants that products are free from manufacturing defects for the period shown below, counted from the delivery date.
1.2 The standard period for each product category is:

| Category | Warranty period |
|---|---|
| Valves | 12 months |
| Fittings | 12 months |
| Bearings | 6 months |
| Electrical | 24 months |
| Tools | 12 months |
| Gaskets & Seals | 3 months |
| PPE, Fasteners, Lubricants | No warranty (shelf-life or consumable items) |

1.3 Motors such as MS-ELC0410 (3-Phase Induction Motor 7.5 kW) are Electrical products and carry the 24-month warranty.

## 2. Exclusions
2.1 The warranty does not cover damage caused by misuse, incorrect installation, use outside rated pressure or temperature, or lack of maintenance.
2.2 Normal wear and tear, including worn seals and bearings at end of service life, is excluded.
2.3 Meridian is not liable for consequential losses such as production downtime.

## 3. Making a claim
3.1 A warranty claim must be raised within 14 days of discovering the defect, by email with the order ID, SKU, batch number and photographs.
3.2 Meridian may request that the product is returned for inspection. Freight for warranty inspections is paid by Meridian.

## 4. Remedy
4.1 At its option Meridian will repair, replace or credit the defective product. No cash refund is payable for warranty claims.
"""

P["shipping.md"] = """---
doc_id: POL-SHP-001
title: Shipping and Delivery Policy
effective_date: 2025-06-01
version: 4.2
owner: Logistics
---
# Shipping and Delivery Policy

## 1. Dispatch
1.1 Orders confirmed before 2:00 PM IST are dispatched the same business day, subject to stock availability.
1.2 The promised delivery date is shown on the order confirmation.

## 2. Standard transit times

| Zone | Examples | Standard transit |
|---|---|---|
| Metro | Mumbai, Delhi, Chennai, Bengaluru, Kolkata, Hyderabad | 2 business days |
| Tier-2 | Pune, Nashik, Coimbatore, Rajkot, Raipur | 3-4 business days |
| Remote | North-East states, islands, hill stations | 6-8 business days |

## 3. Freight charges

| Order value | Freight |
|---|---|
| Below 10,000 rupees | 850 rupees |
| 10,000 to 24,999 rupees | 450 rupees |
| 25,000 rupees and above | Free |

## 4. Carriers
4.1 Meridian ships with BlueDart, Delhivery, DTDC, VRL Logistics and SafeHaul Logistics.
4.2 A customer may request a preferred courier. Meridian honours the request where the carrier serves the delivery pincode and the goods are eligible (see the Hazardous Goods Handling Policy for restricted goods).

## 5. Tracking
5.1 A tracking number is emailed to the customer on dispatch.

## 6. Delivery attempts
6.1 Carriers make up to 3 delivery attempts. After the third failed attempt the consignment returns to Meridian and re-delivery is charged.
"""

P["shipping_delays.md"] = """---
doc_id: POL-SHP-002
title: Shipping Delays and Compensation
effective_date: 2025-09-01
version: 1.3
owner: Logistics
---
# Shipping Delays and Compensation

## 1. Definition
1.1 An order is delayed when it has not been delivered by the promised delivery date shown on the order confirmation.
1.2 Days late are counted in calendar days from the promised date to the date of delivery (or to today, if not yet delivered).

## 2. Customer notification
2.1 Meridian notifies the customer of a known delay within 1 business day of identifying it.
2.2 The notice states the new expected date and the reason for the delay.

## 3. Compensation for late delivery
3.1 Compensation depends on how many days late the order is:

| Days late | Remedy |
|---|---|
| 1 to 5 days | Apology only, no credit |
| 6 to 10 days | Written apology and a credit of 3% of the order value |
| 11 to 20 days | Written apology, a credit of 5% of the order value and expedited freight on the replacement shipment |
| More than 20 days | Written apology, a credit of 8% of the order value and the right to cancel the order without charge |

3.2 Credits are applied to the customer's next invoice and are not paid in cash.
3.3 Order value means the total value of the delayed order.

## 4. Backorders
4.1 Where a delay is caused by an out-of-stock item and the backorder will take more than 7 days, the customer may cancel the affected lines without penalty.

## 5. Exclusions
5.1 No compensation is payable for delays caused by force majeure events such as floods, strikes or government restrictions.
"""

P["hazardous_goods.md"] = """---
doc_id: POL-HAZ-001
title: Hazardous Goods Handling Policy
effective_date: 2025-03-01
version: 2.1
owner: Compliance
---
# Hazardous Goods Handling Policy

## 1. Scope
1.1 Hazardous goods are products in the Hazardous Goods catalogue category: flammable liquids, corrosives, compressed gases and similar regulated items. Examples:

| SKU | Product | Hazard class |
|---|---|---|
| MS-HAZ0101 | Industrial Acetone 20 L Drum | 3 (flammable liquid) |
| MS-HAZ0105 | Hydrochloric Acid 30% 25 L Carboy | 8 (corrosive) |
| MS-HAZ0110 | Argon Gas Cylinder 47 L | 2.2 (compressed gas) |

## 2. Carriers
2.1 Hazardous goods may be shipped only with approved hazardous-goods carriers: SafeHaul Logistics and VRL Logistics.
2.2 Hazardous goods are never shipped by air.
2.3 A customer's preferred-courier request cannot override clause 2.1.

## 3. Documentation
3.1 A Material Safety Data Sheet (MSDS) accompanies every hazardous consignment.
3.2 Customers ordering compressed gas cylinders must hold a valid gas purchase licence.

## 4. Returns
4.1 Hazardous goods cannot be returned once dispatched, except where the wrong item was shipped or the goods arrived damaged and this is reported within 48 hours of delivery.
4.2 Opened containers of hazardous goods are never accepted for return.

## 5. Quantity limits
5.1 An order for more than 15 units of any single hazardous SKU requires written approval from Compliance before dispatch.
"""

P["bulk_pricing.md"] = """---
doc_id: POL-PRC-001
title: Bulk Pricing Policy
effective_date: 2026-01-01
version: 2.0
owner: Sales Operations
---
# Bulk Pricing Policy

## 1. Principle
1.1 Quantity discounts apply automatically when the quantity of a single SKU on a single order line reaches a tier threshold.

## 2. Discount tiers

| Quantity on one line | Discount on catalogue unit price |
|---|---|
| 1 to 49 | 0% |
| 50 to 99 | 3% |
| 100 to 249 | 6% |
| 250 to 499 | 12% |
| 500 and above | 15% |

## 3. Calculation rules
3.1 The discount is calculated on the catalogue unit price and the discounted unit price is rounded to 2 decimal places.
3.2 Quantities of different SKUs are never added together to reach a tier.
3.3 Bulk discounts cannot be combined with customer-tier benefits or promotional pricing.

## 4. Worked example
4.1 120 units of MS-VLV0200 (Brass Ball Valve 2 inch BSP, catalogue price 1,875 rupees) fall in the 100 to 249 tier: 6% off gives a unit price of 1,762.50 rupees and a line value of 211,500 rupees.
"""

P["credit_terms.md"] = """---
doc_id: POL-FIN-001
title: Credit Terms
effective_date: 2025-04-01
version: 1.4
owner: Finance
---
# Credit Terms

## 1. Credit limits by customer tier

| Tier | Credit limit | Payment terms |
|---|---|---|
| Standard | 500,000 rupees | Net 30 days |
| Silver | 1,500,000 rupees | Net 45 days |
| Gold | 5,000,000 rupees | Net 60 days |

## 2. Orders above the credit limit
2.1 An order whose value exceeds the customer's available credit limit must be prepaid or approved by the Finance Controller before dispatch.

## 3. Late payment
3.1 Invoices unpaid after the due date accrue interest at 1.5% per month.

## 4. Review
4.1 Credit limits are reviewed once a year by Finance.
"""

P["refund_approval.md"] = """---
doc_id: POL-FIN-002
title: Refund Approval Policy
effective_date: 2026-02-01
version: 2.0
owner: Finance
---
# Refund Approval Policy

## 1. Verification
1.1 Every refund requires verification of the order, the delivery date and a reason code before any approval step.

## 2. Approval matrix

| Refund amount | Who may approve |
|---|---|
| Up to 5,000 rupees | Support agent or Desk assistant (auto-approve) |
| 5,001 to 10,000 rupees | Manager approval required |
| Above 10,000 rupees | Finance Controller only; support agents and Desk must not process these |

## 3. Manager response
3.1 Managers respond to approval requests within 1 business day.

## 4. Payment
4.1 Approved refunds are paid to the original payment method within 7 business days.

## 5. Fraud review
5.1 A customer with more than 3 refunds in the previous 90 days is flagged for fraud review. Any further refund for that customer needs manager approval regardless of amount.
"""

P["cancellations.md"] = """---
doc_id: POL-ORD-001
title: Order Cancellation Policy
effective_date: 2025-01-01
version: 1.2
owner: Customer Operations
---
# Order Cancellation Policy

## 1. Before dispatch
1.1 Orders with status pending or processing may be cancelled free of charge.

## 2. After dispatch
2.1 Orders with status shipped cannot be cancelled. Once the goods are delivered the customer may raise a return under the Returns Policy.

## 3. Non-cancellable items
3.1 Custom-cut and made-to-order items cannot be cancelled once production has started.

## 4. Refund of cancelled orders
4.1 Prepaid amounts for cancelled orders are refunded within 7 business days.
"""

P["damaged_goods.md"] = """---
doc_id: POL-QA-001
title: Damaged Goods Policy
effective_date: 2025-01-01
version: 1.1
owner: Quality Assurance
---
# Damaged Goods Policy

## 1. Inspection on delivery
1.1 Customers should inspect consignments on delivery and note visible damage on the carrier's delivery receipt.

## 2. Reporting
2.1 Damage in transit must be reported to Meridian within 7 days of delivery, with photographs of the goods and packaging.
2.2 Hazardous goods damage must be reported within 48 hours (see the Hazardous Goods Handling Policy).

## 3. Remedy
3.1 Meridian replaces or credits goods confirmed as damaged in transit. Return freight is paid by Meridian.
"""

P["customer_tiers.md"] = """---
doc_id: POL-CRM-001
title: Customer Tiers
effective_date: 2025-01-01
version: 1.0
owner: Sales Operations
---
# Customer Tiers

## 1. Tier thresholds

| Tier | Annual spend | Support response time |
|---|---|---|
| Standard | Below 2,500,000 rupees | 24 hours |
| Silver | 2,500,000 to 10,000,000 rupees | 8 hours |
| Gold | Above 10,000,000 rupees | 4 hours |

## 2. Benefits
2.1 Gold customers have a named account manager.
2.2 Tier benefits are support response times and credit limits. They do not include additional price discounts.
"""

P["international_shipping.md"] = """---
doc_id: POL-SHP-003
title: International Shipping Policy
effective_date: 2025-08-01
version: 1.0
owner: Logistics
---
# International Shipping Policy

## 1. Destinations
1.1 Meridian exports only to Nepal, Bhutan, Bangladesh and Sri Lanka.

## 2. Terms
2.1 Export orders are prepaid and quoted FCA (Incoterms) from the Pune warehouse.
2.2 Transit time is 10 to 15 business days after customs clearance documents are complete.

## 3. Restrictions
3.1 Hazardous goods are not exported.
"""

P["data_privacy.md"] = """---
doc_id: POL-PRV-001
title: Customer Data Privacy Policy
effective_date: 2025-01-01
version: 1.1
owner: Legal
---
# Customer Data Privacy Policy

## 1. Use of data
1.1 Customer personal data is used only to fulfil orders, provide support and meet legal obligations.

## 2. Sharing
2.1 Customer data is never shared with third parties other than carriers and payment providers needed to fulfil an order.
2.2 Customer lists must never be sent outside the company.

## 3. Retention
3.1 Order and invoice records are retained for 7 years. Support emails are retained for 3 years.

## 4. Requests
4.1 Requests to access or delete personal data are sent to the Data Protection Officer at privacy@meridiansupply.example.
"""

for name, body in P.items():
    open(f"{OUT}/{name}", "w", encoding="utf-8").write(body)
print(len(P), "policy docs")
