"""One chat-completion call against Groq's OpenAI-compatible API, with retry + exponential backoff.

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


class SimulatedError(Exception):
    """Used by --simulate to exercise the retry path without calling the API."""

    def __init__(self, kind):
        self.kind = kind
        super().__init__("simulated rate limit (429)" if kind == "429" else "simulated timeout")


def retry_reason(exc):
    """Return a short reason if exc is retryable (RateLimitError / APITimeoutError), else None."""
    if isinstance(exc, SimulatedError):
        return "rate limited (429)" if exc.kind == "429" else "request timed out"
    names = {c.__name__ for c in type(exc).__mro__}
    if "RateLimitError" in names or getattr(exc, "status_code", None) == 429:
        return "rate limited (429)"
    if "APITimeoutError" in names:
        return "request timed out"
    return None


def describe(exc):
    """Clear message for errors we do NOT retry."""
    names = {c.__name__ for c in type(exc).__mro__}
    if "AuthenticationError" in names or "PermissionDeniedError" in names:
        return "authentication failed - check GROQ_API_KEY"
    if "NotFoundError" in names:
        return "model not found - check DESK_MODEL / --model"
    if "APIConnectionError" in names:
        return "could not reach the Groq API - check your network"
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
