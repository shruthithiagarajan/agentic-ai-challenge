# NOTES - Break it results

## Challenge 1 - One call
Status: code + offline tests done (Groq via the raw `openai` SDK). **Live checks below still to run with a real GROQ_API_KEY, then tag `ch01`.**

### Done-when checklist
- [ ] General question gets a sensible answer (live)
- [ ] Usage footer appears on every call (live)
- [x] Forced failure retries then fails with a clear message (`--simulate 429`, unit-tested)

### Break it (fill in after running live)
1. `desk ask "My name is Ravi"` then `desk ask "What is my name?"` -> result:
2. Long question - seconds staring at a blank terminal:
3. `desk ask "What is Meridian's return window for opened items?"` -> what it invents (truth per policies: 7 days, 20% fee, effective 2026-03-01; older doc says 14 days / 10%):

### Stretch
- `--model` and `--temperature` flags exist. Run the same question 5x at temperature 0 and 1 and compare:

### What fell short (-> Challenge 2)
