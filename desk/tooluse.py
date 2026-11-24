"""Challenge 6: ONE round of tool calls, written by hand (no agent framework).

1. Send the question with the tool list. 2. If the model asks for tools - one or several at once - validate and
run every call (Toolbox.run never raises) and send each result back as a `tool` message. 3. Ask again for the
final answer. If the model wants a SECOND round, Desk does not run it (that needs a loop: Challenge 7); it
records what was wanted and makes the model answer with what it has.
"""
import json
import os
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from .llm import call_with_retry
from .prompts import TOOLS_SYSTEM_PROMPT
from .tools import timed_run, tool_specs

MAX_CALLS_PER_ROUND = 8  # more than this in one reply are answered with an error instead of being run
DEFAULT_LOG = Path("data") / "tool_calls.jsonl"
# Sent when the model wants a second round. Without it Claude ends the turn silently under tool_choice "none".
NO_MORE_TOOLS = ("No more tools can be run for this request. Answer now using only the tool results above, and say "
                 "clearly which checks you could not do.")


@dataclass
class ToolCall:
    id: str
    name: str
    arguments: str  # raw JSON text, as the model sent it
    result: dict
    ms: float

    @property
    def ok(self):
        return self.result.get("ok", False)


@dataclass
class ToolRun:
    text: str
    input_tokens: int = 0
    output_tokens: int = 0
    latency_s: float = 0.0
    attempts: int = 1
    llm_calls: int = 0
    calls: list = field(default_factory=list)  # ToolCall, in the order they ran
    wanted: list = field(default_factory=list)  # (name, arguments) the model asked for in a 2nd round - not run
    estimated: bool = False


def log_path():
    """JSONL log of every tool call: DESK_TOOL_LOG, default data/tool_calls.jsonl; DESK_TOOL_LOG=off disables it."""
    value = os.getenv("DESK_TOOL_LOG")
    if value and value.strip().lower() in ("off", "0", "none"):
        return None
    return Path(value) if value else DEFAULT_LOG


def write_log(call, model, question, path):
    if path is None:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    record = {"ts": datetime.now(timezone.utc).isoformat(timespec="seconds"), "model": model,
              "question": question[:200], "tool": call.name, "arguments": call.arguments, "ok": call.ok,
              "error": None if call.ok else call.result.get("error", {}).get("code"), "ms": round(call.ms, 1),
              "result_chars": len(json.dumps(call.result))}
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(record) + "\n")


def _tool_calls(message):
    return list(getattr(message, "tool_calls", None) or [])


def answer(question, model, client, toolbox, today=None, vague=False, sleep=time.sleep, log=lambda m: None,
           on_call=lambda c: None, simulate_error=None, log_file="default"):
    """Answer `question` with at most one round of tool calls. Returns a ToolRun."""
    tools = tool_specs(vague)
    path = log_path() if log_file == "default" else log_file
    messages = [{"role": "system", "content": TOOLS_SYSTEM_PROMPT.format(today=today or toolbox.today())},
                {"role": "user", "content": question}]
    run = ToolRun(text="")
    t0 = time.perf_counter()

    def llm(tool_choice):
        def once():
            if simulate_error:
                raise simulate_error
            return client.chat.completions.create(model=model, messages=messages, tools=tools, tool_choice=tool_choice)

        resp, attempts = call_with_retry(once, sleep=sleep, log=log)
        run.llm_calls += 1
        run.attempts = max(run.attempts, attempts)
        if resp.usage:
            run.input_tokens += resp.usage.prompt_tokens or 0
            run.output_tokens += resp.usage.completion_tokens or 0
        return resp.choices[0].message

    first = llm("auto")
    requested = _tool_calls(first)
    if not requested:  # answered directly (e.g. out of scope)
        run.text = (first.content or "").strip()
        run.latency_s = time.perf_counter() - t0
        return run

    turn = {"role": "assistant", "content": first.content or "", "tool_calls": [
        {"id": c.id, "type": "function", "function": {"name": c.function.name, "arguments": c.function.arguments}}
        for c in requested]}
    if getattr(first, "provider_items", None):  # Azure Responses API: hand its raw items (reasoning) back unchanged
        turn["provider_items"] = first.provider_items
    messages.append(turn)
    for i, c in enumerate(requested):
        if i < MAX_CALLS_PER_ROUND:
            result, ms = timed_run(toolbox, c.function.name, c.function.arguments)
        else:
            result, ms = {"ok": False, "error": {"code": "too_many_calls",
                                                 "message": f"only {MAX_CALLS_PER_ROUND} tool calls run per turn"}}, 0.0
        call = ToolCall(c.id, c.function.name, c.function.arguments, result, ms)
        run.calls.append(call)
        write_log(call, model, question, path)
        on_call(call)
        messages.append({"role": "tool", "tool_call_id": c.id, "content": json.dumps(result, ensure_ascii=False)})

    second = llm("auto")
    more = _tool_calls(second)
    if more:  # the task needs another round; Challenge 6 stops here on purpose
        run.wanted = [(c.function.name, c.function.arguments) for c in more]
        messages.append({"role": "user", "content": NO_MORE_TOOLS})
        second = llm("none")
    run.text = (second.content or "").strip()
    run.latency_s = time.perf_counter() - t0
    return run


# ---- stretch: tool-selection check on chat/tool_test_prompts.json --------------

def selected_tools(run):
    """Every tool the model chose: the round that ran plus any second round it asked for."""
    return [c.name for c in run.calls] + [name for name, _ in run.wanted]


def score_selection(expected, chosen):
    """(passed, matched, extra): passed when every expected tool (with repeats) was chosen and, for prompts that
    expect no tool, none was."""
    left = list(chosen)
    matched = 0
    for name in expected:
        if name in left:
            left.remove(name)
            matched += 1
    passed = matched == len(expected) and (bool(expected) or not chosen)
    return passed, matched, len(left)
