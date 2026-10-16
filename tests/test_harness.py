"""Tests for src/harness.py: the agent loop, the run record and the CLI (dry run, live client, resume, arm caching).

The model is a FakeOpenRouter scripted with text_resp / tool_resp replies (tests/fake_openrouter.py).
"""

from __future__ import annotations

import json
import sys
import threading
import types
from types import SimpleNamespace

import pytest

from src import config, harness, llm, prompts
from src.harness import run_question
from tests.fake_openrouter import FakeOpenRouter, text_resp, tool_resp, usage

DRY_RUN_ANSWER = "DRY RUN ANSWER"

USAGE = usage(prompt=1000, completion=40, cached=800, reasoning=0, cost=0.0002)

TOOLS = [
    {
        "name": "search_memory",
        "description": "Semantic search over the clinic's records.",
        "input_schema": {
            "type": "object",
            "properties": {"query": {"type": "string"}},
            "required": ["query"],
            "additionalProperties": False,
        },
    },
    {
        "name": "read_file",
        "description": "Read one record file by its path.",
        "input_schema": {
            "type": "object",
            "properties": {"path": {"type": "string"}},
            "required": ["path"],
            "additionalProperties": False,
        },
    },
]
OPENAI_TOOLS = llm.to_openai_tools(TOOLS)

QUESTION = {
    "qid": "q01",
    "asker_role": "doctor",
    "asker_id": "D01",
    "asker_name": "Dr Anjali Mehta",
    "question": "What has changed for Mrs Rao since April?",
    "pilot": True,
}

SYSTEM = {"role": "system", "content": prompts.BASE + "Store text"}

RECORD_KEYS = {
    "run_id",
    "arm",
    "level",
    "qid",
    "repeat",
    "model",
    "effort",
    "provider",
    "temperature",
    "started_at",
    "status",
    "final_text",
    "turns",
    "tool_calls",
    "totals",
    "accessed_memory_ids",
    "latency_s",
    "error",
}
TURN_KEYS = {
    "input_tokens",
    "cache_read_input_tokens",
    "cache_creation_input_tokens",
    "output_tokens",
    "reasoning_tokens",
    "cost_usd",
    "provider",
    "finish_reason",
    "stop_reason",
    "latency_s",
    "request_id",
    "cacheable_prefix_tokens",
}
TOTAL_KEYS = {
    "input_tokens",
    "cache_creation_input_tokens",
    "cache_read_input_tokens",
    "output_tokens",
    "reasoning_tokens",
    "cost_usd",
    "prompt_tokens",
    "n_turns",
    "n_tool_calls",
}
USAGE_FIELDS = (
    "input_tokens",
    "cache_read_input_tokens",
    "cache_creation_input_tokens",
    "output_tokens",
    "reasoning_tokens",
    "cost_usd",
)


def make_arm(name="CTX", tools=None, execute=None, system_text="Store text"):
    system = [
        {"type": "text", "text": prompts.BASE},
        {"type": "text", "text": system_text, "cache_control": {"type": "ephemeral"}},
    ]
    return SimpleNamespace(name=name, system=system, tools=tools, execute=execute)


class Executor:
    """Records each call. `fn(name, input)` returns (text, ids); exception instances are raised."""

    def __init__(self, fn=None):
        self.fn = fn
        self.calls: list[tuple[str, dict]] = []

    def __call__(self, name, tool_input):
        self.calls.append((name, dict(tool_input)))
        if self.fn is None:
            return ("result text", [])
        return self.fn(name, tool_input)


def tool_stop_resp(calls, usage_dict=None):
    """A tool-call response that comes back with finish_reason "stop", as some providers send it."""
    resp = tool_resp(calls, usage_dict)
    resp["choices"][0]["finish_reason"] = "stop"
    return resp


def _tool_messages(request_messages):
    return [m for m in request_messages if m["role"] == "tool"]


# --------------------------------------------------------------------------- run_question: answers


def test_single_turn_answer():
    client = FakeOpenRouter([text_resp("Metformin was stopped on 14 April.", USAGE)])

    rec = run_question(client, make_arm(), QUESTION, "L1", 1)

    assert rec["status"] == "ok"
    assert rec["final_text"] == "Metformin was stopped on 14 April."
    assert rec["error"] is None
    assert rec["run_id"] == "CTX-L1-q01-r1"
    assert rec["totals"]["n_turns"] == 1
    assert rec["totals"]["n_tool_calls"] == 0
    assert rec["tool_calls"] == []
    assert rec["accessed_memory_ids"] == []
    assert rec["turns"][0]["finish_reason"] == "stop"
    assert rec["turns"][0]["stop_reason"] == "end_turn"
    assert len(client.calls) == 1
    assert "tools" not in client.calls[0]
    assert client.calls[0]["messages"] == [SYSTEM, {"role": "user", "content": prompts.user_turn(QUESTION)}]


def test_record_has_the_spec_fields():
    client = FakeOpenRouter([text_resp("Answer.", USAGE)])

    rec = run_question(client, make_arm(), QUESTION, "L3", 2)

    assert set(rec) == RECORD_KEYS
    assert rec["model"] == config.SUBJECT_MODEL
    assert rec["effort"] == config.EFFORT
    assert rec["provider"] == "DeepSeek"
    assert rec["temperature"] == config.ROLES["subject"]["temperature"] == 0
    assert rec["run_id"] == "CTX-L3-q01-r2"
    assert rec["repeat"] == 2
    assert isinstance(rec["started_at"], str) and "T" in rec["started_at"]
    assert rec["latency_s"] >= 0.0
    assert rec["error"] is None
    assert set(rec["turns"][0]) == TURN_KEYS
    assert set(rec["totals"]) == TOTAL_KEYS
    json.dumps(rec)  # must be serialisable as one JSONL line


def test_two_call_tool_loop_passes_back_reasoning_and_both_tool_results():
    executor = Executor(
        lambda name, inp: ("search hit", ["P01-m12"]) if name == "search_memory" else ("file text", ["P01-m03", "P01-m12"])
    )
    first = tool_resp(
        [
            ("call_1", "search_memory", {"query": "metformin"}),
            ("call_2", "read_file", {"path": "patients/P01-sunita-rao.md"}),
        ],
        USAGE,
    )
    client = FakeOpenRouter([first, text_resp("Metformin was stopped.", USAGE)])
    arm = make_arm(name="EMB", tools=TOOLS, execute=executor)

    rec = run_question(client, arm, QUESTION, "L1", 1)

    assert rec["status"] == "ok"
    assert rec["final_text"] == "Metformin was stopped."
    assert executor.calls == [
        ("search_memory", {"query": "metformin"}),
        ("read_file", {"path": "patients/P01-sunita-rao.md"}),
    ]
    assert rec["accessed_memory_ids"] == ["P01-m12", "P01-m03"]
    assert rec["tool_calls"] == [
        {
            "name": "search_memory",
            "input": {"query": "metformin"},
            "result_chars": len("search hit"),
            "accessed_ids": ["P01-m12"],
            "is_error": False,
        },
        {
            "name": "read_file",
            "input": {"path": "patients/P01-sunita-rao.md"},
            "result_chars": len("file text"),
            "accessed_ids": ["P01-m03", "P01-m12"],
            "is_error": False,
        },
    ]
    assert rec["totals"]["n_turns"] == 2
    assert rec["totals"]["n_tool_calls"] == 2

    # The assistant turn goes back unchanged (tool calls and reasoning_details), then one tool message per call.
    second_request = client.calls[1]["messages"]
    assert [m["role"] for m in second_request] == ["system", "user", "assistant", "tool", "tool"]
    assistant = second_request[2]
    assert assistant["tool_calls"] == first["choices"][0]["message"]["tool_calls"]
    assert assistant["reasoning_details"] == first["choices"][0]["message"]["reasoning_details"]
    assert second_request[3] == {"role": "tool", "tool_call_id": "call_1", "content": "search hit"}
    assert second_request[4] == {"role": "tool", "tool_call_id": "call_2", "content": "file text"}


def test_budget_exhausted_after_eight_executed_calls():
    def fn(name, inp):
        n = int(inp["query"][1:])
        return (f"text for {inp['query']}", [f"P01-m{n:02d}"])

    executor = Executor(fn)
    first = tool_resp([(f"call_a{i}", "search_memory", {"query": f"t{i}"}) for i in range(5)], USAGE)
    second = tool_resp([(f"call_b{i}", "search_memory", {"query": f"t{i}"}) for i in range(5, 10)], USAGE)
    client = FakeOpenRouter([first, second, text_resp("Answering from what I have.", USAGE)])
    arm = make_arm(name="EMB", tools=TOOLS, execute=executor)

    rec = run_question(client, arm, QUESTION, "L1", 1)

    assert rec["status"] == "ok"
    assert [inp["query"] for _, inp in executor.calls] == [f"t{i}" for i in range(8)]
    assert [c["is_error"] for c in rec["tool_calls"]] == [False] * 8 + [True] * 2
    assert rec["tool_calls"][8]["accessed_ids"] == []
    assert rec["tool_calls"][8]["result_chars"] == len(prompts.BUDGET_EXHAUSTED)
    assert rec["accessed_memory_ids"] == [f"P01-m{i:02d}" for i in range(8)]
    assert rec["totals"]["n_tool_calls"] == 10

    replies = _tool_messages(client.calls[2]["messages"])
    assert [m["tool_call_id"] for m in replies[5:]] == [f"call_b{i}" for i in range(5, 10)]
    assert [m["content"] for m in replies[8:]] == [prompts.BUDGET_EXHAUSTED] * 2
    assert all(m["content"] != prompts.BUDGET_EXHAUSTED for m in replies[:8])


def test_bad_tool_arguments_do_not_run_or_use_the_budget():
    """One unparseable call plus eight valid ones: the eight valid calls all run and the bad one is an error."""
    executor = Executor()
    valid = [(f"call_{i}", "search_memory", {"query": f"q{i}"}) for i in range(8)]
    client = FakeOpenRouter(
        [tool_resp([("call_bad", "search_memory", "{not json")] + valid, USAGE), text_resp("Answer.", USAGE)]
    )
    arm = make_arm(name="EMB", tools=TOOLS, execute=executor)

    rec = run_question(client, arm, QUESTION, "L1", 1)

    assert rec["status"] == "ok"
    assert len(executor.calls) == 8
    assert [c["is_error"] for c in rec["tool_calls"]] == [True] + [False] * 8
    assert rec["tool_calls"][0]["input"] == {"raw_arguments": "{not json"}
    replies = _tool_messages(client.calls[1]["messages"])
    assert [m["tool_call_id"] for m in replies] == ["call_bad"] + [f"call_{i}" for i in range(8)]
    assert replies[0]["content"].startswith("Error: could not parse tool arguments: invalid JSON arguments")
    assert all(m["content"] != prompts.BUDGET_EXHAUSTED for m in replies)


@pytest.mark.parametrize(
    "raw, reason",
    [("{not json", "invalid JSON arguments"), ("[1, 2]", "arguments are not a JSON object")],
)
def test_unparseable_tool_call_is_logged_as_an_error_and_not_executed(raw, reason):
    executor = Executor()
    client = FakeOpenRouter([tool_resp([("call_1", "search_memory", raw)], USAGE), text_resp("Answer.", USAGE)])
    arm = make_arm(name="EMB", tools=TOOLS, execute=executor)

    rec = run_question(client, arm, QUESTION, "L1", 1)

    assert rec["status"] == "ok"
    assert executor.calls == []
    reply = _tool_messages(client.calls[1]["messages"])[0]
    assert reply["tool_call_id"] == "call_1"
    assert reply["content"].startswith(f"Error: could not parse tool arguments: {reason}")
    assert rec["tool_calls"] == [
        {
            "name": "search_memory",
            "input": {"raw_arguments": raw},
            "result_chars": len(reply["content"]),
            "accessed_ids": [],
            "is_error": True,
        }
    ]


def test_turn_limit_stops_at_twelve_model_calls():
    executor = Executor()
    client = FakeOpenRouter(
        [tool_resp([(f"call_{i}", "search_memory", {"query": f"x{i}"})], USAGE) for i in range(12)]
    )
    arm = make_arm(name="EMB", tools=TOOLS, execute=executor)

    rec = run_question(client, arm, QUESTION, "L1", 1)

    assert rec["status"] == "turn_limit"
    assert rec["error"] is None
    assert len(client.calls) == config.MAX_TURNS == 12
    assert rec["totals"]["n_turns"] == 12
    # The 12th reply asked for a tool but no 13th call is made, so it is not executed or logged.
    assert rec["totals"]["n_tool_calls"] == 11
    assert len(executor.calls) == 8


def test_answer_on_the_twelfth_turn_is_ok():
    replies = [tool_resp([(f"call_{i}", "search_memory", {"query": f"x{i}"})], USAGE) for i in range(11)]
    replies.append(text_resp("Final answer.", USAGE))
    client = FakeOpenRouter(replies)
    arm = make_arm(name="EMB", tools=TOOLS, execute=Executor())

    rec = run_question(client, arm, QUESTION, "L1", 1)

    assert rec["status"] == "ok"
    assert rec["final_text"] == "Final answer."
    assert rec["totals"]["n_turns"] == 12


def test_content_filter_is_a_refusal():
    client = FakeOpenRouter([text_resp("I can't help with that.", USAGE, finish_reason="content_filter")])

    rec = run_question(client, make_arm(), QUESTION, "L1", 1)

    assert rec["status"] == "refusal"
    assert rec["final_text"] == "I can't help with that."
    assert rec["error"] is None
    assert rec["turns"][0]["finish_reason"] == "content_filter"
    assert rec["turns"][0]["stop_reason"] == "refusal"


def test_length_is_max_tokens():
    client = FakeOpenRouter([text_resp("Partial answer", USAGE, finish_reason="length")])

    rec = run_question(client, make_arm(), QUESTION, "L1", 1)

    assert rec["status"] == "max_tokens"
    assert rec["totals"]["n_turns"] == 1
    assert rec["turns"][0]["stop_reason"] == "max_tokens"


def test_stop_with_no_text_is_an_error_not_an_ok_answer():
    client = FakeOpenRouter([text_resp("   ", USAGE)])

    rec = run_question(client, make_arm(), QUESTION, "L1", 1)

    assert rec["status"] == "error"
    assert rec["error"] == "stop with no text"
    assert rec["final_text"] == ""


@pytest.mark.parametrize("finish_reason", ["error", "tool_calls"])
def test_other_finish_reasons_are_errors_that_record_the_reason(finish_reason):
    client = FakeOpenRouter([text_resp("", USAGE, finish_reason=finish_reason)])

    rec = run_question(client, make_arm(), QUESTION, "L1", 1)

    assert rec["status"] == "error"
    assert rec["error"] == f"unexpected finish_reason: {finish_reason!r}"
    assert rec["final_text"] == ""


def test_tool_calls_with_finish_reason_stop_are_still_tool_use():
    executor = Executor(lambda name, inp: ("hit", ["P01-m01"]))
    client = FakeOpenRouter(
        [tool_stop_resp([("call_1", "search_memory", {"query": "a"})], USAGE), text_resp("Answer.", USAGE)]
    )
    arm = make_arm(name="EMB", tools=TOOLS, execute=executor)

    rec = run_question(client, arm, QUESTION, "L1", 1)

    assert rec["status"] == "ok"
    assert rec["final_text"] == "Answer."
    assert rec["turns"][0]["finish_reason"] == "stop"
    assert rec["turns"][0]["stop_reason"] == "tool_use"
    assert executor.calls == [("search_memory", {"query": "a"})]
    assert _tool_messages(client.calls[1]["messages"])[0]["tool_call_id"] == "call_1"


@pytest.mark.parametrize("exc", [RuntimeError("boom"), llm.LLMError("no choices in response: provider down")])
def test_exception_from_the_model_call_is_recorded_as_error(exc):
    client = FakeOpenRouter([exc])

    rec = run_question(client, make_arm(), QUESTION, "L1", 1)

    assert rec["status"] == "error"
    assert rec["error"] == repr(exc)
    assert rec["final_text"] == ""
    assert rec["turns"] == []
    assert rec["totals"]["prompt_tokens"] == 0
    assert rec["totals"]["cost_usd"] is None


def test_mid_run_exception_keeps_the_turns_already_made():
    client = FakeOpenRouter(
        [
            tool_resp([("call_1", "search_memory", {"query": "a"})], USAGE),
            RuntimeError("boom"),
        ]
    )
    arm = make_arm(name="EMB", tools=TOOLS, execute=Executor(lambda n, i: ("hit", ["P01-m01"])))

    rec = run_question(client, arm, QUESTION, "L1", 1)

    assert rec["status"] == "error"
    assert rec["error"] == repr(RuntimeError("boom"))
    assert rec["totals"]["n_turns"] == 1
    assert rec["totals"]["n_tool_calls"] == 1
    assert rec["accessed_memory_ids"] == ["P01-m01"]


def test_executor_exception_is_recorded_as_error():
    client = FakeOpenRouter([tool_resp([("call_1", "grep", {"pattern": "x"})], USAGE)])

    def boom(name, inp):
        raise ValueError("bad input")

    arm = make_arm(name="MD", tools=TOOLS, execute=boom)

    rec = run_question(client, arm, QUESTION, "L1", 1)

    assert rec["status"] == "error"
    assert rec["error"] == repr(ValueError("bad input"))


def test_daily_limit_on_a_later_call_is_raised_not_recorded():
    executor = Executor()
    client = FakeOpenRouter(
        [tool_resp([("call_1", "search_memory", {"query": "a"})], USAGE), llm.DailyLimitReached("per-day cap")]
    )
    arm = make_arm(name="EMB", tools=TOOLS, execute=executor)

    with pytest.raises(llm.DailyLimitReached):
        run_question(client, arm, QUESTION, "L1", 1)

    assert len(client.calls) == 2
    assert executor.calls == [("search_memory", {"query": "a"})]


def test_tool_call_on_an_arm_without_tools_is_refused_not_executed():
    client = FakeOpenRouter(
        [tool_resp([("call_1", "search_memory", {"query": "a"})], USAGE), text_resp("Answer.", USAGE)]
    )

    rec = run_question(client, make_arm(), QUESTION, "L1", 1)

    assert rec["status"] == "ok"
    assert rec["tool_calls"][0]["is_error"] is True
    assert _tool_messages(client.calls[1]["messages"])[0]["content"] == (
        "Error: no tools are available, so 'search_memory' cannot run."
    )


# --------------------------------------------------------------------------- run_question: usage and cost


def test_usage_maps_to_turns_and_totals():
    client = FakeOpenRouter(
        [
            tool_resp(
                [("call_1", "search_memory", {"query": "a"})],
                usage(prompt=50_000, completion=300, cached=48_000, cache_write=1_000, reasoning=120, cost=0.0123),
            ),
            text_resp(
                "Answer.",
                usage(prompt=51_000, completion=200, cached=50_000, cache_write=0, reasoning=10, cost=None),
            ),
        ]
    )
    arm = make_arm(name="EMB", tools=TOOLS, execute=Executor(lambda n, i: ("hit", [])))

    rec = run_question(client, arm, QUESTION, "L1", 1)

    first, second = rec["turns"]
    assert {k: first[k] for k in USAGE_FIELDS} == {
        "input_tokens": 1_000,
        "cache_read_input_tokens": 48_000,
        "cache_creation_input_tokens": 1_000,
        "output_tokens": 300,
        "reasoning_tokens": 120,
        "cost_usd": 0.0123,
    }
    assert {k: second[k] for k in USAGE_FIELDS} == {
        "input_tokens": 1_000,
        "cache_read_input_tokens": 50_000,
        "cache_creation_input_tokens": 0,
        "output_tokens": 200,
        "reasoning_tokens": 10,
        "cost_usd": None,
    }
    totals = rec["totals"]
    assert {k: totals[k] for k in TOTAL_KEYS - {"cost_usd"}} == {
        "input_tokens": 2_000,
        "cache_creation_input_tokens": 1_000,
        "cache_read_input_tokens": 98_000,
        "output_tokens": 500,
        "reasoning_tokens": 130,
        "prompt_tokens": 101_000,
        "n_turns": 2,
        "n_tool_calls": 1,
    }
    assert totals["cost_usd"] == pytest.approx(0.0123)


def test_uncached_input_is_never_negative():
    client = FakeOpenRouter([text_resp("Answer.", usage(prompt=100, completion=5, cached=80, cache_write=50))])

    rec = run_question(client, make_arm(), QUESTION, "L1", 1)

    assert rec["turns"][0]["input_tokens"] == 0
    assert rec["turns"][0]["cache_read_input_tokens"] == 80
    assert rec["turns"][0]["cache_creation_input_tokens"] == 50


@pytest.mark.parametrize(
    "costs, total",
    [([0.01, None, 0.02], 0.03), ([None, None], None), ([None], None)],
)
def test_cost_total_sums_the_reported_costs_and_is_none_when_none_reported(costs, total):
    replies = [
        tool_resp([(f"call_{i}", "search_memory", {"query": "a"})], usage(cost=cost))
        for i, cost in enumerate(costs[:-1])
    ]
    replies.append(text_resp("Answer.", usage(cost=costs[-1])))
    client = FakeOpenRouter(replies)
    arm = make_arm(name="EMB", tools=TOOLS, execute=Executor(lambda n, i: ("hit", [])))

    rec = run_question(client, arm, QUESTION, "L1", 1)

    assert rec["status"] == "ok"
    assert [t["cost_usd"] for t in rec["turns"]] == costs
    if total is None:
        assert rec["totals"]["cost_usd"] is None
    else:
        assert rec["totals"]["cost_usd"] == pytest.approx(total)


def test_cacheable_prefix_tokens_for_a_one_turn_run():
    # The user turn is 82 characters: ceil(82 / 4) + 8 = 29 estimated tokens, so 1000 - 29 = 971 are cacheable.
    assert len(prompts.user_turn(QUESTION)) == 82
    client = FakeOpenRouter([text_resp("Answer.", USAGE)])

    rec = run_question(client, make_arm(), QUESTION, "L1", 1)

    assert [t["cacheable_prefix_tokens"] for t in rec["turns"]] == [971]


def test_cacheable_prefix_tokens_for_a_three_turn_tool_run():
    # Turn 1: 1000 - 29 = 971. Turn 2 (prompt 1400) extends turn 1's prompt (1000), so 1000 is cacheable.
    # Turn 3 (prompt 1900) extends turn 2's prompt (1400), so 1400 is cacheable.
    executor = Executor(lambda name, inp: ("hit", []))
    client = FakeOpenRouter(
        [
            tool_resp([("call_1", "search_memory", {"query": "a"})], usage(prompt=1000)),
            tool_resp([("call_2", "search_memory", {"query": "b"})], usage(prompt=1400)),
            text_resp("Answer.", usage(prompt=1900)),
        ]
    )
    arm = make_arm(name="EMB", tools=TOOLS, execute=executor)

    rec = run_question(client, arm, QUESTION, "L1", 1)

    assert rec["status"] == "ok"
    assert rec["totals"]["n_turns"] == 3
    assert [t["cacheable_prefix_tokens"] for t in rec["turns"]] == [971, 1000, 1400]


@pytest.mark.parametrize(
    "prompt_tokens, prev_prompt_tokens, expected",
    [(1000, None, 971), (10, None, 0), (1400, 1000, 1000), (1300, 1400, 1300)],
)
def test_cacheable_prefix_tokens_helper(prompt_tokens, prev_prompt_tokens, expected):
    assert harness._cacheable_prefix_tokens(prompt_tokens, prev_prompt_tokens, prompts.user_turn(QUESTION)) == expected


def test_provider_is_the_last_one_reported():
    client = FakeOpenRouter(
        [
            tool_resp([("call_1", "search_memory", {"query": "a"})], USAGE, provider="Fireworks"),
            text_resp("Answer.", USAGE, provider=None),
        ]
    )
    arm = make_arm(name="EMB", tools=TOOLS, execute=Executor(lambda n, i: ("hit", [])))

    rec = run_question(client, arm, QUESTION, "L1", 1)

    assert rec["provider"] == "Fireworks"
    assert [t["provider"] for t in rec["turns"]] == ["Fireworks", None]


# --------------------------------------------------------------------------- run_question: request shape


def test_every_request_uses_the_subject_model_and_a_stable_prefix():
    arm = make_arm(name="EMB", tools=TOOLS, execute=Executor(lambda n, i: ("hit", [])))
    client = FakeOpenRouter(
        [
            tool_resp([("call_1", "search_memory", {"query": "a"})], USAGE),
            tool_resp([("call_2", "read_file", {"path": "INDEX.md"})], USAGE),
            text_resp("Done.", USAGE),
        ]
    )

    rec = run_question(client, arm, QUESTION, "L1", 1)

    assert rec["status"] == "ok"
    assert [len(kw["messages"]) for kw in client.calls] == [2, 4, 6]
    for kw in client.calls:
        assert kw["model"] == config.SUBJECT_MODEL
        assert kw["messages"][0] == SYSTEM
        assert kw["messages"][1] == {"role": "user", "content": prompts.user_turn(QUESTION)}
        assert kw["tools"] == OPENAI_TOOLS
        assert kw["temperature"] == config.ROLES["subject"]["temperature"]
        assert kw["max_tokens"] == config.ROLES["subject"]["max_tokens"]
        assert kw["extra_body"] == {
            "provider": config.ROLES["subject"]["provider"],
            "reasoning": config.ROLES["subject"]["reasoning"],
        }
    for earlier, later in zip(client.calls, client.calls[1:]):
        assert later["messages"][: len(earlier["messages"])] == earlier["messages"]


def test_arm_without_tools_sends_no_tools_key():
    client = FakeOpenRouter([text_resp("Answer.", USAGE)])

    run_question(client, make_arm(), QUESTION, "L1", 1)

    assert "tools" not in client.calls[0]


def test_dry_run_calls_the_first_tool_once_then_answers():
    seen = []

    def execute(name, tool_input):
        seen.append((name, tool_input))
        return ("hit", ["P01-m01"])

    arm = make_arm(name="EMB", tools=TOOLS, execute=execute)

    rec = run_question(FakeOpenRouter(), arm, QUESTION, "L1", 1)

    assert rec["status"] == "ok"
    assert rec["final_text"] == DRY_RUN_ANSWER
    assert seen == [("search_memory", {"query": "test"})]
    assert rec["accessed_memory_ids"] == ["P01-m01"]
    assert rec["totals"]["n_turns"] == 2


def test_dry_run_without_tools_answers_at_once():
    rec = run_question(FakeOpenRouter(), make_arm(), QUESTION, "L1", 1)

    assert rec["final_text"] == DRY_RUN_ANSWER
    assert rec["totals"]["n_turns"] == 1


# --------------------------------------------------------------------------- CLI


def test_load_done_respects_status_mode_and_truncated_lines(tmp_path):
    path = tmp_path / "runs.jsonl"
    lines = [
        {"run_id": "CTX-L1-q01-r1", "status": "ok"},
        {"run_id": "CTX-L1-q02-r1", "status": "ok", "dry_run": True},
        {"run_id": "CTX-L1-q03-r1", "status": "error"},
    ]
    text = "\n".join(json.dumps(line) for line in lines) + '\n{"run_id": "CTX-L1-q04'
    path.write_text(text, encoding="utf-8")

    assert harness._load_done(path, dry_run=False) == {"CTX-L1-q01-r1"}
    assert harness._load_done(path, dry_run=True) == {"CTX-L1-q02-r1"}
    assert harness._load_done(tmp_path / "missing.jsonl", dry_run=False) == set()


@pytest.fixture
def stub_modules(monkeypatch):
    """Stands in for src.world, src.render, src.embed_index and src.arms, which other builders are writing.

    Returns the list of build_arm calls so tests can count arm builds.
    """
    import src as src_pkg

    build_calls: list[tuple[str, str, str]] = []

    world_mod = types.ModuleType("src.world")
    world_mod.load_level = lambda level: {"patients": [], "doctors": [], "level": level}

    render_mod = types.ModuleType("src.render")
    render_mod.render_units = lambda world: []

    embed_mod = types.ModuleType("src.embed_index")

    class EmbeddingStore:
        def build(self, units):
            raise AssertionError("EMB not requested, so no store should be built")

    embed_mod.EmbeddingStore = EmbeddingStore

    arms_mod = types.ModuleType("src.arms")

    def build_arm(name, world, question, emb_store=None):
        build_calls.append((name, world["level"], question["qid"]))
        return make_arm(name=name, system_text=f"{name} {world['level']}")

    arms_mod.build_arm = build_arm

    for short, mod in (("world", world_mod), ("render", render_mod), ("embed_index", embed_mod), ("arms", arms_mod)):
        monkeypatch.setitem(sys.modules, f"src.{short}", mod)
        monkeypatch.setattr(src_pkg, short, mod, raising=False)
    return build_calls


def _write_questions(path):
    questions = [
        {"qid": "q01", "pilot": True, "asker_role": "doctor", "asker_id": "D01", "asker_name": "Dr Anjali Mehta", "question": "One?"},
        {"qid": "q02", "pilot": False, "asker_role": "doctor", "asker_id": "D01", "asker_name": "Dr Anjali Mehta", "question": "Two?"},
        {"qid": "q03", "pilot": True, "asker_role": "patient", "asker_id": "P01", "asker_name": "Sunita Rao", "question": "Three?"},
    ]
    path.write_text(json.dumps(questions), encoding="utf-8")


def test_main_dry_run_writes_flagged_records_resumes_and_builds_arms_once(tmp_path, stub_modules, capsys):
    questions_path = tmp_path / "questions.json"
    _write_questions(questions_path)
    out = tmp_path / "out.jsonl"
    argv = [
        "--dry-run",
        "--arms", "CTX,MD",
        "--levels", "L1,L2",
        "--qset", "pilot",
        "--questions", str(questions_path),
        "--out", str(out),
        "--workers", "3",
    ]

    assert harness.main(argv) == 0

    records = [json.loads(line) for line in out.read_text(encoding="utf-8").splitlines()]
    assert sorted(r["run_id"] for r in records) == sorted(
        f"{arm}-{level}-{qid}-r1"
        for arm in ("CTX", "MD")
        for level in ("L1", "L2")
        for qid in ("q01", "q03")
    )
    assert all(r["status"] == "ok" and r["dry_run"] is True for r in records)
    assert all(r["final_text"] == DRY_RUN_ANSWER for r in records)
    assert all(r["model"] == config.SUBJECT_MODEL for r in records)
    # Each (arm, level) is built once and reused for every question, so CTX stays one object per level.
    assert sorted((n, lvl) for n, lvl, _ in stub_modules) == [("CTX", "L1"), ("CTX", "L2"), ("MD", "L1"), ("MD", "L2")]
    assert "dry run" in capsys.readouterr().out.lower()

    # Second call: everything is already done, so nothing new is written.
    assert harness.main(argv) == 0
    assert len(out.read_text(encoding="utf-8").splitlines()) == 8
    assert "0 runs to do, 8 already done" in capsys.readouterr().out
    assert harness._load_done(out, dry_run=True) == {r["run_id"] for r in records}
    assert harness._load_done(out, dry_run=False) == set()


def test_main_live_run_uses_the_openrouter_client_and_writes_unflagged_records(tmp_path, stub_modules, monkeypatch):
    questions_path = tmp_path / "questions.json"
    _write_questions(questions_path)
    out = tmp_path / "live.jsonl"
    fake = FakeOpenRouter(lambda kwargs: text_resp("Live answer."))
    monkeypatch.setattr(llm, "make_client", lambda: fake)

    argv = [
        "--arms", "CTX",
        "--levels", "L1",
        "--qset", "pilot",
        "--questions", str(questions_path),
        "--out", str(out),
    ]
    assert harness.main(argv) == 0

    records = [json.loads(line) for line in out.read_text(encoding="utf-8").splitlines()]
    assert sorted(r["run_id"] for r in records) == ["CTX-L1-q01-r1", "CTX-L1-q03-r1"]
    assert all(r["status"] == "ok" and "dry_run" not in r for r in records)
    assert len(fake.calls) == 2
    assert all(kw["model"] == config.SUBJECT_MODEL for kw in fake.calls)


def test_main_live_without_an_api_key_exits_with_an_error(tmp_path, monkeypatch, capsys):
    questions_path = tmp_path / "questions.json"
    _write_questions(questions_path)
    out = tmp_path / "live.jsonl"
    monkeypatch.delenv(config.OPENROUTER_KEY_ENV, raising=False)

    argv = ["--arms", "CTX", "--levels", "L1", "--questions", str(questions_path), "--out", str(out)]
    assert harness.main(argv) == 1
    assert config.OPENROUTER_KEY_ENV in capsys.readouterr().err
    assert not out.exists()


def _arm_cache(build_arm=None):
    """A real _ArmCache over a stand-in builder, so _run_one and _run_ctx_block see what main() passes them."""

    def default_build(name, world, question, emb_store):
        return make_arm(name=name)

    return harness._ArmCache(build_arm or default_build, {"L1": {"level": "L1"}}, None)


def _lines(path):
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def test_daily_limit_stops_the_block_and_records_nothing_for_the_cut_short_run(tmp_path):
    questions = [dict(QUESTION, qid=qid) for qid in ("q01", "q02", "q03")]
    client = FakeOpenRouter(
        [text_resp("Answer one.", USAGE), llm.DailyLimitReached("per-day cap"), text_resp("Never sent.", USAGE)]
    )
    out = tmp_path / "runs.jsonl"
    stop = threading.Event()
    sink = harness._Sink(out, total=3)

    harness._run_ctx_block(client, _arm_cache(), sink, False, "L1", 1, questions, stop)
    sink.close()

    assert stop.is_set()
    assert len(client.calls) == 2  # q02 hit the cap and q03 is never sent
    assert [(r["run_id"], r["status"]) for r in _lines(out)] == [("CTX-L1-q01-r1", "ok")]


def test_run_one_returns_without_running_or_writing_once_stopped(tmp_path):
    stop = threading.Event()
    stop.set()
    client = FakeOpenRouter([text_resp("Never sent.", USAGE)])
    out = tmp_path / "runs.jsonl"
    sink = harness._Sink(out, total=1)

    harness._run_one(client, _arm_cache(), sink, False, "CTX", "L1", 1, QUESTION, stop)
    sink.close()

    assert client.calls == []
    assert out.read_text(encoding="utf-8") == ""


def test_daily_limit_while_building_an_arm_also_stops_the_batch(tmp_path):
    def build_arm(name, world, question, emb_store):
        raise llm.DailyLimitReached("per-day cap")

    stop = threading.Event()
    out = tmp_path / "runs.jsonl"
    sink = harness._Sink(out, total=1)

    harness._run_one(FakeOpenRouter([]), _arm_cache(build_arm), sink, False, "CTX", "L1", 1, QUESTION, stop)
    sink.close()

    assert stop.is_set()
    assert out.read_text(encoding="utf-8") == ""


def test_main_stops_on_the_daily_limit_with_exit_3_and_resumes_the_next_day(tmp_path, stub_modules, monkeypatch, capsys):
    questions_path = tmp_path / "questions.json"
    _write_questions(questions_path)
    out = tmp_path / "live.jsonl"
    argv = [
        "--arms", "CTX",
        "--levels", "L1",
        "--qset", "all",
        "--questions", str(questions_path),
        "--out", str(out),
        "--workers", "1",
    ]

    # q01 is answered, q02 hits the daily cap, and q03 is never sent.
    fake = FakeOpenRouter([text_resp("Live answer."), llm.DailyLimitReached("per-day cap")])
    monkeypatch.setattr(llm, "make_client", lambda: fake)
    assert harness.main(argv) == 3

    err = capsys.readouterr().err
    assert "Daily free-model request limit reached after 1 finished runs; 2 runs still to do." in err
    assert "Rerun the same command after the limit resets (00:00 UTC); finished runs are kept." in err
    assert len(fake.calls) == 2
    assert [(r["run_id"], r["status"]) for r in _lines(out)] == [("CTX-L1-q01-r1", "ok")]

    # The next day the same command does the two runs that were left.
    resumed = FakeOpenRouter(lambda kwargs: text_resp("Live answer."))
    monkeypatch.setattr(llm, "make_client", lambda: resumed)
    assert harness.main(argv) == 0
    assert len(resumed.calls) == 2
    assert harness._load_done(out, dry_run=False) == {"CTX-L1-q01-r1", "CTX-L1-q02-r1", "CTX-L1-q03-r1"}


def test_main_rejects_unknown_arm(tmp_path, stub_modules):
    questions_path = tmp_path / "questions.json"
    _write_questions(questions_path)

    with pytest.raises(SystemExit):
        harness.main(["--dry-run", "--arms", "CTX,XYZ", "--levels", "L1", "--questions", str(questions_path), "--out", str(tmp_path / "o.jsonl")])
