"""Challenge 7: the agent loop, written from scratch (no framework).

    messages = [system, task]
    loop:
        check the limits (steps, tokens / cost, wall clock)
        reply = model(messages, tools)
        no tool calls?  -> the model says it is finished: stop ("done")
        otherwise run every call, append each result, go again

Stop conditions: max steps (model turns), a token budget, a cost budget (only when DESK_PRICE_IN / DESK_PRICE_OUT
are set), a wall-clock timeout, and the same call with the same arguments made `max_repeats` times. Every tool call
is a trace event (step, tool, arguments, result size, tokens), printed live and saved as JSONL, and a run that stops
early reports what it managed from that trace - without spending another LLM call.
"""
import json
import os
import time
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from .llm import call_with_retry
from .prompts import AGENT_SYSTEM_PROMPT, PLAN_RULE
from .tooluse import ToolCall, log_path, write_log

TRACE_DIR = Path("data") / "agent_traces"
RESULT_PREVIEW = 500  # chars of each tool result kept in the trace
STOP_REASONS = {
    "done": "the model finished the task",
    "max_steps": "step limit reached",
    "max_tokens": "token budget used up",
    "max_cost": "cost budget used up",
    "timeout": "wall-clock time limit reached",
    "repeated_call": "the same call was repeated with the same arguments",
}


@dataclass
class Budget:
    """Defaults match meridian_desk_data/chat/c07_agent_tasks.json (25 steps, $0.50, 300 s)."""
    max_steps: int = 25  # model turns
    max_tokens: int = 500_000  # input + output, all turns
    max_cost_usd: float = 0.50  # enforced only when prices are known
    wall_clock_s: float = 300.0
    max_repeats: int = 3  # the 3rd identical call (same tool, same arguments) stops the run
    max_calls_per_turn: int = 20


@dataclass
class AgentRun:
    task: str
    model: str
    answer: str = ""
    stop_reason: str = ""
    stop_detail: str = ""
    steps: int = 0  # model turns
    llm_calls: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    cost_usd: float = None  # None when prices are not configured
    latency_s: float = 0.0
    attempts: int = 1
    calls: list = field(default_factory=list)  # ToolCall, in order
    events: list = field(default_factory=list)  # the trace
    pending: list = field(default_factory=list)  # (tool, arguments) the model asked for but the run stopped first
    notes: list = field(default_factory=list)  # text the model wrote alongside its tool calls
    drafts: list = field(default_factory=list)
    plan: list = field(default_factory=list)
    trace_file: str = ""
    estimated: bool = False

    @property
    def finished(self):
        return self.stop_reason == "done"

    @property
    def tool_calls(self):
        return len(self.calls)


def _cost(tin, tout, pricing):
    if not pricing:
        return None
    return (tin * pricing[0] + tout * pricing[1]) / 1_000_000


def _key(name, arguments):
    """Canonical (tool, arguments) so '{"sku":"A"}' and '{ "sku": "A" }' count as the same call."""
    try:
        args = json.dumps(json.loads(arguments or "{}"), sort_keys=True)
    except ValueError:
        args = (arguments or "").strip()
    return name, args


def run_agent(task, model, client, toolbox, budget=None, plan=False, pricing=None, today=None,
              clock=time.monotonic, sleep=time.sleep, log=lambda m: None, on_event=lambda e: None,
              simulate_error=None, tool_log="default", trace_dir="default"):
    """Run the loop until the model finishes or a limit stops it. Returns an AgentRun (never raises on a limit;
    LLMError still propagates if the API itself fails)."""
    budget = budget or Budget()
    tools = toolbox.specs(plan)  # Ch8: whatever the toolbox offers - local (Ch7) or discovered from MCP servers
    system = AGENT_SYSTEM_PROMPT.format(today=today or toolbox.today(), plan_rule=PLAN_RULE if plan else "")
    if toolbox.instructions():
        system += "\n\nConnected tool servers:\n" + toolbox.instructions()
    messages = [{"role": "system", "content": system}, {"role": "user", "content": task}]
    run = AgentRun(task, model)
    log_file = log_path() if tool_log == "default" else tool_log
    trace = None
    if trace_dir == "default":  # DESK_TRACE_DIR overrides data/agent_traces; "off" disables the trace file
        trace_dir = os.getenv("DESK_TRACE_DIR") or TRACE_DIR
        trace_dir = None if str(trace_dir).strip().lower() == "off" else trace_dir
    if trace_dir:
        Path(trace_dir).mkdir(parents=True, exist_ok=True)
        trace = Path(trace_dir) / f"{datetime.now().strftime('%Y%m%d-%H%M%S')}_{model.replace('/', '-')}.jsonl"
        run.trace_file = str(trace)
    seen = Counter()
    start = clock()

    def emit(event):
        event = {"elapsed_s": round(clock() - start, 2), **event}
        run.events.append(event)
        if trace:
            with open(trace, "a", encoding="utf-8") as f:
                f.write(json.dumps(event, ensure_ascii=False) + "\n")
        on_event(event)

    def stop(reason, detail=""):
        run.stop_reason, run.stop_detail = reason, detail
        run.latency_s = clock() - start
        run.drafts, run.plan = list(toolbox.drafts), list(toolbox.plan)
        if not run.finished:
            run.answer = early_report(run)
        emit({"event": "stop", "reason": reason, "detail": detail, "steps": run.steps, "tool_calls": run.tool_calls,
              "input_tokens": run.input_tokens, "output_tokens": run.output_tokens, "cost_usd": run.cost_usd})
        return run

    def over_budget():
        if run.input_tokens + run.output_tokens > budget.max_tokens:
            return "max_tokens", f"{run.input_tokens + run.output_tokens:,} tokens > {budget.max_tokens:,}"
        if run.cost_usd is not None and run.cost_usd > budget.max_cost_usd:
            return "max_cost", f"${run.cost_usd:.4f} > ${budget.max_cost_usd:.2f}"
        return None

    emit({"event": "start", "task": task, "model": model, "plan": plan, "tools": len(tools),
          "tool_definition_tokens": len(json.dumps(tools)) // 4,
          "budget": {"max_steps": budget.max_steps, "max_tokens": budget.max_tokens,
                     "max_cost_usd": budget.max_cost_usd if pricing else None, "wall_clock_s": budget.wall_clock_s}})
    while True:
        if run.steps >= budget.max_steps:
            return stop("max_steps", f"{run.steps} steps")
        if clock() - start >= budget.wall_clock_s:
            return stop("timeout", f"{clock() - start:.0f}s >= {budget.wall_clock_s:g}s")

        def once():
            if simulate_error:
                raise simulate_error
            return client.chat.completions.create(model=model, messages=messages, tools=tools, tool_choice="auto")

        resp, attempts = call_with_retry(once, sleep=sleep, log=log)
        run.steps += 1
        run.llm_calls += 1
        run.attempts = max(run.attempts, attempts)
        tin = (resp.usage.prompt_tokens or 0) if resp.usage else 0
        tout = (resp.usage.completion_tokens or 0) if resp.usage else 0
        run.input_tokens += tin
        run.output_tokens += tout
        run.cost_usd = _cost(run.input_tokens, run.output_tokens, pricing)
        message = resp.choices[0].message
        calls = list(getattr(message, "tool_calls", None) or [])
        text = (message.content or "").strip()
        emit({"event": "turn", "step": run.steps, "tool_calls": len(calls), "turn_input_tokens": tin,
              "turn_output_tokens": tout, "total_tokens": run.input_tokens + run.output_tokens,
              "cost_usd": run.cost_usd, "text": text[:300]})

        if not calls:  # the model says it is finished
            run.answer = text
            return stop("done")
        if text:
            run.notes.append(text)
        limit = over_budget()
        if limit:
            run.pending = [(c.function.name, c.function.arguments) for c in calls]
            return stop(*limit)

        turn = {"role": "assistant", "content": message.content or "", "tool_calls": [
            {"id": c.id, "type": "function", "function": {"name": c.function.name, "arguments": c.function.arguments}}
            for c in calls]}
        if getattr(message, "provider_items", None):  # Azure Responses API: hand raw items back unchanged
            turn["provider_items"] = message.provider_items
        messages.append(turn)

        repeated = None
        for i, c in enumerate(calls):
            name, arguments = c.function.name, c.function.arguments
            key = _key(name, arguments)
            seen[key] += 1
            t0 = time.perf_counter()
            if seen[key] >= budget.max_repeats:
                result = {"ok": False, "error": {"code": "repeated_call", "message":
                          f"{name} was already called {seen[key] - 1} times with these arguments; the result will not change"}}
                repeated = repeated or f"{name}({arguments}) x{seen[key]}"
            elif i >= budget.max_calls_per_turn:
                result = {"ok": False, "error": {"code": "too_many_calls",
                                                 "message": f"only {budget.max_calls_per_turn} tool calls run per turn"}}
            else:
                result = toolbox.run(name, arguments)
            call = ToolCall(c.id, name, arguments, result, (time.perf_counter() - t0) * 1000)
            run.calls.append(call)
            write_log(call, model, task, log_file)
            content = json.dumps(result, ensure_ascii=False)
            emit({"event": "tool", "step": run.steps, "tool": name, "arguments": arguments, "ok": call.ok,
                  "error": None if call.ok else result["error"]["code"], "result_chars": len(content),
                  "ms": round(call.ms, 1), "total_tokens": run.input_tokens + run.output_tokens,
                  "result_preview": content[:RESULT_PREVIEW]})
            messages.append({"role": "tool", "tool_call_id": c.id, "content": content})
        if repeated:
            return stop("repeated_call", repeated)


def early_report(run):
    """What a stopped run managed, built from the trace (no extra LLM call, so no extra spend)."""
    done = Counter(c.name for c in run.calls if c.ok)
    failed = Counter(f"{c.name} [{c.result.get('error', {}).get('code')}]" for c in run.calls if not c.ok)
    lines = [f"STOPPED before finishing: {STOP_REASONS.get(run.stop_reason, run.stop_reason)}"
             + (f" ({run.stop_detail})" if run.stop_detail else "") + "."]
    lines.append(f"Managed in {run.steps} step(s): " +
                 (", ".join(f"{name} x{n}" for name, n in done.items()) or "no successful tool calls") + ".")
    if failed:
        lines.append("Failed calls: " + ", ".join(f"{name} x{n}" for name, n in failed.items()) + ".")
    orders = sorted({json.loads(c.arguments).get("order_id") for c in run.calls
                     if c.ok and c.name == "get_order"} - {None})
    listed = [o["order_id"] for c in run.calls if c.ok and c.name == "list_orders" for o in c.result["result"]["orders"]]
    if listed or orders:
        lines.append("Orders looked at: " + ", ".join(dict.fromkeys(listed + orders)) + ".")
    if run.drafts:
        lines.append(f"Drafts saved ({len(run.drafts)}, not sent): " + ", ".join(run.drafts) + ".")
    else:
        lines.append("No drafts were saved.")
    if run.plan:
        lines.append("Plan: " + "; ".join(f"[{i['status']}] {i['step']}" for i in run.plan) + ".")
    if run.pending:
        lines.append("Next calls the model wanted (not run): " +
                     ", ".join(f"{name}({args})" for name, args in run.pending) + ".")
    if run.notes:
        lines.append(f"Model's last note: {run.notes[-1][:400]}")
    return "\n".join(lines)


def format_event(e):
    """One readable trace line per event (printed to stderr by desk agent)."""
    if e["event"] == "start":
        b = e["budget"]
        cost = f"${b['max_cost_usd']:.2f}" if b["max_cost_usd"] is not None else "n/a (no prices set)"
        return (f"[agent] model {e['model']} | budget: {b['max_steps']} steps, {b['max_tokens']:,} tokens, cost {cost}, "
                f"{b['wall_clock_s']:g}s" + (" | plan-first (todo tool)" if e["plan"] else ""))
    if e["event"] == "turn":
        cost = f" | ${e['cost_usd']:.4f}" if e.get("cost_usd") is not None else ""
        what = f"{e['tool_calls']} tool call(s)" if e["tool_calls"] else "final answer"
        return (f"[step {e['step']:>2}] {e['elapsed_s']:>6.1f}s  model: {e['turn_input_tokens']:,} in / "
                f"{e['turn_output_tokens']:,} out (total {e['total_tokens']:,}{cost}) -> {what}")
    if e["event"] == "tool":
        status = "ok" if e["ok"] else f"ERROR {e['error']}"
        args = e["arguments"] if len(e["arguments"]) <= 140 else e["arguments"][:137] + "..."
        return f"          {e['tool']}({args}) -> {status}, {e['result_chars']:,} chars, {e['ms']:.0f} ms"
    if e["event"] == "stop":
        return (f"[agent] stop: {e['reason']}" + (f" ({e['detail']})" if e["detail"] else "") +
                f" after {e['steps']} step(s), {e['tool_calls']} tool call(s), "
                f"{e['input_tokens'] + e['output_tokens']:,} tokens")
    return json.dumps(e)
