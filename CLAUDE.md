# CLAUDE.md - project guide for Claude Code

Project: **Desk**, the customer-operations assistant for the fictional Meridian Supply Co. Built across 15 progressive challenges (see the PDF). Current: **Challenge 1 - One call**.

## Commands
- Install: `pip install -e .`
- Config: copy `.env.example` to `.env` and fill `AZURE_AI_API_KEY` + the two Azure base URLs; `DESK_MODEL` is the deployment (`gpt-6.2-sol`, or `claude-sonnet-5-5` - names starting with `claude` use the Anthropic endpoint). Groq is kept but commented out (`DESK_PROVIDER=groq` + `GROQ_API_KEY` to switch back). Each challenge's command has its own model, `DESK_MODEL_<ROLE>` (ONECALL, CHAT, EXTRACT, RAG, REWRITE, JUDGE, TOOLS, AGENT; see `llm.ROLES`), with `--model` overriding and `DESK_MODEL` as the fallback. Loaded via python-dotenv
- Run: `desk ask "question"` (flags: `--model`, `--temperature`, `--simulate 429|timeout|network`)
- Ch6 tools: `desk tools "question"` (flags: `--debug`, `--fail-tool NAME`, `--vague`, `--simulate`) and `desk eval-tools` (tool selection on `chat/tool_test_prompts.json`). Tool calls on Azure GPT go through `/v1/responses` (chat.completions rejects tools with reasoning on); every call is logged to `data/tool_calls.jsonl`
- Ch7 agent: `desk agent "task"` or `desk agent --preset apex|impossible|repeat` (flags: `--plan`, `--max-steps`, `--max-tokens`, `--max-cost`, `--timeout`, `--show-results`, `--fail-tool`); exit 3 = stopped on a limit. Trace per run in `data/agent_traces/`, drafts in `meridian_desk_data/outbox/` (never sent)
- Ch8 MCP: `python -m meridian_ops` (stdio) or `python -m meridian_ops --http --port 8080` (stateless Streamable HTTP, bearer tokens from `meridian_desk_data/mcp/auth_tokens.json`, 401 / 403 per tool scope); `desk agent` now discovers its tools from `mcp_servers.json` (`--only NAME`, `--servers FILE`, `--prompt delay-apology --arg order_id=...`; `--local` = Ch7 in-process tools); `desk mcp list|call`; stretch LB: `python -m meridian_ops.lb --backends 8081 8082`. Write tools only touch `data/meridian_ops/orders.db` (a copy), never the seed
- Test (offline, no key): `python -m unittest -v`
- Live Break it run: `python scripts/break_it.py --write`

## Rules
- Challenges 1-7: **no agent frameworks**, raw SDKs only: `openai` for Azure GPT (and Groq, `https://api.groq.com/openai/v1`), `anthropic` (`AnthropicFoundry`) for Claude on Azure. Write the loop yourself.
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
