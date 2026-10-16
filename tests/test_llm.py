import json

import pytest

from src import config, llm
from tests.fake_openrouter import FakeOpenRouter, text_resp, tool_resp, usage

TOOLS = [{"name": "search_memory", "description": "d", "input_schema": {"type": "object", "properties": {"query": {"type": "string"}}, "required": ["query"], "additionalProperties": False}}]


def test_request_shape_pins_provider_and_reasoning():
    fake = FakeOpenRouter([text_resp("hi")])
    llm.chat(fake, "subject", [{"role": "user", "content": "q"}], tools=TOOLS)
    kw = fake.calls[0]
    cfg = config.ROLES["subject"]
    assert kw["model"] == cfg["model"]
    assert kw["temperature"] == cfg["temperature"]
    assert kw["max_tokens"] == cfg["max_tokens"]
    assert kw["extra_body"] == {"provider": cfg["provider"], "reasoning": cfg["reasoning"]}
    assert kw["tools"] == [{"type": "function", "function": {"name": "search_memory", "description": "d", "parameters": TOOLS[0]["input_schema"]}}]
    assert "response_format" not in kw


def test_no_tools_key_when_no_tools():
    fake = FakeOpenRouter([text_resp("hi")])
    llm.chat(fake, "judge", [{"role": "user", "content": "q"}], response_format=llm.json_schema_format("j", {"type": "object"}))
    kw = fake.calls[0]
    assert "tools" not in kw
    assert kw["response_format"]["type"] == "json_schema"


def test_usage_normalised_and_cost_kept():
    fake = FakeOpenRouter([text_resp("hi", usage(prompt=50_000, completion=300, cached=48_000, reasoning=120, cost=0.0123))])
    r = llm.chat(fake, "subject", [{"role": "user", "content": "q"}])
    assert r.usage == {"prompt_tokens": 50_000, "cached_tokens": 48_000, "cache_write_tokens": 0,
                       "completion_tokens": 300, "reasoning_tokens": 120, "cost_usd": 0.0123}
    assert r.text == "hi" and r.finish_reason == "stop" and r.provider == "DeepSeek"


def test_tool_calls_parsed_and_assistant_message_keeps_reasoning():
    fake = FakeOpenRouter([tool_resp([("c1", "search_memory", {"query": "sugar"}), ("c2", "search_memory", "{bad json")])])
    r = llm.chat(fake, "subject", [{"role": "user", "content": "q"}], tools=TOOLS)
    assert r.finish_reason == "tool_calls"
    assert r.tool_calls[0] == {"id": "c1", "name": "search_memory", "arguments": {"query": "sugar"},
                               "raw_arguments": json.dumps({"query": "sugar"}), "parse_error": None}
    assert r.tool_calls[1]["arguments"] is None and "invalid JSON" in r.tool_calls[1]["parse_error"]
    assert r.assistant_message["tool_calls"][0]["id"] == "c1"
    assert r.assistant_message["reasoning_details"]


def test_empty_choices_raise():
    fake = FakeOpenRouter([{"id": "x", "choices": [], "error": {"message": "provider down"}}])
    with pytest.raises(llm.LLMError, match="provider down"):
        llm.chat(fake, "subject", [{"role": "user", "content": "q"}])


def test_system_text_joins_blocks():
    assert llm.system_text([{"type": "text", "text": "a"}, {"type": "text", "text": "b", "cache_control": {}}]) == "ab"
    assert llm.system_text("plain") == "plain"


# --- retries, daily limit, rate limiter ------------------------------------------------------

import httpx2
import openai


def _status_error(cls, status: int, message: str, headers: dict | None = None):
    response = httpx2.Response(status, headers=headers or {},
                               request=httpx2.Request("POST", "https://openrouter.ai/api/v1/chat/completions"))
    return cls(message, response=response, body={"error": {"message": message, "code": status}})


@pytest.fixture
def sleeps(monkeypatch):
    slept = []
    monkeypatch.setattr(llm, "_sleep", slept.append)
    return slept


def test_daily_limit_raises_at_once_without_retry(sleeps):
    fake = FakeOpenRouter([_status_error(openai.RateLimitError, 429, "Rate limit exceeded: free-models-per-day"),
                           text_resp("never reached")])
    with pytest.raises(llm.DailyLimitReached):
        llm.chat(fake, "subject", [{"role": "user", "content": "q"}])
    assert len(fake.calls) == 1 and sleeps == []


def test_daily_limit_in_empty_choices_body(sleeps):
    fake = FakeOpenRouter([{"id": "x", "choices": [], "error": {"message": "free-models-per-day exceeded", "code": 429}}])
    with pytest.raises(llm.DailyLimitReached):
        llm.chat(fake, "subject", [{"role": "user", "content": "q"}])


def test_per_minute_429_and_5xx_are_retried(sleeps):
    fake = FakeOpenRouter([
        _status_error(openai.RateLimitError, 429, "Rate limit exceeded: free-models-per-min", {"retry-after": "7"}),
        _status_error(openai.InternalServerError, 502, "upstream error"),
        {"id": "x", "choices": [], "error": {"message": "rate-limited upstream", "code": 429}},
        text_resp("ok"),
    ])
    r = llm.chat(fake, "subject", [{"role": "user", "content": "q"}])
    assert r.text == "ok" and len(fake.calls) == 4
    assert sleeps[0] == 7.0 and len(sleeps) == 3


def test_fatal_errors_are_not_retried(sleeps):
    fake = FakeOpenRouter([_status_error(openai.BadRequestError, 400, "bad request"), text_resp("x")])
    with pytest.raises(openai.BadRequestError):
        llm.chat(fake, "subject", [{"role": "user", "content": "q"}])
    assert len(fake.calls) == 1 and sleeps == []


def test_transient_errors_give_up_after_max_retries(sleeps, monkeypatch):
    monkeypatch.setattr(config, "MAX_RETRIES", 2)
    fake = FakeOpenRouter([_status_error(openai.InternalServerError, 503, "down")] * 3)
    with pytest.raises(openai.InternalServerError):
        llm.chat(fake, "subject", [{"role": "user", "content": "q"}])
    assert len(fake.calls) == 3 and len(sleeps) == 2


def test_rate_limiter_holds_starts_to_per_minute():
    now = [0.0]
    waits = []

    def sleep(s):
        waits.append(s)
        now[0] += s

    lim = llm.RateLimiter(3, clock=lambda: now[0], sleep=sleep)
    for _ in range(3):
        lim.acquire()
    assert waits == []
    now[0] = 10.0
    lim.acquire()                      # 4th start must wait until the first is 60 s old
    assert waits == [50.0] and now[0] == 60.0


def test_chat_uses_client_limiter(sleeps):
    fake = FakeOpenRouter([text_resp("a"), text_resp("b")])
    acquired = []
    fake._study_limiter = type("L", (), {"acquire": lambda self: acquired.append(1)})()
    llm.chat(fake, "judge", [{"role": "user", "content": "q"}])       # judge is always a free model
    llm.chat(fake, "generator", [{"role": "user", "content": "q"}])
    assert len(acquired) == 2


def test_make_client_attaches_limiter_for_free_profile(monkeypatch):
    monkeypatch.setenv(config.OPENROUTER_KEY_ENV, "test-not-a-real-key")
    monkeypatch.setattr(config, "REQUESTS_PER_MINUTE", 18)
    client = llm.make_client()
    assert client._study_limiter.per_minute == 18
    assert client.max_retries == 0


def test_paid_models_skip_the_free_limiter(monkeypatch, sleeps):
    roles = dict(config.ROLES)
    roles["subject"] = {**roles["subject"], "model": "deepseek/deepseek-v4.1-flash"}
    monkeypatch.setattr(config, "ROLES", roles)
    fake = FakeOpenRouter([text_resp("a"), text_resp("b")])
    acquired = []
    fake._study_limiter = type("L", (), {"acquire": lambda self: acquired.append(1)})()
    llm.chat(fake, "subject", [{"role": "user", "content": "q"}])   # paid: not limited
    llm.chat(fake, "judge", [{"role": "user", "content": "q"}])     # free: limited
    assert len(acquired) == 1


def test_judge_and_generator_are_free_nemotron_super_whatever_the_subject():
    for role in ("judge", "generator"):
        assert config.ROLES[role]["model"] == "nvidia/nemotron-3-super-120b-a12b:free"
        assert config.is_free(role)
    assert set(config.SUBJECTS) >= {config.SUBJECT}
