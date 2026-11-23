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
