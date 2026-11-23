"""Calls to Groq's OpenAI-compatible API (one-shot and streaming), with retry + exponential backoff.

Raw `openai` SDK only (Challenges 1-7: no agent frameworks).
"""
import os
import time
from dataclasses import dataclass

GROQ_BASE_URL = "https://api.groq.com/openai/v1"
DEFAULT_MODEL = "openai/gpt-oss-120b"  # also: openai/gpt-oss-20b, qwen/qwen3.8-27b (via DESK_MODEL or --model)
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


SIMULATED = {"429": "rate limited (429)", "timeout": "request timed out", "network": "could not reach the Groq API (network)"}


class SimulatedError(Exception):
    """Used by --simulate to exercise the retry path without calling the API."""

    def __init__(self, kind):
        self.kind = kind
        super().__init__(f"simulated: {SIMULATED[kind]}")


def retry_reason(exc):
    """Return a short reason if exc is retryable (rate limit, timeout, network), else None."""
    if isinstance(exc, SimulatedError):
        return SIMULATED[exc.kind]
    names = {c.__name__ for c in type(exc).__mro__}
    if "RateLimitError" in names or getattr(exc, "status_code", None) == 429:
        return SIMULATED["429"]
    if "APITimeoutError" in names:  # subclass of APIConnectionError: check first
        return SIMULATED["timeout"]
    if "APIConnectionError" in names:
        return SIMULATED["network"]
    return None


def describe(exc):
    """Clear message for errors we do NOT retry."""
    names = {c.__name__ for c in type(exc).__mro__}
    if "AuthenticationError" in names or "PermissionDeniedError" in names:
        return "authentication failed - check GROQ_API_KEY"
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


def make_client(timeout=30.0):
    api_key = os.getenv("GROQ_API_KEY")
    if not api_key:
        raise LLMError("GROQ_API_KEY is not set (put it in .env or your environment).")
    from openai import OpenAI  # lazy: tests and --simulate work without the SDK installed

    return OpenAI(api_key=api_key, base_url=GROQ_BASE_URL, timeout=timeout, max_retries=0)  # our loop does the retrying


def ask(question, system, model, temperature=None, simulate=None, timeout=30.0, client=None, sleep=time.sleep, log=lambda m: None):
    """Send one request; return a Result."""
    if not simulate and client is None:
        client = make_client(timeout)

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
        client = make_client(timeout)

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
