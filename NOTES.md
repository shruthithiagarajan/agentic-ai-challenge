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
**Provider / Model:** Groq (`openai/gpt-oss-120b`, also the judge) · **Retrieval:** ChromaDB dense (`all-MiniLM-L6-v2`) + `rank-bm25`, merged with Reciprocal Rank Fusion · **No frameworks**

### 1. What was built
* **Heading-aware chunking:** one chunk per `## N.` section (id `file#N`, matching the golden set's section ids), Markdown tables never split, each chunk prefixed with `[Title | N. Heading | Effective YYYY-MM-DD]` → **58 chunks** (max 690 chars) instead of 15 whole-document chunks.
* **Hybrid search:** dense top-20 + BM25 top-20 (SKUs kept as whole tokens, `18,000` → `18000`) merged with RRF (k=60).
* **Rerank top-20 → top-5:** a fast feature reranker (query-word coverage, section-heading match, exact SKU match) on top of the RRF score. `sentence-transformers` cross-encoders were skipped: they pull in PyTorch (~1-2 GB, slow on CPU).
* **Query rewriting:** `desk ask "and for 30,000?" --prev "<earlier question>"` turns a follow-up into a standalone query with one LLM call (falls back to joining the questions if the call fails).
* **Latest `effective_date` preference:** two sections from the same policy family (`POL-RET-001` vs `POL-RET-002`) with ≥0.35 word overlap count as the same rule; the newer one is pulled into the top 5 and ranked above the older, and both get a `NOTE` naming the dates. The prompt tells the model to apply the latest rule and state its effective date.
* **`--catalog` (stretch):** adds the `catalog.csv` row of any SKU in the question as a citable chunk (`[catalog.csv#MS-ELC0410]`).
* **`desk eval-rag`:** step-by-step ablation (Recall@5, MRR@5) + LLM-judged faithfulness on the 40 golden questions. Reference answers go only to the judge, never to the answering model.

### 2. Build & Acceptance Verification ("Done when")
* **Unit Tests (`python -m unittest -v`):** Passed — **129/129** (31 new in `tests/test_ch05.py`): heading/table chunking, RRF math, BM25 rescue of a dense miss, exact-SKU boost, query rewrite + fallback, latest-date preference, Recall@5 / MRR math, judge flow.
* **`desk ingest` idempotency:** `stored 58 chunks (0 new, 0 stale removed)` on re-run.
* **Live `desk ask`:**
  * *Opened items:* "7 days under the updated terms effective 2026-03-01 [returns_opened_items.md#2] (replacing the 14-day window effective 2025-01-01 [returns.md#3])" ✅
  * *Follow-up* `"and for 30,000?" --prev "...18,000 rupees?"` → free freight [shipping.md#3] ✅
  * *Catalog* `"What warranty applies to MS-ELC0410?" --catalog` → 24 months ✅
  * *Refusal* `"Who is Meridian's CEO?"` → `not in the documents` ✅
  * Input tokens per question dropped from ~1,200-1,600 (Ch4) to ~750-950.

### 3. Ablation (`desk eval-rag --catalog`, 40 golden questions, k=5)
| Step | Recall@5 | MRR@5 | Follow-ups | Newer-first | Ctx tokens |
|---|---|---|---|---|---|
| Ch4 baseline: fixed 2000-char chunks, dense | 0.975 | 0.933 | 1/2 | 2/2 | 1320 |
| + heading-aware chunks with headers | 0.938 | 0.822 | 1/2 | 1/2 | 354 |
| + BM25 hybrid (RRF) | 0.975 | 0.872 | 1/2 | 2/2 | 376 |
| + rerank top-20 → 5 | 0.975 | 0.905 | 1/2 | 2/2 | 381 |
| + query rewrite for follow-ups | **1.000** | **0.950** | 2/2 | 2/2 | 381 |
| + latest effective_date preference | **1.000** | **0.950** | 2/2 | 2/2 | 383 |

**Faithfulness (LLM judge, 40 full-pipeline answers):** faithful to context **38/40 (95.0%)**, agrees with reference **39/40 (97.5%)**, refusals 0, errors 0 · 82 LLM calls, 64,300 in / 14,677 out tokens (120 rate-limit retries absorbed by backoff).

**Reading the table:** the Ch4 baseline looks strong only because each 2,000-char chunk is a whole policy, so it "contains" every section — at 3.5× the context. Small section chunks alone lose recall (0.938) and ranking (0.822); BM25 wins back exact terms (tables, SKUs, amounts), the rerank fixes ordering, and rewriting fixes the two vague follow-ups. The recency step doesn't move these metrics (the newer section was already first after the rerank) — its value is the dated `NOTE` that makes the model state which rule applies.

### 4. "Break it" Experiment Results

#### Experiment 1: Recency rule over-applied (g001)
* **Question:** "How long do I have to return unopened goods?"
* **What Happened:** Correct (30 days) but judged **unfaithful**: the model said the 2026 terms "replace the 30-day rule" from 2025 — both documents say the same thing, so nothing was replaced.
* **Root Cause / Takeaway:** The prompt's "apply the latest rule and say what it replaces" fires even when the two documents agree. Fix: only add the replaced-rule wording when the `NOTE` lines are present, or tell the model to mention replacement only when the values differ.

#### Experiment 2: Query rewrite drift (g040)
* **Question:** "what about for bulk?" after "What is the return window for unopened goods?"
* **What Happened:** Rewritten to *"bulk return window policy for unopened goods"*. Retrieval was perfect (Recall 1.0), but the answer added a 30-day window for bulk returns that the documents don't state → **unfaithful and incorrect**.
* **Root Cause / Takeaway:** The rewrite blended the old topic (30-day window) into the new question, and the model connected two facts that the policy never connects. Rewrites need to carry the *topic*, not the previous answer's facts.

#### Experiment 3: Rewrite output duplicated (g039)
* **What Happened:** The rewrite came back as the same query twice with no line break (`...30000 rupeesMeridian Supply Co freight charge...`). Retrieval still worked because both halves are the same query.
* **Root Cause / Takeaway:** LLM output used as an internal input still needs validation (length cap / de-duplication), not just "take the first line".

#### Experiment 4: Whole-document chunks hide their cost
* **What Happened:** The fixed-size baseline scores Recall@5 0.975 / MRR 0.933 — close to the final pipeline — while sending **1,320** context tokens per question vs **383**.
* **Root Cause / Takeaway:** Recall alone rewards stuffing more text into each chunk. Measure context size (and faithfulness) next to recall, or the "best" retriever is just the one that sends everything.

### 5. What fell short (→ next challenges)
* Recency wording leaks into answers where documents agree (g001).
* Follow-up rewriting needs output validation and must not carry over previous answers' facts (g039, g040).
* `desk ask` follow-ups need `--prev` by hand; the RAG pipeline is not wired into `desk chat` history yet.
* The judge is the same model that answers — a different judge model would make the faithfulness score more independent.
