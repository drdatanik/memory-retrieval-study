"""The only module that talks to the model API (OpenRouter, OpenAI-compatible chat completions).

Everything else passes Anthropic-style pieces (system text blocks, tool dicts with input_schema)
and gets back an LLMResponse with normalised usage, so the rest of the pipeline is API-agnostic.
"""

from __future__ import annotations

import json
import os
import re
import threading
import time
from collections import deque
from dataclasses import dataclass, field

from src import config


class LLMError(RuntimeError):
    """The API returned no usable choice (provider error, empty response)."""


class DailyLimitReached(LLMError):
    """OpenRouter's per-day request cap for free models was hit. Stop and resume tomorrow; don't record a result."""


_DAILY_RE = re.compile(r"per[-_ ]?day", re.IGNORECASE)
_sleep = time.sleep   # replaced in tests


class RateLimiter:
    """At most `per_minute` request starts in any 60-second window, shared by all threads of one process."""

    def __init__(self, per_minute: int, clock=time.monotonic, sleep=None):
        self.per_minute = per_minute
        self._clock = clock
        self._sleep = sleep
        self._starts: deque = deque()
        self._lock = threading.Lock()

    def acquire(self) -> None:
        while True:
            with self._lock:
                now = self._clock()
                while self._starts and now - self._starts[0] >= 60:
                    self._starts.popleft()
                if len(self._starts) < self.per_minute:
                    self._starts.append(now)
                    return
                wait = 60 - (now - self._starts[0])
            (self._sleep or _sleep)(max(wait, 0.05))


@dataclass
class LLMResponse:
    text: str
    tool_calls: list[dict]          # [{"id", "name", "arguments": dict | None, "raw_arguments": str, "parse_error": str | None}]
    finish_reason: str              # stop | tool_calls | length | content_filter | error | ...
    usage: dict                     # prompt_tokens, cached_tokens, cache_write_tokens, completion_tokens, reasoning_tokens, cost_usd
    assistant_message: dict         # append to `messages` unchanged to continue the conversation
    provider: str | None = None
    model: str | None = None
    request_id: str | None = None
    raw: dict = field(default_factory=dict, repr=False)


def make_client():
    from openai import OpenAI

    key = os.environ.get(config.OPENROUTER_KEY_ENV)
    if not key:
        raise LLMError(f"{config.OPENROUTER_KEY_ENV} is not set")
    # Retries are done in chat(), not by the SDK, so a daily-limit 429 is never retried.
    client = OpenAI(base_url=config.OPENROUTER_BASE_URL, api_key=key,
                    timeout=config.REQUEST_TIMEOUT_S, max_retries=0)
    if config.REQUESTS_PER_MINUTE:
        client._study_limiter = RateLimiter(config.REQUESTS_PER_MINUTE)
    return client


def key_info() -> dict:
    """OpenRouter's account/key status (GET /key): limits, usage, is_free_tier. Never contains the key itself."""
    import httpx

    key = os.environ.get(config.OPENROUTER_KEY_ENV)
    if not key:
        raise LLMError(f"{config.OPENROUTER_KEY_ENV} is not set")
    r = httpx.get(f"{config.OPENROUTER_BASE_URL}/key", headers={"Authorization": f"Bearer {key}"}, timeout=30)
    r.raise_for_status()
    data = dict(r.json().get("data") or {})
    data.pop("label", None)   # the label is a masked form of the key; keep it out of logs
    return data


def system_text(blocks) -> str:
    """Anthropic-style system blocks (or a plain string) -> one system string."""
    if isinstance(blocks, str):
        return blocks
    return "".join(b["text"] for b in blocks)


def to_openai_tools(tools: list[dict] | None) -> list[dict] | None:
    if not tools:
        return None
    return [{"type": "function",
             "function": {"name": t["name"], "description": t["description"], "parameters": t["input_schema"]}}
            for t in tools]


def json_schema_format(name: str, schema: dict) -> dict:
    return {"type": "json_schema", "json_schema": {"name": name, "strict": True, "schema": schema}}


def _i(value) -> int:
    return int(value or 0)


def _normalise_usage(usage: dict | None) -> dict:
    usage = usage or {}
    prompt_details = usage.get("prompt_tokens_details") or {}
    completion_details = usage.get("completion_tokens_details") or {}
    cost = usage.get("cost")
    return {
        "prompt_tokens": _i(usage.get("prompt_tokens")),
        "cached_tokens": _i(prompt_details.get("cached_tokens")),
        "cache_write_tokens": _i(prompt_details.get("cache_write_tokens")),
        "completion_tokens": _i(usage.get("completion_tokens")),
        "reasoning_tokens": _i(completion_details.get("reasoning_tokens")),
        "cost_usd": float(cost) if cost is not None else None,
    }


def _parse_tool_calls(raw_calls: list[dict] | None) -> list[dict]:
    calls = []
    for c in raw_calls or []:
        fn = c.get("function") or {}
        raw_args = fn.get("arguments") or "{}"
        try:
            args, err = json.loads(raw_args), None
            if not isinstance(args, dict):
                args, err = None, "arguments are not a JSON object"
        except json.JSONDecodeError as e:
            args, err = None, f"invalid JSON arguments: {e}"
        calls.append({"id": c.get("id"), "name": fn.get("name"), "arguments": args,
                      "raw_arguments": raw_args, "parse_error": err})
    return calls


def _error_kind(status, text: str) -> str:
    """'daily' (free-model daily cap), 'transient' (worth retrying) or 'fatal'."""
    if status == 429 and _DAILY_RE.search(text or ""):
        return "daily"
    if status in (408, 409, 429) or (isinstance(status, int) and status >= 500):
        return "transient"
    return "fatal"


def _exception_kind(exc: Exception) -> str:
    import openai

    if isinstance(exc, (openai.APITimeoutError, openai.APIConnectionError)):
        return "transient"
    if isinstance(exc, openai.APIStatusError):
        return _error_kind(exc.status_code, f"{exc} {exc.body}")
    return "fatal"


def _backoff_s(attempt: int, exc: Exception | None = None) -> float:
    response = getattr(exc, "response", None)
    retry_after = response.headers.get("retry-after") if response is not None else None
    try:
        if retry_after is not None:
            return min(120.0, max(1.0, float(retry_after)))
    except ValueError:
        pass
    return float(min(60, 5 * 2 ** attempt))


def _create(client, kwargs: dict, limited: bool = True) -> dict:
    """One completion with retries. Returns the raw response dict with at least one choice.
    `limited` applies the client's per-minute limiter (free models only)."""
    limiter = getattr(client, "_study_limiter", None) if limited else None
    for attempt in range(config.MAX_RETRIES + 1):
        last = attempt == config.MAX_RETRIES
        if limiter is not None:
            limiter.acquire()
        try:
            resp = client.chat.completions.create(**kwargs)
        except Exception as exc:
            kind = _exception_kind(exc)
            if kind == "daily":
                raise DailyLimitReached(str(exc)) from exc
            if kind == "transient" and not last:
                _sleep(_backoff_s(attempt, exc))
                continue
            raise
        raw = resp.to_dict() if hasattr(resp, "to_dict") else dict(resp)
        if raw.get("choices"):
            return raw
        err = raw.get("error") or {}
        kind = _error_kind(err.get("code"), str(err.get("message") or ""))
        if kind == "daily":
            raise DailyLimitReached(str(err.get("message")))
        if kind == "transient" and not last:
            _sleep(_backoff_s(attempt))
            continue
        raise LLMError(f"no choices in response: {raw.get('error') or raw}")
    raise LLMError("retries exhausted")  # not reached


def chat(client, role: str, messages: list[dict], tools: list[dict] | None = None,
         response_format: dict | None = None, max_tokens: int | None = None) -> LLMResponse:
    """One chat-completions call for `role` (a key of config.ROLES).

    Requests to free models wait for the client's per-minute limiter; paid models don't.
    Transient failures (timeouts, connection errors, 408/409/429/5xx, empty responses carrying such a code)
    are retried up to config.MAX_RETRIES times with backoff. Raises DailyLimitReached at once on the free-model
    daily cap, LLMError on an empty response that is not transient, and re-raises other SDK errors.
    """
    cfg = config.ROLES[role]
    kwargs = {
        "model": cfg["model"],
        "messages": messages,
        "max_tokens": max_tokens or cfg["max_tokens"],
        "temperature": cfg["temperature"],
        "extra_body": {"provider": cfg["provider"], "reasoning": cfg["reasoning"]},
    }
    oa_tools = to_openai_tools(tools)
    if oa_tools:
        kwargs["tools"] = oa_tools
    if response_format:
        kwargs["response_format"] = response_format

    raw = _create(client, kwargs, limited=config.is_free(role))
    choice = raw["choices"][0]
    msg = choice.get("message") or {}

    assistant = {"role": "assistant", "content": msg.get("content") or ""}
    if msg.get("tool_calls"):
        assistant["tool_calls"] = msg["tool_calls"]
    if msg.get("reasoning_details"):
        assistant["reasoning_details"] = msg["reasoning_details"]   # pass back unmodified (OpenRouter docs)

    return LLMResponse(
        text=msg.get("content") or "",
        tool_calls=_parse_tool_calls(msg.get("tool_calls")),
        finish_reason=choice.get("finish_reason") or "error",
        usage=_normalise_usage(raw.get("usage")),
        assistant_message=assistant,
        provider=raw.get("provider"),
        model=raw.get("model"),
        request_id=raw.get("id"),
        raw=raw,
    )
