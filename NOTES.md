# NOTES - Break it results

## Challenge 1 - One call
Status: done. Code + 16 offline tests pass. Live run on Groq `openai/gpt-oss-120b` on 2026-11-23 (full output in "live Break it run" below).

### Done-when checklist
- [x] General question gets a sensible answer (live): "In one sentence, what is an API rate limit?" got a correct one-line answer
- [x] Usage footer appears on every call (live): `input tokens: 127 | output tokens: 70 | latency: 1.55s | est. cost: n/a` (cost shows n/a until `DESK_PRICE_IN`/`DESK_PRICE_OUT` are set in `.env`)
- [x] Forced failure retries then fails with a clear message (`--simulate 429`: retries after 1s, then 2s, gives up after 3 attempts, exit code 1; unit-tested too)

### Break it
1. `"My name is Ravi"` then `"What is my name?"`: the second call says "I don't have access to that information". **No memory**: each call is stateless, so the model sees only one message.
2. Long question (653 words): the reply took **1.23s**, and the screen stays blank until the whole answer arrives (no streaming). Groq is fast, so the wait is short here, but on a slower model or a longer answer it would feel stuck.
3. `"What is Meridian's return window for opened items?"`: it **made up a policy**. It said opened items are *not returnable* except defective or wrong items, with "7 business days" notice, return shipping at the customer's cost, and a fake phone number `1-800-555-0199`. The 7 days matches by luck, but it missed the 20% restocking fee and the 2026-03-01 effective date, and it sounded completely sure.

### Stretch
- Same question 5x: at **temperature 0** we got 3 distinct answers out of 5 (mostly the same, but not deterministic on Groq); at **temperature 1** we got 5 out of 5 distinct. Temperature 0 lowers the variation but doesn't remove it.

### What fell short (-> Challenge 2)
- No conversation memory: every `desk ask` starts from zero.
- No access to Meridian's data (`data/policies`, orders...), so it invents answers about company policy with confidence instead of saying "I don't know".
- No streaming: the user stares at a blank terminal until the full reply is ready.
- The cost estimate needs prices in `.env`; there is no default.
- Output encoding: replies contain Unicode characters (e.g. U+2011); piped output on Windows crashed until stdout was switched to UTF-8 (fixed in `cli.utf8_output`).

## Challenge 1 - live Break it run

### 1. No memory between calls

**Call 1**

Q: My name is Ravi

> Hello Ravi! How can I assist you today?

`in 120 / out 54 tokens, 0.68s, n/a`

**Call 2 (separate call)**

Q: What is my name?

> I’m sorry, but I don’t have access to that information. If you need help with an order or account detail, please provide the relevant reference number or other identifying information, and I’ll be happy to assist.

`in 121 / out 97 tokens, 0.64s, n/a`

### 2. Long question - blank-terminal wait

The terminal shows nothing until the whole reply is done: **1.23s** of blank screen for a 653-word question.

**Reply**

Q: I run a fabrication unit. We use brass and stainless valves on water, mild-chemical and compressed-air lines and want the trade-offs. We use brass and stainless...

> - **Corrosion & Compatibility** – Stainless‑steel (especially 304/316) resists rust and most chemicals, making it the safest choice for mild‑chemical and high‑humidity compressed‑air lines; brass is generally fine for plain water but can dezincify or tarnish when exposed to aggressive chemicals or chlorinated water.  
> 
> - **Cost & Availability** – Brass valves are typically 30‑50 % cheaper and have a larger inventory of sizes and fittings; stainless valves carry a higher upfront price but lower long‑term replacement costs in corrosive environments.  
> 
> - **Temperature & Pressure Limits** – Stainless‑steel maintains strength at higher temperatures (up to 600 °F/315 °C) and pressures, whereas brass is best limited to ≤ 250 °F (≈ 120 °C) and moderate pressures; for high‑temperature hot‑water or steam‑assisted systems, stainless is preferred.  
> 
> - **Mechanical Strength & Wear** – Stainless offers superior tensile strength and fatigue resistance, reducing the risk of valve seat wear in high‑velocity compressed‑air applications; brass provides adequate strength for low‑pressure water service but can wear faster under abrasive flow.  
> 
> - **Maintenance & Longevity** – Brass valves are easier to machine and repair on‑site, but may require periodic cleaning to prevent mineral buildup; stainless valves need less routine maintenance and typically deliver longer service life in aggressive or moist environments, offsetting their higher initial cost.

`in 1013 / out 371 tokens, 1.23s, n/a`

### 3. Invented policy

**Answer (the model has never seen Meridian's policies)**

Q: What is Meridian's return window for opened items?

> Meridian’s standard return policy does **not** allow returns of opened items, except when the product is defective or does not match the order. In those cases you must:
> 
> 1. Notify your Meridian representative or our Returns Team within **7 business days** of receipt.  
> 2. Provide a brief description of the issue (and, if possible, photos).  
> 3. Return the item in its original packaging, with all accessories and documentation, at your expense unless the return is due to a Meridian error.
> 
> If the return is approved, we’ll issue a credit or replacement per the terms of your contract. For any other questions, feel free to reach out to your account manager or contact us at 1‑800‑555‑0199.

`in 126 / out 248 tokens, 0.92s, n/a`

Truth per `data/policies`: 7 days and 20% restocking fee (returns_opened_items.md, effective 2026-03-01); the older returns.md says 14 days / 10%.

### Stretch - same question x5 at temperature 0 vs 1

- temperature 0: **3 distinct answers out of 5**
  - 'Here are a few concise, professional brand‑name ideas for a new line of brass ball valves:\n\n| # | Br'
  - 'Here are a few concise, professional brand‑name ideas for a new line of brass ball valves:\n\n| # | Br'
  - 'Here are a few concise, professional brand‑name ideas for a new line of brass ball valves:\n\n| # | Br'
  - 'Here are a few concise, professional brand‑name ideas for a new line of brass ball valves:\n\n| # | Br'
  - 'Here are a few concise, professional name ideas for a new brass ball‑valve brand:\n\n| # | Suggested B'

- temperature 1: **5 distinct answers out of 5**
  - 'Here are a few concise, professional brand‑name ideas that convey durability, precision, and the pre'
  - 'Here are a few concise, professional‑sounding brand names that emphasize durability and precision fo'
  - 'Here are a few brand‑name ideas that convey durability, precision, and the brass‑construction of the'
  - 'Here are a few concise, professional brand‑name options for a brass ball valve line:\n\n| # | Suggeste'
  - 'Here are a few concise, professional name ideas for a brass‑focused ball‑valve brand:\n\n| # | Brand N'

