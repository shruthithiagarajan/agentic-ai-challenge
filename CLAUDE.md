# CLAUDE.md - project guide for Claude Code

Project: **Desk**, the customer-operations assistant for the fictional Meridian Supply Co. Built across 15 progressive challenges (see the PDF). Current: **Challenge 1 - One call**.

## Commands
- Install: `pip install -e .`
- Config: copy `.env.example` to `.env` and fill `GROQ_API_KEY` (and optionally `DESK_MODEL`); loaded via python-dotenv
- Run: `desk ask "question"` (flags: `--model`, `--temperature`, `--simulate 429|timeout|network`)
- Test (offline, no key): `python -m unittest -v`
- Live Break it run: `python scripts/break_it.py --write`

## Rules
- Challenges 1-7: **no agent frameworks**, raw `openai` SDK pointed at Groq (`https://api.groq.com/openai/v1`). Write the loop yourself.
- Never commit `.env` or keys. Never hard-code model IDs or prices (read from env).
- Keep modules small: `desk/cli.py` (args + output), `desk/llm.py` (the call + retry), `desk/prompts.py`, `.env` loading via `python-dotenv` in `cli.main`.
- Every behaviour gets an offline test (fake the SDK). Live checks go in `NOTES.md`.
- `data/` is seed data. Anything under a `ground_truth/` folder is answer-key material: do not feed it to the agent.
- Tag each finished challenge `ch01`, `ch02`, ... and record Break it results in `NOTES.md`.

## Challenge 1 definition of done
- [ ] A general question gets a sensible answer (live)
- [ ] Usage footer on every call: input tokens, output tokens, latency, est. cost
- [ ] `--simulate 429` retries (RateLimitError / APITimeoutError / APIConnectionError, i.e. rate limit, timeout, bad network per the PDF; 1s, 2s backoff, max 3 attempts) then fails with a clear message
- [ ] Break it results written in `NOTES.md`, then `git tag ch01`
