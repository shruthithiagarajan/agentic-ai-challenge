# Meridian Supply Co. - synthetic data for the Desk project (15 Agentic AI challenges)

Everything here is fictional. "Today" inside the fictional world is **2026-10-05** (stored in `orders.db.meta`). "Last quarter" = Jul-Sep 2026; all 500 orders fall in it.
Rebuild everything deterministically: `python gen/build_all.py` (fixed random seeds).

## Seed data (needed before Challenge 1)
| Path | What |
|---|---|
| `catalog.csv` | 200 products (sku, name, category, unit_price, stock, specs JSON incl. unit) |
| `orders.db` | SQLite: customers (26), orders (500), order_lines, meta, empty refund_ledger |
| `policies/*.md` | 14 policy docs with front matter, numbered sections/clauses, 5 tables; `returns.md` (2025-01-01, opened = 14 days, 10% fee) CONTRADICTS `returns_opened_items.md` (2026-03-01, opened = 7 days, 20% fee) |
| `emails/email_01..30.txt` | 30 emails (8 order, 7 status, 6 complaint, 6 return, 3 other); messy ones: 02, 04, 05, 07, 08, 12, 13, 15, 16, 17, 21, 23 |

## Ground truth (never show to the agent)
`ground_truth/`: `planted_mismatches.json` (10 pricing mismatches), `planted_policy_violations.json` (10), `apex_delayed_orders.json` (Apex scenario + expected credit %),
`email_labels.json` (15 hand labels) + `email_labels_all30.json`, `description_to_sku_map.json`, `test_orders.json`, `world_constants.json`.

Bulk tiers (per SKU per line): 50-99 3%, 100-249 6%, 250-499 12%, 500+ 15%. Approved hazardous carriers: SafeHaul Logistics, VRL Logistics.

## Per-challenge data
| Challenge | Files |
|---|---|
| C1-C2 | `chat/c01_smoke_tests.json`, `c02_followup_dialogues.json`, `c02_forty_turn_conversation.json` (fact at turn 2), `c02_saved_sessions_fixture.json` |
| C3 | `ground_truth/email_labels*.json`, `description_to_sku_map.json` |
| C4-C5 | `policies/`, `evals/golden_retrieval.jsonl` (40 Qs; section IDs like `returns.md#3` - map to your chunk IDs) |
| C6-C7 | `chat/tool_test_prompts.json`, `chat/c07_agent_tasks.json`, `ground_truth/apex_delayed_orders.json`, `outbox/` |
| C8 | `mcp/servers.json`, `auth_tokens.json`, `tool_scope_map.json`, `delay_apology_prompt.json`, `fake_servers/*.json` (30 stub tools; ~51 total), `thirty_step_task.json` |
| C9 | `memory/` (customer memory seed, yesterday's session, today's prompt, never-store test inputs, budget table template) |
| C10 | `harness/` (permissions.json, AGENTS.md, 2 skills with scripts, hook tests, sandbox scripts, session log sample, repeat-refund runs) |
| C11 | `workflows/` (20 refund requests with expected route/amount, fraud signals, approver decisions, mock payment + critique cases) |
| C12 | `multi_agent/` (10 audit batches, expected findings per batch, report schema, A2A agent card, failure injection, benchmark template, handoff conversations) |
| C13 | `evals/dataset.jsonl` (50 tasks), `judge_calibration_labels.jsonl` (20 human-labelled), `judge_rubric.json`, `failure_taxonomy.json`, `injected/` |
| C14 | `security/redteam.jsonl` (30 attacks), `pii_corpus.json`, `egress_allowlists.json`, `threat_model_inputs.json`, `attack_indicators.json`, `injected/` |
| C15 | `production/` (3 tenants/10 users, per-tenant canary data, 50 load sessions, chaos scenarios, routing config, usage ledger seed, feedback, release artifacts) |

`injected/` holds prompt-injection payloads (policies, emails, order-note SQL, tool outputs, malicious MCP tool description). They are kept OUT of the clean seed so Challenges 1-12 stay unpolluted; apply them for Challenge 13/14.
All emails use `.example` domains, phone numbers are `+91 00000 xxxxx`, card numbers are published test numbers.
