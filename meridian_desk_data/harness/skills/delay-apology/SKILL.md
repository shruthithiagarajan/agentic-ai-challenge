---
name: delay-apology
description: Draft an apology email for a delayed order, quoting the shipping-delay compensation clause.
---
# delay-apology
1. Call get_order for the order ID; compute days late = as_of_date - promised_date (or delivered_date - promised_date).
2. Run `python scripts/credit_pct.py <days_late>` to get the credit percentage and remedy.
3. Check stock for each line with check_stock and say which items are back in stock.
4. Quote shipping_delays.md section 3 and draft the email with draft_email. Never send.
