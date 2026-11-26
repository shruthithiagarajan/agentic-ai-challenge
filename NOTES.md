# Agentic AI Engineering — Build & Break-It Notes

## Challenge 1: One call (`ch01`)
**Provider / Model:** Groq (`openai/gpt-oss-120b`)

### 1. Build & Acceptance Verification ("Done when")
* **Unit Tests (`python -m unittest -v`):** Passed — **21/21 tests** ran in `0.167s` (`OK`), covering retry backoff (1s → 2s), missing API key handling, temperature validation, model precedence, and UTF-8 output.
* **General Question (`desk ask "What is a ball valve?"`):** Passed — returned an accurate technical overview with the usage footer (`input tokens: 122 | output tokens: 340 | latency: 2.47s | est. cost: n/a`).
* **Failure & Exponential Backoff Handling:**
  * `--simulate 429`: Retried 3 times (`1s`, `2s` backoff) and exited cleanly with code `1`.
  * `--simulate timeout`: Retried 3 times (`1s`, `2s` backoff) and exited cleanly with code `1`.
  * `--simulate network` & broken proxy (`HTTPS_PROXY="http://127.0.0.1:9"`): Retried 3 times (`1s`, `2s` backoff) and exited cleanly with code `1`.
  * Missing positional argument (`desk ask`): Rejected locally by `argparse` with exit code `2`.

### 2. "Break it" Experiment Results

#### Experiment 1: Stateless Memory (`Ravi` Test)
* **Commands Run:**
  1. `desk ask "My name is Ravi"` → `"Hello Ravi, how can I assist you today?"` (`latency: 0.67s`)
  2. `desk ask "what is your name"` → `"I’m Desk, the customer‑operations assistant for Meridian Supply Co. How can I help you today?"` (`latency: 0.58s`)
  3. `desk ask "what is my name"` → `"I’m sorry, but I don’t have access to that information. Could you please let me know your name or the account details you’d like to reference? I’ll be happy to assist you further."` (`latency: 0.88s`)
* **Root Cause / Takeaway:** Each `desk ask` command spawns an isolated API call with no conversation history. The model knows its own system prompt (`Desk`), but immediately forgets user details across turns—motivating a stateful message-history loop in **Challenge 2**.

#### Experiment 2: Blank Terminal Wait (No Streaming)
* **Command Run:** `desk ask "What is a ball valve?"` (340 output tokens)
* **What Happened:** The terminal stayed completely blank for **2.47s** (compared to `0.58s–0.88s` for short replies) until the entire response dumped at once.
* **Root Cause / Takeaway:** Without streaming, latency scales linearly with output length and leaves the user staring at an empty screen—motivating token streaming in **Challenge 2**.

#### Experiment 3: Hallucinated Company Policy
* **Command Run:** `desk ask "What is Meridian's return window for opened items?"` (`latency: 1.09s`, `126 in / 333 out`)
* **What Happened:** Because Meridian's `policies/` documents haven't been connected yet, the model smoothly hallucinated an entire company policy, including:
  * A fake **15-calendar-day** return window for unused items in original packaging.
  * Fake contact details (`returns@meridian.com` and phone number `1-800-555-0199`, Mon–Fri 8 am–6 pm EST).
* **Root Cause / Takeaway:** Without document grounding (RAG) or strict refusal instructions, the LLM confidently invents plausible-sounding rules and contact info—motivating **Challenge 4 (Naive RAG)**.

---

## Challenge 2: Conversation (`ch02`)
**Provider / Model:** Groq (`openai/gpt-oss-120b`)

### 1. Build & Acceptance Verification ("Done when")
* **Multi-Turn Follow-Ups:** Passed — Desk resolved contextual references across turns (e.g., *"And what about the second one?"* correctly referred to the stainless-steel pressure gauge from Turn 1).
* **Token Streaming:** Passed — output visibly streamed token-by-token to the terminal instead of blocking on a blank screen.
* **Session Commands (`/reset`, `/save`, `/load`):** Passed — saved 4 messages to `.desk_sessions\shruthi_chat_1.json`, cleared history via `/reset` (confirming Desk forgot the user's name), and reloaded `shruthi_chat_1` to restore full conversation recall.
* **Sliding Window (`DESK_MAX_HISTORY_TOKENS`):** Verified that setting `DESK_MAX_HISTORY_TOKENS=150` dropped older message pairs (`[history] dropped 2 oldest message(s)`) while keeping the system prompt pinned.

#### Live Terminal Transcript (`desk chat`)
```text
(.venv) PS D:\Shruthi\agentic-ai-challenge> $env:DESK_MAX_HISTORY_TOKENS = "3000"
(.venv) PS D:\Shruthi\agentic-ai-challenge> desk chat
Desk chat (openai/gpt-oss-120b). commands: /reset  /save <name>  /load <name>  /exit
you> Hi, I'm Shruthi. I want to check on two items: a 2-inch brass ball valve and a stainless steel pressure gauge.
desk> Hello Shruthi! I’d be happy to look into the 2‑inch brass ball valve and the stainless‑steel pressure gauge for you.

Could you let me know what you’d like to check on them? For example:

* Are you looking for current stock/availability?  
* Do you have an existing order and need a status update (please share the order number)?  
* Any other specific detail you need?

Just let me know, and I’ll help you from there.
--
input tokens: 211 | output tokens: 189 | latency: 0.98s | est. cost: n/a | history: ~278/3000 tokens
you> And what about the second one?
desk> Sure thing, Shruthi!  

For the stainless‑steel pressure gauge, could you let me know what you’d like to find out? For example:

* Current stock/availability?  
* Pricing or lead time?  
* Whether it’s part of an existing order (please share the order number if you have one)?  

Just tell me what you need, and I’ll help you from there.
--
input tokens: 327 | output tokens: 188 | latency: 0.84s | est. cost: n/a | history: ~377/3000 tokens
you> /save shruthi_chat_1
saved 4 messages to .desk_sessions\shruthi_chat_1.json
you> what is my name
desk> Your name is Shruthi.
--
input tokens: 422 | output tokens: 42 | latency: 0.56s | est. cost: n/a | history: ~393/3000 tokens
you> /reset
history cleared
you> what is my name
desk> I’m sorry, but I don’t have that information. If you’d like to share your name, I’ll be happy to address you accordingly.
--
input tokens: 187 | output tokens: 77 | latency: 0.66s | est. cost: n/a | history: ~180/3000 tokens
you> /save shruthi_chat_2
saved 2 messages to .desk_sessions\shruthi_chat_2.json
you> /load shruthi_chat_1
loaded 4 messages from 'shruthi_chat_1'
you> what is my name
desk> Your name is Shruthi.
--
input tokens: 422 | output tokens: 53 | latency: 0.41s | est. cost: n/a | history: ~393/3000 tokens
you> /exit
bye
```

### 2. "Break it" Experiment Results

#### Experiment 1: Unstructured Prose vs. Python `json.loads` (5 Emails × 3 Runs)
* **What Was Tested:** Pasted 5 customer order emails into `desk chat` asking for extracted order lines as JSON, repeated 3 times with `/reset` between runs, and passed raw output into `json.loads()`.
* **What Happened:** `json.loads()` crashed with `json.decoder.JSONDecodeError: Expecting value: line 1 column 1 (char 0)` because chat completions wrap data in conversational text, Markdown fences (` ```json `), and inconsistent JSON field names/types across runs.
* **Root Cause / Takeaway:** Free-form chat text cannot be reliably consumed by downstream software. This motivates **Challenge 3 (Structured outputs)** using strict Pydantic/JSON schemas and validation repair loops.

#### Experiment 2: 40-Turn Conversation Forgetting Test (Parked for Challenge 9)
* **What Was Tested:** Sent a 40-turn conversation where Turn 2 established a specific fact (`project code DELTA-99 / preferred courier BlueDart`), then asked about Turn 2 on Turn 40.
* **What Happened:** Once conversation history exceeded `DESK_MAX_HISTORY_TOKENS` (`3000`), the sliding window automatically evicted Turn 2's messages, and Desk reported it had no record of that information on Turn 40.
* **Root Cause / Takeaway:** A naive sliding window prevents context overflow, but causes "amnesia" for important early facts. **Parked for Challenge 9**, which fixes this via context compaction, scratchpad notes, and external long-term memory.

---

## Challenge 3: Structured outputs (`ch03`)
**Provider / Model:** Groq (`openai/gpt-oss-120b`, `json_schema` mode)

### 1. Build & Acceptance Verification ("Done when")
* **100% Schema Validation & Self-Healing Repair Loop:**
  * Processed all **30/30 emails** in `data/emails/` with zero crashes (`validated: 30/30 | needs_review: 0/30`).
  * **3 emails (`email_16`, `email_18`, `email_24`)** failed initial validation and automatically self-repaired on Attempt 2.
  * Batch metrics: `38,691` input tokens / `14,758` output tokens (`1,782` tokens/email), `233.0s` total latency (`7.77s`/email including 429 rate-limit backoffs).
* **Field-Level Accuracy Report (`desk accuracy` on 15 hand-labeled emails):**
  * `customer`: **15/15 (100.0%)**
  * `intent`: **15/15 (100.0%)**
  * `requested_date`: **13/15 (86.7%)**
  * `line_items`: **12/15 (80.0%)**
  * `urgency`: **12/15 (80.0%)**
  * `secondary_intents`: **11/15 (73.3%)**
  * **Overall Accuracy:** **86.7%**
  * *Notable error modes:* In `email_16`, order IDs (`MS-10433`, `MS-10401`) were misclassified as `line_items`; in `email_02`, `"before end of October"` was kept as a string instead of normalizing to `"2026-10-31"`.

### 2. "Break it" Experiment Results

#### Experiment 1: Plain-English Product Descriptions vs. `catalog.csv` SKUs
* **What Was Tested:** Checked extracted `line_items` across all 30 emails against `data/catalog.csv` (`SKUs not in catalog.csv: 0 | SKUs not written in the email: 0`).
* **What Happened:** Because the model has zero access to `catalog.csv`, emails describing products in words (e.g., *"the 2-inch brass valve"*) were left with `sku: null` instead of resolving to real catalog SKUs.
* **Root Cause / Takeaway:** Structured outputs guarantee valid JSON formatting, but cannot bridge the knowledge gap between plain-language descriptions and Meridian's internal catalog—motivating **Challenge 4 (RAG)**.

#### Experiment 2: Policy Question Without Document Retrieval
* **Command Run:** `desk ask "What is Meridian's return policy for opened items?"` (`latency: 1.01s`, `193 in / 147 out`)
* **What Happened:** Thanks to the Challenge 2 system prompt guardrail, Desk refused cleanly (*"I’m sorry—I don’t have the specific details of Meridian’s return policy for opened items..."*) instead of fabricating a fake policy.
* **Root Cause / Takeaway:** While guardrails prevent hallucinations, Desk cannot answer real business policy questions until we index and retrieve `data/policies/` via **Challenge 4 (Naive RAG)**.


## Challenge 4: Naive RAG (`ch04`)
**Provider / Model:** Groq (`openai/gpt-oss-120b`) · **Vector DB:** ChromaDB 1.5.9 (local, `.chroma/`) · **Embeddings:** Chroma default `all-MiniLM-L6-v2` (local ONNX, no API key)

### 1. Build & Acceptance Verification ("Done when")
* **Unit Tests (`python -m unittest -v`):** Passed — **96/96 tests** (29 new in `tests/test_ch04.py`) using a fake Chroma collection and a fake SDK: fixed-size chunking + overlap, 1-based chunk IDs, idempotent re-ingest, stale-chunk deletion, top-4 retrieval, citation checks, `--debug` output, `"not in the documents"` refusal, empty-index error.
* **Corpus:** 14 policy docs in `meridian_desk_data/policies/` (5 Markdown tables; `returns.md` 2025-01-01 contradicts `returns_opened_items.md` 2026-03-01).
* **Chunking:** fixed-size 2,000 chars (~500 tokens) with 200-char (~50 token) overlap → **15 chunks** (only `returns.md` splits into `#1`/`#2`; every other doc fits in one chunk).
* **`desk ingest` idempotency:**
  * Run 1: `loaded 14 files ... stored 15 chunks (15 new, 0 stale removed) ... now holds 15 chunks`
  * Run 2: `stored 15 chunks (0 new, 0 stale removed) ... now holds 15 chunks` — 15 IDs, 15 unique, zero duplicates.
* **`desk ask` before `ingest`:** `error: no policy documents are indexed - run desk ingest first` (no LLM call).
* **`--debug`:** prints the top-4 chunk IDs, cosine distance/similarity and a snippet before the LLM call, e.g. `[returns_opened_items.md#1] distance 0.553 (similarity 0.447)`.
* **Ch1 flags still work:** `--simulate 429` retries 1s → 2s then exits 1; `--model` / `--temperature` honoured; usage footer on every answer.

### 2. Live Q&A Results (questions from `evals/golden_retrieval.jsonl`)
| Question | Answer (cited) | Verdict |
|---|---|---|
| Return window for opened items? | 7 days [returns_opened_items.md#1] **and** 14 days [returns.md#1] | ⚠️ Reports both, does not say the 2026 doc supersedes |
| Return unopened goods? | 30 days 【returns.md#1】 | ✅ content / ⚠️ wrong bracket style |
| How to raise a return request? | Email returns@meridiansupply.example with order ID, SKU, qty, reason [returns.md#1] | ✅ (omits "RMA within 2 business days") |
| Who pays return freight for a wrong item? | Meridian [returns.md#1] | ✅ |
| Refund time after inspection? | 7 business days 【returns.md#1】 | ✅ content / ⚠️ bracket style |
| What does the warranty not cover? | Misuse, installation, wear & tear, consequential loss [warranty.md#1] | ✅ |
| Which carriers? | BlueDart, Delhivery, DTDC, VRL, SafeHaul [shipping.md#1] | ✅ |
| Warranty for bearings? | 6 months 【warranty.md#1】 | ✅ content / ⚠️ bracket style |
| When is freight free? | Orders ≥ ₹25,000 [shipping.md#1] (+ return-freight note) | ✅ (table lookup) |
| Freight on ₹18,000? | ₹450 [shipping.md#1] | ✅ (table lookup) |
| What is MS-HAZ0105? | Hydrochloric Acid 30% 25 L Carboy, class 8 【hazardous_goods.md#1】 | ✅ content / ⚠️ bracket style |
| Who is Meridian's CEO? | `not in the documents` | ✅ refusal |
| Can lithium batteries ship by air? | `not in the documents` | ✅ defensible — docs never classify lithium batteries as hazardous |
| 500-unit discount? | 15% [bulk_pricing.md#1] | ✅ (table lookup) |

**Score:** facts correct on all answerable questions; **4/11 broke the citation format**, **1 contradiction not resolved**.

### 3. "Break it" Experiment Results

#### Experiment 1: Contradicting Documents
* **Command:** `desk ask "What is the return window for opened items?"`
* **What Happened:** Retrieved both `returns_opened_items.md#1` (7 days, 2026-03-01) and `returns.md#1` (14 days, 2025-01-01) and reported both as equally valid.
* **Root Cause / Takeaway:** Naive RAG treats every chunk as equally true; nothing in retrieval or the prompt ranks by `effective_date`. A customer could be told the outdated 14-day window. Fix: carry `effective_date` as chunk metadata and prefer/flag the newest document.

#### Experiment 2: Citation Format Drift (`【 】` vs `[ ]`)
* **What Happened:** 4 of 11 answers cited as `【returns.md#1】` (full-width lenticular brackets, gpt-oss's native citation style) despite the prompt requiring `[returns.md#1]`. The citation checker flagged each one: `[citations] warning: answer has no [file.md#N] citations`.
* **Root Cause / Takeaway:** Prompt instructions alone don't guarantee output format. The model's trained citation habit wins ~36% of the time. Programmatic checking caught it; next step is to normalise `【】` → `[]` or re-prompt on violation (the warning message should also say "wrong citation format" rather than "no citations").
* **Fix applied:** `rag.normalize_citations()` now rewrites `【returns.md#1】`, `【warranty.md#1†L3-L5】` and grouped `[a.md#1, b.md#2]` into `[returns.md#1][b.md#2]` form before printing and checking. Re-running the 4 affected questions printed `[returns.md#1][returns.md#2]`, `[warranty.md#1]`, `[hazardous_goods.md#1]`, `[returns.md#1]` with **no citation warnings** (98/98 tests pass).

#### Experiment 3: Invented Chunk IDs (smaller model)
* **Command:** `desk ask "How fast are refunds approved?" --model openai/gpt-oss-20b --temperature 0`
* **What Happened:** Cited `[refund_approval.md#2]` and `[refund_approval.md#3]`, which don't exist (that file is a single chunk, `#1`). The checker flagged: `cites chunks that were not retrieved: refund_approval.md#2, refund_approval.md#3`.
* **Root Cause / Takeaway:** The model converted the document's *section numbers* (`## 2`, `## 3`) into chunk IDs. Citations look trustworthy but can be fabricated, so they must be validated against the retrieved set.

#### Experiment 4: Chunk Size vs Document Size
* **What Happened:** At 2,000 chars, 13 of 14 policies became a single chunk, so "retrieval" is effectively whole-document retrieval. Low similarity scores (0.11–0.51) still pulled irrelevant docs into the top 4 (e.g. `hazardous_goods.md` for a returns question), and each call cost ~1,200–1,600 input tokens.
* **Root Cause / Takeaway:** Fixed-size chunking ignores document structure (sections, tables) and there is no relevance threshold. This motivates **Challenge 5**: structure-aware chunking, measuring recall@k / MRR against `golden_retrieval.jsonl`, and a minimum-similarity cut-off.

### 4. What fell short (→ Challenge 5)
* No preference for the newest policy when documents conflict.
* Citation format not enforced (only warned).
* No retrieval quality measurement yet (recall@k, MRR).
* Single-shot `ask` can't handle follow-ups like "and for 30,000?" (no chat history in RAG).

---

## Challenge 5: Advanced RAG, measured (`ch05`)
**Provider / Model:** Groq (`openai/gpt-oss-120b`) + Local Embeddings (`all-MiniLM-L6-v2` via ChromaDB) + BM25 (`rank-bm25`)

### 1. Why We Built This: Naive RAG vs. Advanced RAG

| What Failed in Challenge 4 (Naive RAG) | How Challenge 5 Fixes It (Advanced RAG) |
| :--- | :--- |
| No way to measure quality (had to eyeball `--debug` scores) | **Step 1: Golden Set + Metrics (`Recall@5`, `MRR`, `Faithfulness`)** — builds an automated scorecard first. |
| Blind 2,000-char cutting sliced Markdown tables in half and lost document headers | **Step 2: Structure-Aware Chunking** — splits on `##` headings, keeps tables intact, and prepends `[Title \| Section \| Effective Date]`. |
| Dense embeddings missed exact SKUs (`MS-HAZ0101` / `MS-VLV0200`) | **Step 3: Hybrid Search (BM25 + Vectors + RRF)** — pairs exact keyword matching with semantic vectors. |
| Top-k noise & vague follow-ups (`"what about for bulk?"` failed in chat) | **Steps 4 & 5: Reranking (`20 → 5`) + Query Rewriting** — expands follow-ups using chat history and re-scores the top 20 candidates. |
| Contradicting policies (`2025` vs. `2026`) confused the model | **Step 6: Metadata Filtering (Latest Effective Date)** — prioritizes the newer `2026` document and explicitly cites its date. |

---

### 2. Evaluation Metrics Reference (40-Question Golden Set)

| Metric | What It Measures (Simple Explanation) | Example |
| :--- | :--- | :--- |
| **`Recall@5`** *(Target: `>= 0.85`)* | **"Did the right document show up anywhere in the top 5 results?"**<br>Checks how many of the 40 questions found the right chunk inside the top 5 returned chunks. | If 36 out of 40 questions had the right policy in the top 5:<br>**`Recall@5 = 36 / 40 = 0.90`** (90% — Pass!) |
| **`MRR`** *(Mean Reciprocal Rank)* | **"How close to #1 was the right document?"**<br>Gives `1.0` point if the right chunk was **#1**, `0.5` points if it was **#2**, `0.33` if **#3**, etc., averaged across all 40 questions. | Finding the right chunk at **#1** (`MRR = 1.0`) is much better than burying it at **#5** (`MRR = 0.20`). |
| **`Faithfulness`** *(LLM-Judged)* | **"Did the final written answer stick strictly to the retrieved chunks without making things up?"**<br>A judge LLM checks whether every claim in Desk's answer is actually supported by the 5 chunks. | Scored `0.0` to `1.0` (e.g., `0.95` means almost zero hallucinations). |

---

### 3. Build & Acceptance Verification ("Done when")
* **Unit Tests (`python -m unittest -v`):** Passed — **129/129** (31 new in `tests/test_ch05.py`).
* **Ablation Table (`desk eval-rag --catalog` on 40 golden questions, k=5):**

| Step | Recall@5 | MRR@5 | Follow-ups | Newer-first | Ctx tokens |
|---|---|---|---|---|---|
| Ch4 baseline: fixed 2000-char chunks, dense | 0.975 | 0.933 | 1/2 | 2/2 | 1320 |
| + heading-aware chunks with headers | 0.938 | 0.822 | 1/2 | 1/2 | 354 |
| + BM25 hybrid (RRF) | 0.975 | 0.872 | 1/2 | 2/2 | 376 |
| + rerank top-20 → 5 | 0.975 | 0.905 | 1/2 | 2/2 | 381 |
| + query rewrite for follow-ups | **1.000** | **0.950** | 2/2 | 2/2 | 381 |
| + latest effective_date preference | **1.000** | **0.950** | 2/2 | 2/2 | 383 |

  * **Recall@5 achieved:** `1.000` (`>= 0.85` target met)
  * **MRR achieved:** `0.950`
  * **Answer Faithfulness (LLM-judged + manual spot check):** `0.95` — 38/40 faithful to the retrieved chunks; 39/40 agree with the reference answer (82 LLM calls, 64,300 in / 14,677 out tokens).
  * *Manual spot check of the two misses:* **g001** ("return unopened goods") was right (30 days) but claimed the 2026 terms "replace" the 2025 rule although both documents agree; **g040** ("what about for bulk?") was rewritten to include the earlier 30-day topic and the answer invented a 30-day window for bulk returns.
  * *Reading the table:* the Ch4 baseline scores high only because each 2,000-char chunk is a whole policy (3.5× the context). Section chunks alone drop recall; BM25, the rerank and query rewriting win it back at ~383 tokens per question.
* **Exact SKU Lookup:** Verified `desk ask "What hazard class is MS-HAZ0101?"` (and `"What is MS-HAZ0105?"`) retrieves `hazardous_goods.md#1` at rank #1 via BM25 + RRF fusion and the exact-SKU rerank boost.
* **Contradictory Policy Resolution:** Verified `desk ask "What is the return window for opened items?"` ranks the newer `2026-03-01` document first (`returns_opened_items.md#2`: 7 days, 20% restocking fee) above the older `2025-01-01` `returns.md#3` and explicitly cites its effective date: *"7 days under the updated terms effective 2026-03-01 [returns_opened_items.md#2] (replacing the 14-day window effective 2025-01-01 [returns.md#3])"*.

---

### 4. "Break it" Experiment Results (Setting Up Challenge 6: Tool Calling)
*Run with `--model openai/gpt-oss-20b`: `gpt-oss-120b` had reached Groq's free-tier daily limit (200,000 tokens/day) after the full eval runs.*

#### Experiment 1: Live Database Lookup
* **Command Run:** `desk ask "Where is order MS-10422 right now?"`
* **What Happened:** Desk replied `"not in the documents"` because order statuses live in `orders.db` (a live SQLite database), not in static Markdown policy files. Retrieval still returned 5 loosely related chunks (cancellations, dispatch times) — the refusal came from the prompt, not from retrieval.
* **Root Cause / Takeaway:** RAG only searches static text documents; real-time order tracking requires live database **Tool Calling (`get_order`)** in **Challenge 6**.

#### Experiment 2: Multi-Step Arithmetic
* **Command Run:** `desk ask "What is a 12% bulk discount on 340 units at ₹1,875 each?"`
* **What Happened:** Hand check: `340 × ₹1,875 = ₹637,500`; `12%` discount = **`₹76,500`** (net total **`₹561,000`**, i.e. **`₹1,650`** per unit). This time the model's mental math was **correct** (₹1,650/unit, ₹561,000 total) — but it cited `[bulk_pricing.md#2][bulk_pricing.md#3]` for numbers that appear in no document, so computed values were dressed up as retrieved facts, and nothing guarantees the next calculation is right.
* **Root Cause / Takeaway:** LLMs are next-token predictors, not calculators—reliable business math requires a deterministic **`calculate(expression)` tool** in **Challenge 6**.

#### Experiment 3: Taking Action / State Mutation
* **Command Run:** `desk ask "Cancel that order."` (and with `--prev "Where is order MS-10422 right now?"`)
* **What Happened:** Both replied `"not in the documents"`. With `--prev`, the follow-up was rewritten to *"Meridian Supply Co. cancellation policy for order MS-10422"* and the cancellation policy was retrieved, but Desk still cannot modify `orders.db` or trigger any operational workflow—RAG is strictly read-only. (It never falsely claimed to have cancelled anything.)
* **Root Cause / Takeaway:** Giving the assistant hands to query live databases, run exact calculations, and execute actions motivates **Challenge 6 (Tool calling)**.

---

## Challenge 6: Tool calling (`ch06`)
**Provider / Model:** Azure AI Foundry `gpt-6.2-sol` (`DESK_MODEL_TOOLS`), tool requests sent through `/v1/responses` · also verified on `claude-sonnet-5-5` (Anthropic SDK, `AnthropicFoundry`) · **Data:** `orders.db` (SQLite, opened read-only), `catalog.csv`, the Ch5 policy index (`.chroma/`)

### 1. Why We Built This: Ch4 vs. Ch5 vs. Ch6

| | Challenge 4: Naive RAG | Challenge 5: Advanced RAG | Challenge 6: Tool calling |
| :--- | :--- | :--- | :--- |
| **Command** | `desk ask` | `desk ask` / `desk eval-rag` | `desk tools` / `desk eval-tools` |
| **Model** | Groq `gpt-oss-120b` | Groq `gpt-oss-120b` (Azure: Claude answers, GPT rewrites and judges) | Azure `gpt-6.2-sol` |
| **What it can read** | 14 policy docs, 15 fixed 2,000-char chunks | Same 14 docs, 58 heading-aware chunks with `[Title \| Section \| Effective]` headers | Policies (Ch5 retriever as `search_policies`) **plus** live `orders.db` and `catalog.csv` |
| **How it finds things** | Dense vector top-4 | BM25 + dense (RRF) → rerank 20→5 → latest-date preference → query rewrite | The **model chooses** which tool(s) to call and with what arguments |
| **Who does the work** | The model answers from pasted chunks | Same, with better chunks | **Our code** runs the lookup or the maths; the model writes the final answer from the results |
| **Arithmetic** | Model guesses | Model guesses (correct once, but cited policy docs for computed numbers) | `calculate` tool: exact, safe evaluator (no `eval`) |
| **Order status** | `not in the documents` | `not in the documents` | Read from `orders.db` (status, carrier, tracking, days late) |
| **Main weakness** | Missed contradiction, citation drift | Rewrite drift on vague follow-ups | Can't chain dependent steps (one round only) |
| **Writes data?** | No | No | **No**: every tool is read-only (writes come in later challenges) |
| **Cost per question** | ~1,200–1,600 input tokens | ~1,400 input tokens | ~2,800–3,100 input tokens over 2 LLM calls (the 5 tool definitions + prompt cost ~1,380 tokens on every call) |

**The same question, Ch5 vs. Ch6:** `"Is MS-VLV0200 in stock and what does it cost?"`
* **Ch5 `desk ask`:** *"Stock status for MS-VLV0200 is not in the documents"*; price ₹1,875 and an unrequested 6% tier price (₹1,762.50), both lifted from the 120-unit worked example in `[bulk_pricing.md#4]`. The price was right only because the policy happened to use this SKU as its example.
* **Ch6 `desk tools`:** called `check_stock` **and** `search_catalog` in the same turn: *"640 pieces in stock (check_stock). The list price is ₹1,875 per piece (search_catalog)."*

**The Ch5 Break it prompts, answered by Ch6:**

| Ch5 Break it prompt | Ch5 result | Ch6 result |
| :--- | :--- | :--- |
| Where is order MS-10422 right now? | `not in the documents` | `get_order` → shipped with Delhivery on Oct 2, promised Oct 6, tracking DL566614170 ✅ |
| 12% bulk discount on 340 units at ₹1,875 | Right numbers, but the model's own maths cited as policy | `calculate("340 * 1875 * 0.12")` = 76,500 and `calculate("340 * 1875 * (1 - 0.12)")` = 561,000 ✅ |
| Cancel that order. | `not in the documents` (read-only) | Still read-only by design: the prompt says Desk cannot cancel, change or refund (actions come later) |

---

### 2. What Was Built

| Tool | Reads | Arguments (JSON schema) | Error codes returned to the model |
| :--- | :--- | :--- | :--- |
| `get_order(order_id)` | `orders.db` (`mode=ro`, parameterised SQL) | `order_id` must match `^MS-\d{5}$` | `order_not_found`, `database_unavailable` |
| `search_catalog(query, filters)` | `catalog.csv` | `query` 1–200 chars; optional `filters`: `category` (enum of 11), `max_unit_price` ≥ 0, `min_stock` ≥ 0 | (empty result + message) |
| `check_stock(sku)` | `catalog.csv` | `sku` must match `^MS-[A-Z]{3}\d{4}$` | `sku_not_found` |
| `calculate(expression)` | AST walk: numbers, `+ - * / // % **`, `round/min/max/abs` | ≤ 200 chars | `invalid_expression`, `unsupported_expression`, `division_by_zero`, `number_too_large` |
| `search_policies(query)` | Ch5 hybrid retriever (top 5, citable ids like `shipping_delays.md#1`) | `query` 1–300 chars | `index_empty`, `index_unavailable` |

* **Round trip (`desk/tooluse.py`):** question + tool list → model asks for one or more tools → every call is validated and run (`Toolbox.run` never raises) → one `tool` message per call → final answer. If the model asks for a **second** round, Desk records it, does not run it, and tells the model to answer with what it has.
* **Failures are data:** unknown tool, invalid JSON, schema violations, not-found records and crashes all come back as `{"ok": false, "error": {"code", "message"}}`.
* **Tool descriptions** are written like docs for a new teammate: what the tool returns, when to use it, id formats, and what it cannot do (e.g. "product SKUs such as MS-VLV0200 are NOT order ids").
* **Logging:** every call is appended to `data/tool_calls.jsonl` (gitignored): timestamp, model, question, tool, raw arguments, ok/error code, milliseconds, result size.
* **Azure note:** `gpt-6.2-sol` rejects function tools on `/v1/chat/completions` while reasoning is on (`400 ... use /v1/responses or set reasoning_effort to 'none'`), and `reasoning_effort='none'` is not allowed for this model. `AzureGPT` therefore sends tool requests through the Responses API (stateless, `store=False`) and hands the returned items, including encrypted reasoning, back on the next call.
* **Claude note:** with `tool_choice: none` straight after tool results, Claude ended its turn with **no text**. Fixed by adding a short "no more tools, answer now" user message before the forced final call.

---

### 3. Build & Acceptance Verification ("Done when")
* **Unit Tests (`python -m unittest -v`):** Passed: **185/185** (41 new in `tests/test_ch06.py`), offline with a temp `orders.db`, a 5-row catalog and fake OpenAI / Anthropic / Responses clients. They cover:
  * schemas and the validator;
  * every tool and every error code;
  * a read-only DB and SQL injection;
  * calculator attacks;
  * two tools at once;
  * second round not run;
  * the per-reply call cap;
  * retry on 429;
  * the JSONL log;
  * both SDK adapters;
  * the CLI and `eval-tools` scoring.
* **Order status comes from the database:**
  * **Command Run:** `desk tools "Where is order MS-10422 right now?"`
  * **Tool:** `get_order({"order_id":"MS-10422"}) -> ok (2 ms)`
  * **Answer:** shipped with Delhivery on October 2, not yet delivered, promised October 6, 2026. The answer says the record has no live location and gives tracking **DL566614170**.
  * **Usage:** 2 LLM calls, 3,113 in / 102 out, 4.14s ✅
* **Discount maths went through the calculator:**
  * **Command Run:** `desk tools "What is a 12% bulk discount on 340 units at 1,875 rupees each?"`
  * **Tools:** `calculate("340 * 1875 * 0.12")` and `calculate("340 * 1875 * (1 - 0.12)")`
  * **Answer:** *saves **₹76,500**; total **₹5,61,000** after the discount* (matches the hand check from Ch5)
  * **Usage:** 2,943 in / 111 out ✅
* **Unknown order ID gives a graceful answer:**
  * **Command Run:** `desk tools "Status of MS-99999"`
  * **Tool:** `get_order -> error order_not_found (1 ms)`
  * **Answer:** *"Order MS-99999 wasn't found in Meridian's order database (get_order). Please double-check the order ID..."* No invented data ✅

#### Edge cases (live)
| Command | Tools run | Result | Verdict |
| :--- | :--- | :--- | :--- |
| `desk tools "Is MS-VLV0200 in stock and what does it cost?"` | `check_stock` + `search_catalog` (same turn) | 640 pcs in stock, ₹1,875 list price | ✅ two tools at once |
| `desk tools "What's the weather in Pune?"` | none (1 LLM call, 1,382 in) | "I can only help with Meridian's orders, products, and policies." | ✅ out of scope |
| `desk tools "Get order with id ''; DROP TABLE orders;--"` | none | Asked for an id in the `MS-10422` format | ✅ refused before any tool (the schema pattern and read-only DB are the backstop) |
| `desk tools "calculate(__import__('os').system('ls'))" --debug` | none | "I can't run shell commands." | ✅ refused by the model; the safe evaluator rejects it as `unsupported_expression` (unit test) |
| `desk tools "Where is order MS-10422?" --fail-tool get_order` | `get_order -> error tool_failed` | "The live order lookup is unavailable right now..." | ✅ crash returned as a structured error, no stack trace |
| `desk tools "   "` | none | `error: question is empty` (exit 2) | ✅ |
| `desk tools "hi" --simulate 429` | none | retries 1s → 2s → `Request failed after 3 attempts` | ✅ Ch1 retry still works |
| `desk tools "Where is order MS-10388?" --model claude-sonnet-5-5 --debug` | `get_order` | Full order summary, 6 days late, quotes the "need on time" note, suggests escalating, offers to check stock | ✅ Claude path works (5,675 in / 344 out) |

**Log sample (`Get-Content data\tool_calls.jsonl -Tail 5`):** one line per call, e.g. `{"tool": "check_stock", "arguments": "{\"sku\":\"MS-VLV0201\"}", "ok": true, "error": null, "ms": 0.1, "result_chars": 128}`. Tool latency is 0.1–3 ms for SQLite/CSV and 150–190 ms for `search_policies` (warm index). The first policy search in a process is ~1.5–3 s while the BM25 index loads.

---

### 4. Stretch: Vague Tool Description vs. Tool Selection
`desk eval-tools` runs the 15 prompts in `chat/tool_test_prompts.json`. "chose" = the round that ran + any second round the model asked for (not run). `--vague` changes only `check_stock` to *"Gets product data."* (parameter: *"an id"*).

| Run | Passed | Expected tools chosen | Extra tool calls | LLM calls | Tokens (in / out) |
| :--- | :--- | :--- | :--- | :--- | :--- |
| Normal descriptions | **11/15 (73%)** | 18/22 | 5 | 31 | 50,143 / 2,002 |
| Vague `check_stock` | **10/15 (67%)** | 17/22 | 6 | 31 | 46,890 / 2,179 |

* **What degraded:** prompt 7 *"Is MS-VLV0200 in stock and what does it cost?"* went from `[check_stock, search_catalog]` (PASS) to `[search_catalog]` only (FAIL). With no useful description, the model skipped the stock tool and relied on the `stock` column inside the catalog result. Prompt 3 also gained a redundant second `search_catalog`.
* **What did not degrade:** *"Any MS-VLV0300 left?"* still chose `check_stock`. It was the only tool taking a `sku`, so the parameter name carried the meaning. A strong model partly recovers from a bad description. The cost shows up on prompts where two tools overlap.
* **The 4 failures common to both runs:**
  * **#8 and #9: the one-round limit.** Each needed `calculate` *after* seeing the stock or price results, so the maths would have been a third round. #9 instead called `search_catalog` twice to compare prices.
  * **#10 and #11: refused without calling any tool.** On the SQL-injection and `__import__` prompts the model declined outright. That is safe behaviour, but the dataset expected the tool to be called and to return the validation error. The validator and safe evaluator are covered by unit tests instead.
* **Takeaway:** descriptions are part of the program. A one-line description cost one prompt (73% → 67%) here, and more where tools overlap. Overlapping tools (`check_stock` vs `search_catalog`, which both expose stock) need descriptions that say when to use which.

---

### 5. "Break it" Experiment Results (Setting Up Challenge 7: The Agent Loop)

#### Experiment 1: The Apex Industries Multi-Step Task
* **Command Run:** `desk tools "For every Apex Industries order delayed more than 5 days, check whether the items are back in stock and draft an apology quoting the right policy clause."`
* **What Happened:** One tool call only: `search_policies("shipping delay more than 5 days apology ...")`, which found the right clause. Desk then **asked the user for the Apex order IDs**: *"I can look up orders by ID, but I can't search the live order database by customer name, so I haven't verified any delays or stock yet."* It did quote the remedy correctly from `[shipping_delays.md#1][shipping_delays.md#3]`: written apology + 3% credit for 6–10 days late, 5% + expedited freight for 11–20 days, 8% + free cancellation for > 20 days.
* **Root Cause / Takeaway:** The task is a chain where each step needs the previous result: find the customer → list their orders → keep those > 5 days late → `check_stock` every line → pick the clause by days late → draft. We have no "orders by customer" tool, and even with one, a single round can't feed step 2's output into step 3. The honest refusal is the best possible answer for a one-round design.

#### Experiment 2: One Round Is Not Enough
* **Command Run:** `desk tools "Where is order MS-10388 and is everything in it in stock?"`
* **What Happened:** Round 1 ran `get_order` (delayed, 6 days late, lines MS-VLV0200 × 20 and MS-FIT0110 × 40). The model then asked for `check_stock(MS-VLV0200)` and `check_stock(MS-FIT0110)`. Desk printed `[tools] the model asked for a second round - NOT run` and the answer said **"I couldn't verify current stock for either item"** (3 LLM calls, 4,876 in / 215 out). Claude, asked only "Where is order MS-10388?", stopped after `get_order` and *offered* to check stock instead of asking.
* **Root Cause / Takeaway:** The SKUs to check only exist *after* `get_order` returns, so the stock check can't run in the same round. The model knew exactly what to do next; our code simply has no loop.

#### Experiment 3: Noticing the Urge to Hard-Code
* **What Happened:** The obvious "fix" is a Python function `apex_delay_apologies()`:
  1. `SELECT` the orders for the customer;
  2. filter on `days_late > 5`;
  3. loop `check_stock`;
  4. look up `shipping_delays.md#3`;
  5. fill in an apology template.

  That works for this one sentence and breaks for the next variation (another customer, "more than 10 days", "refund instead of apology").
* **Root Cause / Takeaway:** Hard-coding the sequence turns the assistant back into a script. The fix is the **agent loop (Challenge 7)**: keep calling the model, run whatever tools it asks for, feed the results back, and stop when it answers. Add step and budget limits, and a list-orders-by-customer tool. `ground_truth/apex_delayed_orders.json` stays an answer key for checking that run, never an input.

---

### 6. What fell short (→ Challenge 7)
* Exactly one round of tool calls: dependent lookups (order → its SKUs → stock → policy → draft) stop after step 1.
* No tool to list or filter orders by customer, status or delay, so "every Apex order" can't even start.
* Tool selection is 73% on the test prompts. The misses are mostly multi-step prompts, which a loop would fix.
* Each LLM call re-sends ~1,380 tokens of tool definitions and prompt. A loop multiplies that, so Ch7 needs step limits and token budgets.
* Still read-only: no cancel, refund or email actions (they need approvals and audit; later challenges).

---

## Challenge 7: The agent loop, from scratch (`ch07`)
**Provider / Model:** Azure AI Foundry `claude-sonnet-5-5` (`DESK_MODEL_AGENT`, Anthropic SDK `AnthropicFoundry`) · also runs on `gpt-6.2-sol` via `--model` · **No framework:** the loop is `desk/agent.py` (~270 lines incl. trace formatting and the early-stop report)

### 1. Why We Built This: Ch6 vs. Ch7

| | Challenge 6: Tool calling | Challenge 7: Agent loop |
| :--- | :--- | :--- |
| **Command** | `desk tools "question"` | `desk agent "task"` / `desk agent --preset apex\|impossible\|repeat` |
| **Model** | `gpt-6.2-sol` | `claude-sonnet-5-5` |
| **Rounds of tool calls** | Exactly **one**; a requested 2nd round is recorded, not run | **As many as needed**, until the model answers without a tool call or a limit stops it |
| **Who plans** | Effectively *us*: anything multi-step needed a human or hard-coded sequence | **The model**: it sees each result and decides the next step |
| **Tools** | 5: `get_order`, `search_catalog`, `check_stock`, `calculate`, `search_policies` | Same 5 + `list_orders` (find orders by customer), `draft_email` (writes to `outbox/` only) + `todo` with `--plan` |
| **Retrieval** | `search_policies` called once, if at all | **Agentic RAG**: the agent searches when it wants, refines the query and searches again (e.g. "2027 price list" → "price list effective 2027 price changes annual revision") |
| **Writes anything?** | No | Only email **drafts** in `meridian_desk_data/outbox/`, marked `DRAFT - NOT SENT`. Nothing is sent and the database stays read-only |
| **Safety limits** | 8 calls per reply | Max steps (25), token budget (500k), cost budget ($0.50, when prices are set), wall clock (300 s), 3rd identical call stops the run, 20 calls per turn |
| **Observability** | One JSONL line per tool call (`data/tool_calls.jsonl`) | Plus a **per-step trace**, live on screen and as `data/agent_traces/<time>_<model>.jsonl`: step, tool, arguments, result size, ms, tokens, result preview |
| **When it can't finish** | Answers with what it has | Exit code **3** and a "STOPPED … Managed …" report built from the trace (no extra LLM call, so no extra spend) |
| **Apex Break it task** | 1 tool call, then *"Please send the Apex order IDs"* | **Completed**: 6 steps, 35 tool calls, 8 drafts, every credit correct |
| **MS-10388 "is everything in stock?"** | `get_order`, then *"I couldn't verify current stock"* (2nd round not run) | `get_order` → `check_stock` ×2 → *"Everything in the order is in stock"* (3 steps, 12,298 tokens) |

---

### 2. What Was Built
* **The loop (`desk/agent.py`):**
  1. Check the limits (steps, wall clock).
  2. Call the model with the full history and the tools.
  3. If there are no tool calls, the model is finished (`done`).
  4. Otherwise check the token and cost budget, run every tool call (with the repeat detector and per-turn cap), append one result per call, and loop.
* **Stop conditions:** `max_steps`, `max_tokens`, `max_cost` (only when `DESK_PRICE_IN` / `DESK_PRICE_OUT` are set; they are not, so the footer shows `est. cost: n/a`), `timeout`, `repeated_call`. Each has a clear reason and detail in the trace.
* **New tools:**
  * `list_orders(customer, status, min_days_late)` matches the customer by name or id and returns orders with days late and their lines, plus the contact email.
  * `draft_email(to, subject, body)` writes a `.txt` draft into the outbox. The filename is sanitised, so it can't escape the folder.
  * `todo(items)` is the stretch tool: a plan checklist.
* **Prompt (`AGENT_SYSTEM_PROMPT`):** work in steps, call independent tools together, every number through `calculate`, re-search policies when results don't answer, drafts are never "sent", don't repeat identical calls, and finish with a summary that lists what could not be done.
* **Unit Tests (`python -m unittest -v`):** Passed: **208/208** (23 new in `tests/test_ch07.py`, scripted fake client, fake clock). They cover:
  * the new tools, including outbox containment;
  * a multi-step run to completion with full history;
  * the trace events and file;
  * each stop condition and its report;
  * the repeat detector with differently spaced JSON;
  * the per-turn cap;
  * tool failures fed back;
  * plan mode;
  * retry on 429;
  * Responses-API items handed back;
  * the CLI (exit codes 0 / 1 / 2 / 3, presets).

---

### 3. Build & Acceptance Verification ("Done when")

#### ✅ The Apex task from Challenge 6 completes end to end in one run
* **Command Run:** `desk agent --preset apex`
* **Trace (condensed):**

| Step | Model tokens (in / out) | Tool calls | What the agent did |
| :--- | :--- | :--- | :--- |
| 1 | 3,536 / 131 | 2 | `list_orders("Apex", min_days_late=6)` + `search_policies("delayed order apology compensation days late")`, in parallel |
| 2 | 6,249 / 1,326 | 22 | `check_stock` for all 14 SKUs + 8 credit `calculate`s. The last 2 hit the 20-per-turn cap (`too_many_calls`) |
| 3 | 9,287 / 362 | 2 | Re-ran the 2 capped calculations, including `27900*0.05`, the wrong tier (already wrong in step 2) |
| 4 | 9,508 / 407 | 1 | **Corrected itself:** `27900*0.08` (MS-10463 is 21 days late, so the > 20 days tier, 8%) |
| 5 | 9,597 / 4,258 | 8 | `draft_email` × 8, one per order |
| 6 | 14,351 / 870 | 0 | Final summary table |

* **Result:** `stop: done | steps: 6 | tool calls: 35 | LLM calls: 6`, 52,528 in / 7,354 out tokens, 44.8 s. There were 8 files in `meridian_desk_data\outbox` (930–1,140 bytes each).
* **Checked against `ground_truth/apex_delayed_orders.json`** (used only for checking; never given to the agent):

| Order | Days late | Stock verdict | Credit (agent) | Credit (answer key) |
| :--- | :--- | :--- | :--- | :--- |
| MS-10388 | 6 | all in stock | ₹1,347 | ₹1,347 ✅ |
| MS-10401 | 7 | VLV0300 + GSK0101 out | ₹894 | ₹894 ✅ |
| MS-10415 | 9 | BRG0110 only 5 of 30 | ₹1,559.61 | ₹1,559.61 ✅ |
| MS-10433 | 11 | all in stock | ₹5,685 | ₹5,685 ✅ |
| MS-10447 | 14 | all in stock | ₹1,552.50 | ₹1,552.50 ✅ |
| MS-10452 | 17 | all in stock | ₹6,810 | ₹6,810 ✅ |
| MS-10463 | 21 | VLV0300 out | ₹2,232 | ₹2,232 ✅ |
| MS-10471 | 26 | GSK0101 out | ₹2,080 | ₹2,080 ✅ |

  * The 8 orders and every stock verdict match the answer key.
  * MS-10478 and MS-10485 (3 and 4 days late) were correctly excluded by `min_days_late=6`.
  * MS-10023 (delivered 9 days late in July) was deliberately left out as "not currently delayed".
  * The drafts quote `[shipping_delays.md#3]`, and `#4` (the backorder cancellation right) for out-of-stock lines. They also flag that `[shipping_delays.md#2]` requires a new expected date and reason, which the data doesn't have.

#### ✅ A deliberately impossible task stops on budget and reports what it managed
* **Command Run:** `desk agent "Reconcile every order against the 2027 price list and email each customer the variance." --max-tokens 6000`
* **What Happened:**
  * **Step 1:** `search_policies("2027 price list")` + `search_catalog("2027 price list")`.
  * **Step 2:** the model asked for a refined search, but the run was over budget: `stop: max_tokens (8,416 tokens > 6,000)`, exit code 3.
  * **Report:** *"STOPPED before finishing: token budget used up… Managed in 2 step(s): search_policies x1, search_catalog x1. No drafts were saved. Next calls the model wanted (not run): search_policies({"query": "price list effective 2027 price changes annual revision"})."*
* **Also:** `desk agent --preset apex --max-steps 2` stopped with `max_steps`. Its report:
  * *Managed:* `list_orders x1, search_policies x1, check_stock x14, calculate x6`;
  * *Failed calls:* `calculate [too_many_calls] x2`;
  * *Orders looked at:* the 9 order ids;
  * *No drafts were saved.*
* **Without a budget squeeze:** `desk agent --preset impossible` finished honestly in **2 steps** (8,475 tokens): *"No 2027 price list exists… Sending customers made-up numbers would be worse than sending nothing."* The preset's task text gives it away ("No 2027 price list exists"), and a strong model stops by itself, so we need a tight budget to watch the budget stop fire.

#### ✅ You can follow the run by reading the trace
* **Command Run:** `desk agent "Where is order MS-10388 and is everything in it in stock?" --show-results`, then `Get-Content` on the newest file in `data\agent_traces`.
* **What Happened:** The live trace shows each step and tool call with arguments, result size and ms, plus the result preview with `--show-results`. The JSONL file holds the same events: `start`, `turn` ×3, `tool` ×3, `stop`.
  * **Step 1:** `get_order` (795 chars).
  * **Step 2:** `check_stock` ×2 in parallel (640 pcs, 4,200 pcs).
  * **Step 3:** the final answer, at a running total of 12,298 tokens.
* **Encoding note:** Windows PowerShell 5.1 displayed `₹` as `â‚¹` because `Get-Content` assumes ANSI. The file is valid UTF-8; read it with `Get-Content -Encoding utf8 <file>`.

#### Edge cases (live)
| Command | Result | Verdict |
| :--- | :--- | :--- |
| `desk agent --preset repeat` | 1 `check_stock` (0 pcs), then: *"I can't keep checking until it goes above zero… I have no way to wait or monitor stock"* (2 steps) | ✅ No loop. The prompt rule ("don't repeat identical calls") was enough, so the code-level detector (3rd identical call → `repeated_call`) stayed the unit-tested backstop |
| `desk agent "Where is order MS-10388?" --fail-tool get_order` | `get_order` failed twice (`tool_failed`, retried once), then: *"Retrying the same call won't change the result"*, with a suggestion to give the customer name so `list_orders` can be used | ✅ Failure fed back as data; sensible single retry; a 3rd identical call would have been stopped by the detector |
| `python -m unittest -v` | `Ran 208 tests … OK` | ✅ |

---

### 4. Stretch: Plan First (`--plan`, the `todo` tool)
| Apex run | Steps | Tool calls | Tokens | Time |
| :--- | :--- | :--- | :--- | :--- |
| No plan (first run) | 5 | 42 | 60,569 | 54.0 s |
| No plan (second run) | 6 | 35 | 59,882 | 44.8 s |
| `--plan` | 6 (1 is the `todo` call) | 35 | 57,337 | 42.8 s |

* **Result:** all three runs produced the same 8 correct drafts.
* **Plan vs no plan:** planning spent one extra step writing the checklist, and the work after it took 5 steps. The difference between plan and no-plan is no bigger than the run-to-run difference without a plan (5 vs 6 steps, 42 vs 35 calls).
* **Takeaway:** for a task with an obvious structure, a strong model already plans internally and batches its calls in parallel. An explicit plan pays off mainly as **observability** (the checklist is in the trace) and on longer, less obvious tasks. On this task it neither helped nor hurt measurably.

---

### 5. "Break it" Experiment Results (Setting Up Challenge 8: MCP)

#### Experiment 1: "A teammate wants your order tools inside their IDE assistant"
* **Setup:** list what sharing would take today.
* **What Happened:** there is no standard way. They would need `desk/tools.py` (566 lines: schemas, validator, handlers, data paths) **plus** our provider adapters in `desk/llm.py` (~90 lines: OpenAI-style → Anthropic `tool_use` mapping and the Azure Responses mapping), our `.env` layout, the `orders.db` path and the Chroma index. The options:

  | Option | Problem |
  | :--- | :--- |
  | Copy the code | Two copies that drift apart |
  | Shell out to `desk tools` | Parses prose; can't call one tool directly |
  | Wrap it as an HTTP API | They still write a client and re-declare every schema in *their* assistant's tool format |

* **Root Cause / Takeaway:** our tools are locked into our loop. Even inside Desk, the same tools needed one adapter per model provider.

#### Experiment 2: Add a third-party capability and count the custom glue
* **Setup:**
  * Built a shipping-carrier tool by hand, `track_shipment(awb)`, from the stub manifest `mcp/fake_servers/carrier_tracking.json`, on a snapshot of the working tree.
  * Measured the diff, saved it as a patch, then restored the tree.
* **Before the glue** (run on branch `breakit-ch07-carrier`, which had no carrier code):
  * `desk agent "Where is order MS-10422? Track the shipment with the carrier too."` answered from `get_order` only: *"I can't track the shipment with the carrier. None of my tools connect to Delhivery or any other carrier's tracking system."*
  * `git diff --stat main` on that branch showed only the uncommitted Ch7 work (8 files, +252 / −7, plus the new `desk/agent.py` and `tests/test_ch07.py`). That's the size of adding the loop itself.
* **After the glue:** the same question ran `get_order` → `track_shipment("DL566614170")` → answer in 3 steps (13,295 tokens), with Delhivery scan events and ETA.
  * The agent also noticed that the hand-made stub data was inconsistent: the last scan said "destination hub" in Ahmedabad for a Bengaluru customer. Canned test data needs the same care as real data.
* **Glue counted for ONE read-only endpoint (1 of the carrier's 4 tools):**

| Piece | Lines |
| :--- | :--- |
| `desk/carrier.py`: config + API key, HTTP call, carrier JSON shape → ours, HTTP 401/403/404/429/5xx + network errors → structured errors (incl. 12 lines of stub data) | 71 |
| `desk/tools.py`: JSON schema + teammate-style description, handler, registration | 21 |
| `desk/cli.py` (`--fail-tool` choice) + `.env.example` (`CARRIER_API_URL`, `CARRIER_API_KEY`) | 6 |
| `tests/test_breakit_carrier.py` | 42 |
| Ripple: existing Ch7 tests pinning the tool list and count broke and had to change | 2 |
| **Total** | **~142 lines in 6 files, 0 lines of business logic** |

* **Root Cause / Takeaway:** every line was protocol plumbing (auth, shapes, error codes, registration, tests), and it works **only inside Desk**. Slack and GitHub (`slack_stub.json`, `github_stub.json`) would each need the same again.

#### Experiment 3: 5 agents × 10 systems
* **What Happened:**
  * Point to point, every agent needs its own glue for every system: **5 × 10 = 50 integrations**. At ~140 lines each that's **~7,000 lines**, and one carrier API change means 5 separate fixes.
  * With a shared protocol, each system is wrapped once as a server and each agent implements one client: **5 + 10 = 15 pieces**.
* **Root Cause / Takeaway:** the loop is the easy part (~100 lines). Integration grows as **M × N**. That motivates **Challenge 8, the Model Context Protocol (MCP)**: tools are published once as MCP servers and any MCP-capable agent can discover and call them, so the cost drops to **M + N**.

---

### 6. What fell short (→ Challenge 8)
* Every new system (carrier, Slack, GitHub) means hand-written glue inside Desk, and nobody else can reuse our tools.
* The tool list is hard-coded. The agent can't discover new capabilities at runtime.
* The cost budget is not enforced until `DESK_PRICE_IN` / `DESK_PRICE_OUT` are set (the token budget is the active guard).
* Each turn re-sends the whole history: the Apex run grew from 3.5k to 14k input tokens per turn (52.5k total). Longer tasks need context management (Challenge 9, memory).
* Drafts need a human to review and send. There is no approval workflow yet (Challenges 10–11).
