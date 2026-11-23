# agentic-ai-challenge - Meridian Desk

Desk is the customer-operations assistant for the fictional Meridian Supply Co. (a B2B distributor of industrial supplies), built across 15 progressive challenges.
Challenges 1-7 use the raw `openai` SDK only (no agent frameworks), pointed at Groq's OpenAI-compatible API.

## Challenge 1 - One call

### Setup (PowerShell)
```powershell
python -m venv .venv            # skip if you already have one
.\.venv\Scripts\Activate.ps1
pip install -e .

$env:GROQ_API_KEY = "gsk_..."
$env:DESK_MODEL   = "openai/gpt-oss-120b"   # default; also openai/gpt-oss-20b, qwen/qwen3.8-27b
$env:DESK_PRICE_IN  = "0.15"                # optional, USD per 1M input tokens (footer shows n/a if unset)
$env:DESK_PRICE_OUT = "0.60"                # optional, USD per 1M output tokens
```
Or put the same values in a `.env` file (copy `.env.example`); it is loaded automatically and real environment variables take priority. The two price values above are placeholders: use the current rates for your model from Groq's pricing page.

### Run
```powershell
desk ask "What is a ball valve?"
desk ask "What is a ball valve?" --model openai/gpt-oss-20b
desk ask "What is a ball valve?" --model qwen/qwen3.8-27b
desk ask "Suggest a name for a brass valve brand" --temperature 0
desk ask "Suggest a name for a brass valve brand" --temperature 1
desk ask "hi" --simulate 429        # forced rate limit: retries after 1s and 2s, then exits non-zero
desk ask "hi" --simulate timeout
desk ask "hi" --simulate network
```
Output is the answer followed by a footer: `input tokens | output tokens | latency | est. cost`.
Rate limits (429), timeouts and network failures (`RateLimitError`, `APITimeoutError`, `APIConnectionError`) are retried (max 3 attempts, 1s then 2s backoff). Other errors (bad key, unknown model, request too large) fail immediately with a clear message.
Empty questions and `--temperature` outside 0-2 are rejected locally (exit code 2) without calling the API.
If `desk` is not found, use `python -m desk.cli ask "..."`.

### Tests (offline, no API key, no network)
```powershell
python -m unittest -v
```

### Break it + stretch (live, needs GROQ_API_KEY)
```powershell
python scripts/break_it.py --write     # appends results to NOTES.md
```
