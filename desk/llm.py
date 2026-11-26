"""LLM calls (one-shot and streaming), with retry + exponential backoff. Raw SDKs only, no agent frameworks.

Providers (DESK_PROVIDER, see .env):
- azure: Azure AI Foundry. GPT deployments via the `openai` SDK; any model named claude-* via the official
  `anthropic` SDK (AnthropicFoundry), wrapped so the rest of Desk keeps calling `client.chat.completions.create`.
- groq: Groq's OpenAI-compatible API (Challenges 1-5). Kept as a fallback; commented out in .env.
"""
import json
import os
import re
import time
from dataclasses import dataclass
from types import SimpleNamespace

# Groq (Challenges 1-5) - kept, used only when DESK_PROVIDER=groq (or no Azure key is set).
GROQ_BASE_URL = "https://api.groq.com/openai/v1"
DEFAULT_MODEL = "openai/gpt-oss-120b"  # Groq default; on Azure set DESK_MODEL (gpt-6.2-sol / claude-sonnet-5-5)
CLAUDE_MAX_TOKENS = 16000  # Anthropic needs max_tokens; override with DESK_MAX_TOKENS
MAX_ATTEMPTS = 3
BASE_DELAY_S = 1.0  # backoff: 1s, 2s


class LLMError(Exception):
    """A call failed for good: non-retryable error, or retries exhausted."""

    def __init__(self, message, attempts=0):
        super().__init__(message)
        self.attempts = attempts


@dataclass
class Result:
    text: str
    input_tokens: int
    output_tokens: int
    latency_s: float
    attempts: int
    estimated: bool = False  # True when token counts are a chars//4 guess (API sent no usage)


SIMULATED = {"429": "rate limited (429)", "timeout": "request timed out", "network": "could not reach the {api} API (network)"}


def provider():
    """'azure' or 'groq'. DESK_PROVIDER wins; otherwise azure when AZURE_AI_API_KEY is set, else groq."""
    name = (os.getenv("DESK_PROVIDER") or ("azure" if os.getenv("AZURE_AI_API_KEY") else "groq")).strip().lower()
    if name not in ("azure", "groq"):
        raise LLMError(f"DESK_PROVIDER must be 'azure' or 'groq', got {name!r}")
    return name


def is_claude(model):
    return (model or "").strip().lower().startswith("claude")


# One model per job, set in .env (DESK_MODEL_<ROLE>); DESK_MODEL is the fallback for any role left unset.
ROLES = {
    "ONECALL": "Ch1 one plain call (scripts/break_it.py)",
    "CHAT": "Ch2 desk chat",
    "EXTRACT": "Ch3 desk extract",
    "RAG": "Ch4-5 desk ask / eval-rag answers",
    "REWRITE": "Ch5 follow-up query rewriting",
    "JUDGE": "Ch5 eval-rag faithfulness judge",
    "TOOLS": "Ch6 desk tools / eval-tools (tool calling)",
    "AGENT": "Ch7 desk agent (the agent loop)",
}


def model_for(role, override=None):
    """--model (override) > DESK_MODEL_<ROLE> > DESK_MODEL > the Groq default."""
    return override or os.getenv(f"DESK_MODEL_{role}") or os.getenv("DESK_MODEL") or DEFAULT_MODEL


def _api_name():
    try:
        return "Azure AI Foundry" if provider() == "azure" else "Groq"
    except LLMError:
        return "LLM"


def _reason(kind):
    return SIMULATED[kind].format(api=_api_name())


class SimulatedError(Exception):
    """Used by --simulate to exercise the retry path without calling the API."""

    def __init__(self, kind):
        self.kind = kind
        super().__init__(f"simulated: {_reason(kind)}")


def retry_reason(exc):
    """Return a short reason if exc is retryable (rate limit, timeout, network), else None.

    Matched by class name, so the same rule covers the openai and anthropic SDKs' error classes.
    """
    if isinstance(exc, SimulatedError):
        return _reason(exc.kind)
    names = {c.__name__ for c in type(exc).__mro__}
    if "RateLimitError" in names or getattr(exc, "status_code", None) == 429:
        return _reason("429")
    if "APITimeoutError" in names:  # subclass of APIConnectionError: check first
        return _reason("timeout")
    if "APIConnectionError" in names:
        return _reason("network")
    return None


def describe(exc):
    """Clear message for errors we do NOT retry."""
    names = {c.__name__ for c in type(exc).__mro__}
    if "AuthenticationError" in names or "PermissionDeniedError" in names:
        return f"authentication failed - check {'AZURE_AI_API_KEY' if _api_name() == 'Azure AI Foundry' else 'GROQ_API_KEY'}"
    if "NotFoundError" in names:
        return "model not found - check DESK_MODEL / --model"
    if getattr(exc, "status_code", None) == 413:
        return "question too large for this model's token limit - shorten it or use a model with a higher limit"
    return f"{type(exc).__name__}: {exc}"


def call_with_retry(fn, max_attempts=MAX_ATTEMPTS, base_delay=BASE_DELAY_S, sleep=time.sleep, log=lambda m: None):
    """Run fn(); retry rate-limit/timeout errors with exponential backoff. Returns (value, attempts)."""
    for attempt in range(1, max_attempts + 1):
        try:
            return fn(), attempt
        except Exception as exc:  # noqa: BLE001 - classified below
            why = retry_reason(exc)
            if why is None:
                raise LLMError(f"Request failed: {describe(exc)}", attempt) from exc
            if attempt == max_attempts:
                raise LLMError(f"Request failed after {attempt} attempts: {why}. Giving up - try again later.", attempt) from exc
            delay = base_delay * 2 ** (attempt - 1)
            log(f"attempt {attempt}/{max_attempts} failed: {why}; retrying in {delay:g}s")
            sleep(delay)


def _require(name):
    value = os.getenv(name)
    if not value:
        raise LLMError(f"{name} is not set (put it in .env or your environment).")
    return value


def make_client(timeout=30.0, model=None):
    """A client with `.chat.completions.create(**openai_style_kwargs)` for the configured provider and model.

    SDK-level retries are off everywhere (max_retries=0): call_with_retry does the retrying.
    """
    if provider() == "groq":
        api_key = _require("GROQ_API_KEY")
        from openai import OpenAI  # lazy: tests and --simulate work without the SDK installed

        return OpenAI(api_key=api_key, base_url=GROQ_BASE_URL, timeout=timeout, max_retries=0)
    api_key = _require("AZURE_AI_API_KEY")
    if is_claude(model):
        from anthropic import AnthropicFoundry

        return ClaudeChat(AnthropicFoundry(api_key=api_key, base_url=_require("AZURE_ANTHROPIC_BASE_URL"),
                                           timeout=timeout, max_retries=0))
    from openai import OpenAI

    return AzureGPT(OpenAI(api_key=api_key, base_url=_require("AZURE_OPENAI_BASE_URL"), timeout=timeout, max_retries=0))


def _completions(create):
    """The `client.chat.completions.create` attribute path the rest of Desk calls."""
    return SimpleNamespace(completions=SimpleNamespace(create=create))


class AzureGPT:
    """OpenAI SDK client for an Azure GPT deployment.

    Reasoning deployments such as gpt-6.2-sol reject any temperature but the default (400). After the first
    such rejection the request is re-sent without `temperature`, and later requests skip it (the CLI tells
    the user when their --temperature is ignored).
    """

    def __init__(self, client):
        self._client = client
        self.send_temperature = True
        self.chat = _completions(self.create)

    def create(self, **kwargs):
        if kwargs.get("tools"):  # Challenge 6: tool calls on reasoning deployments only work via /v1/responses
            return self._responses(**kwargs)
        if not self.send_temperature:
            kwargs.pop("temperature", None)
        try:
            return self._client.chat.completions.create(**kwargs)
        except Exception as exc:  # noqa: BLE001 - only a temperature rejection is handled here
            if "temperature" not in kwargs or getattr(exc, "status_code", None) != 400 or "temperature" not in str(exc):
                raise
        self.send_temperature = False
        kwargs.pop("temperature")
        return self._client.chat.completions.create(**kwargs)

    def _responses(self, model, messages, tools, tool_choice=None, **_ignored):
        """A chat.completions-style tool request sent through the Responses API, and its reply mapped back.

        Azure rejects function tools with reasoning on /v1/chat/completions for gpt-6.2-sol (and reasoning cannot
        be turned off). Stateless (store=False): every request carries the whole history. The reply's raw output
        items (including encrypted reasoning) ride along on the message as `provider_items`, so the next request
        can hand them back unchanged. temperature is not sent (reasoning models reject it).
        """
        items = []
        for m in messages:
            if m["role"] == "system":
                continue
            if m["role"] == "tool":
                items.append({"type": "function_call_output", "call_id": m["tool_call_id"], "output": m["content"]})
            elif m.get("tool_calls"):
                if m.get("provider_items"):
                    items += m["provider_items"]
                    continue
                if m.get("content"):
                    items.append({"role": "assistant", "content": m["content"]})
                items += [{"type": "function_call", "call_id": c["id"], "name": c["function"]["name"],
                           "arguments": c["function"]["arguments"]} for c in m["tool_calls"]]
            else:
                items.append({"role": m["role"], "content": m["content"]})
        kwargs = dict(model=model, input=items, store=False, include=["reasoning.encrypted_content"],
                      tools=[{"type": "function", "name": t["function"]["name"],
                              "description": t["function"].get("description", ""),
                              "parameters": t["function"]["parameters"]} for t in tools])
        system = "\n\n".join(m["content"] for m in messages if m["role"] == "system")
        if system:
            kwargs["instructions"] = system
        if tool_choice:
            kwargs["tool_choice"] = tool_choice
        r = self._client.responses.create(**kwargs)
        calls = [SimpleNamespace(id=o.call_id, type="function", function=SimpleNamespace(name=o.name, arguments=o.arguments))
                 for o in r.output if o.type == "function_call"]
        raw = [o.model_dump(exclude_none=True) if hasattr(o, "model_dump") else o for o in r.output] if calls else None
        message = SimpleNamespace(content=r.output_text or "", tool_calls=calls or None, provider_items=raw)
        return SimpleNamespace(choices=[SimpleNamespace(message=message, finish_reason="tool_calls" if calls else "stop")],
                               usage=SimpleNamespace(prompt_tokens=r.usage.input_tokens,
                                                     completion_tokens=r.usage.output_tokens))


class ClaudeRefusal(Exception):
    """Claude declined the request (stop_reason 'refusal'). Not retried."""

    def __init__(self, message):
        details = getattr(message, "stop_details", None)
        category = getattr(details, "category", None)
        super().__init__(f"Claude declined the request (refusal{f', category {category}' if category else ''})")


class ClaudeChat:
    """Claude on Azure AI Foundry behind the `client.chat.completions.create(...)` call the rest of Desk uses.

    Every request goes through the official Anthropic SDK (AnthropicFoundry.messages); this class only maps
    the OpenAI-style arguments in (system messages -> `system`, response_format -> a JSON instruction) and the
    reply out (text blocks -> choices[0].message.content, input/output tokens -> usage). `temperature` is not
    sent: the current SDK no longer has the parameter.
    """

    def __init__(self, client, max_tokens=None):
        self._client = client
        self.max_tokens = max_tokens or int(os.getenv("DESK_MAX_TOKENS") or CLAUDE_MAX_TOKENS)
        self.chat = _completions(self.create)

    def create(self, model, messages, temperature=None, stream=False, stream_options=None, response_format=None,
               tools=None, tool_choice=None):
        system = "\n\n".join(m["content"] for m in messages if m["role"] == "system")
        if response_format:
            system = f"{system}\n\n{_json_instruction(response_format)}".strip()
        kwargs = dict(model=model, max_tokens=self.max_tokens, messages=_claude_messages(messages))
        if system:
            kwargs["system"] = system
        if tools:  # Challenge 6: OpenAI function specs -> Anthropic tools
            kwargs["tools"] = [{"name": t["function"]["name"], "description": t["function"].get("description", ""),
                                "input_schema": t["function"]["parameters"]} for t in tools]
            if tool_choice:
                kwargs["tool_choice"] = {"type": {"required": "any"}.get(tool_choice, tool_choice)}
        if stream:
            return self._stream(kwargs)
        msg = self._client.messages.create(**kwargs)
        if msg.stop_reason == "refusal":
            raise ClaudeRefusal(msg)
        text = "".join(b.text for b in msg.content if b.type == "text")
        if response_format:
            text = _strip_fences(text)
        calls = [SimpleNamespace(id=b.id, type="function", function=SimpleNamespace(name=b.name, arguments=json.dumps(b.input)))
                 for b in msg.content if b.type == "tool_use"]
        finish = "tool_calls" if msg.stop_reason == "tool_use" else msg.stop_reason
        return SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content=text, tool_calls=calls or None), finish_reason=finish)],
            usage=SimpleNamespace(prompt_tokens=msg.usage.input_tokens, completion_tokens=msg.usage.output_tokens))

    def _stream(self, kwargs):
        """OpenAI-style chunks: one per text delta, then a final usage-only chunk."""
        with self._client.messages.stream(**kwargs) as stream:
            for piece in stream.text_stream:
                yield SimpleNamespace(choices=[SimpleNamespace(delta=SimpleNamespace(content=piece))], usage=None)
            final = stream.get_final_message()
        if final.stop_reason == "refusal":
            raise ClaudeRefusal(final)
        yield SimpleNamespace(choices=[], usage=SimpleNamespace(prompt_tokens=final.usage.input_tokens,
                                                                completion_tokens=final.usage.output_tokens))


def _claude_messages(messages):
    """OpenAI-style history -> Anthropic messages. An assistant turn's tool_calls become tool_use blocks; each
    `tool` message becomes a tool_result block, and consecutive results share one user message."""
    out = []
    for m in messages:
        if m["role"] == "system":
            continue
        if m["role"] == "tool":
            block = {"type": "tool_result", "tool_use_id": m["tool_call_id"], "content": m["content"]}
            if '"ok": false' in m["content"]:
                block["is_error"] = True
            if out and out[-1]["role"] == "user" and isinstance(out[-1]["content"], list):
                out[-1]["content"].append(block)
            else:
                out.append({"role": "user", "content": [block]})
        elif m.get("tool_calls"):
            blocks = [{"type": "text", "text": m["content"]}] if m.get("content") else []
            blocks += [{"type": "tool_use", "id": c["id"], "name": c["function"]["name"],
                        "input": _json_or_empty(c["function"]["arguments"])} for c in m["tool_calls"]]
            out.append({"role": "assistant", "content": blocks})
        elif m["role"] == "user" and out and out[-1]["role"] == "user" and isinstance(out[-1]["content"], list):
            out[-1]["content"].append({"type": "text", "text": m["content"]})  # text after tool results: same turn
        else:
            out.append({"role": m["role"], "content": m["content"]})
    return out


def _json_or_empty(text):
    try:
        value = json.loads(text or "{}")
    except ValueError:
        return {}
    return value if isinstance(value, dict) else {}


def _json_instruction(response_format):
    text = "Reply with ONE JSON object only - no prose and no code fences."
    if response_format.get("type") == "json_schema":
        text += " It must match this JSON Schema:\n" + json.dumps(response_format["json_schema"]["schema"])
    return text


def _strip_fences(text):
    return re.sub(r"^\s*```(?:json)?\s*|\s*```\s*$", "", text)


def ask(question, system, model, temperature=None, simulate=None, timeout=30.0, client=None, sleep=time.sleep, log=lambda m: None):
    """Send one request; return a Result."""
    if not simulate and client is None:
        client = make_client(timeout, model)

    def once():
        if simulate:
            raise SimulatedError(simulate)
        kwargs = dict(model=model, messages=[{"role": "system", "content": system}, {"role": "user", "content": question}])
        if temperature is not None:
            kwargs["temperature"] = temperature
        return client.chat.completions.create(**kwargs)

    t0 = time.perf_counter()
    resp, attempts = call_with_retry(once, sleep=sleep, log=log)
    latency = time.perf_counter() - t0
    text = (resp.choices[0].message.content or "").strip()
    usage = resp.usage
    return Result(text, usage.prompt_tokens, usage.completion_tokens, latency, attempts)


# ---- Challenge 2: streaming -------------------------------------------------

def estimate_tokens(text):
    """Rough token count (~4 chars per token), used when the API sends no usage."""
    return len(text or "") // 4


def _field(obj, name):
    return obj.get(name) if isinstance(obj, dict) else getattr(obj, name, None)


def chunk_text(chunk):
    """The content piece in a streamed chunk ('' for role-only / usage-only chunks)."""
    choices = _field(chunk, "choices") or []
    delta = _field(choices[0], "delta") if choices else None
    return (_field(delta, "content") if delta is not None else None) or ""


def chunk_usage(chunk):
    """(prompt_tokens, completion_tokens) from chunk.usage or Groq's chunk.x_groq.usage, else None."""
    for usage in (_field(chunk, "usage"), _field(_field(chunk, "x_groq") or {}, "usage")):
        if usage is not None:
            p, c = _field(usage, "prompt_tokens"), _field(usage, "completion_tokens")
            if p is not None and c is not None:
                return p, c
    return None


def stream_chat(messages, model, temperature=None, simulate=None, timeout=30.0, client=None, sleep=time.sleep,
                log=lambda m: None, on_token=lambda t: print(t, end="", flush=True)):
    """Stream one reply for a full message list, calling on_token(piece) as text arrives.

    Retries (rate limit / timeout / network) only until the first token arrives; once text has been
    shown, a failure is final, so the user never sees a reply restart halfway.
    """
    if not simulate and client is None:
        client = make_client(timeout, model)

    def until_first_token():
        if simulate:
            raise SimulatedError(simulate)
        kwargs = dict(model=model, messages=messages, stream=True, stream_options={"include_usage": True})
        if temperature is not None:
            kwargs["temperature"] = temperature
        chunks = iter(client.chat.completions.create(**kwargs))
        head = []
        for chunk in chunks:
            head.append(chunk)
            if chunk_text(chunk):
                break
        return head, chunks

    t0 = time.perf_counter()
    (head, rest), attempts = call_with_retry(until_first_token, sleep=sleep, log=log)
    parts, usage = [], None

    def take(chunk):
        nonlocal usage
        piece = chunk_text(chunk)
        if piece:
            parts.append(piece)
            on_token(piece)
        usage = chunk_usage(chunk) or usage

    for chunk in head:
        take(chunk)
    try:
        for chunk in rest:
            take(chunk)
    except Exception as exc:  # noqa: BLE001 - mid-stream: never retried
        raise LLMError(f"Reply interrupted after it started (not retried): {describe(exc)}", attempts) from exc
    latency = time.perf_counter() - t0
    text = "".join(parts).strip()
    if usage:
        return Result(text, usage[0], usage[1], latency, attempts)
    est_in = sum(estimate_tokens(m["content"]) for m in messages)
    return Result(text, est_in, estimate_tokens(text), latency, attempts, estimated=True)
