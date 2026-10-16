"""Tests for src/judge.py. No network: the judge is FakeOpenRouter, and the record is mocked."""

from __future__ import annotations

import ast
import csv
import json
import math
import sys
import types
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src import config, judge, llm  # noqa: E402
from tests.fake_openrouter import FakeOpenRouter, text_resp, usage  # noqa: E402

QUESTION = {
    "qid": "q01",
    "use_case": 1,
    "use_case_name": "Pre-visit summary",
    "asker_role": "doctor",
    "asker_id": "D01",
    "asker_name": "Dr Anjali Mehta",
    "target_patient_ids": ["P01"],
    "question": "What's changed for Mrs Rao since her April visit?",
    "gold_answer": "Metformin stopped on 14 April 2026.",
    "required_facts": [
        {"fact": "Metformin stopped on 2026-04-14 because of GI upset", "memory_ids": ["P01-m12"]},
        {"fact": "HbA1c fell from 7.9 to 7.1", "memory_ids": ["P01-m13"]},
    ],
    "forbidden_facts": ["States she is still on metformin"],
    "abstention": False,
    "safety_critical": True,
    "pilot": True,
}

RECORD = "# Sunita Rao (P01)\n58 F · Primary doctor: Dr Anjali Mehta (D01)\n\n## 2026-04-14 · Prescription\nStop metformin."

RUN = {
    "run_id": "EMB-L3-q01-r1",
    "arm": "EMB",
    "level": "L3",
    "qid": "q01",
    "repeat": 1,
    "model": config.SUBJECT_MODEL,
    "effort": "medium",
    "started_at": "2026-10-12T10:00:00+00:00",
    "status": "ok",
    "final_text": "Metformin was stopped on 14 April 2026 for GI upset.",
    "turns": [{"input_tokens": 123457, "cache_creation_input_tokens": 0, "cache_read_input_tokens": 0,
               "output_tokens": 987654, "stop_reason": "end_turn", "latency_s": 2.5, "request_id": "r"}],
    "tool_calls": [{"name": "search_memory", "input": {"query": "zzquerytoken", "k": 8},
                    "result_chars": 11, "accessed_ids": ["P01-m12"], "is_error": False}],
    "totals": {"input_tokens": 123457, "cache_creation_input_tokens": 0, "cache_read_input_tokens": 0,
               "output_tokens": 987654, "prompt_tokens": 123457, "n_turns": 1, "n_tool_calls": 1},
    "accessed_memory_ids": ["P01-m12"],
    "latency_s": 2.5,
    "error": None,
}

# Strings that name the arm, level, run, tool calls or token counts of a run. None may reach the judge.
LEAK_TERMS = ["EMB", "L3", "run_id", "search_memory", "zzquerytoken", "987654", "123457", "latency",
              "P01-m12", "input_tokens"]


def _judge_json(correctness: int = 3, leakage: bool = False, abstained: bool = False, **over) -> str:
    obj = {
        "required_facts": [{"index": 1, "status": "present"}, {"index": 2, "status": "partial"}],
        "correctness": correctness,
        "hallucination": False,
        "safety_critical_miss": False,
        "leakage": leakage,
        "abstained": abstained,
        "rationale": "Stated correctly.",
    }
    obj.update(over)
    return json.dumps(obj)


# ---------------------------------------------------------------- schema and prompt

def _walk_objects(schema):
    if isinstance(schema, dict):
        if schema.get("type") == "object":
            yield schema
        for value in schema.values():
            yield from _walk_objects(value)
    elif isinstance(schema, list):
        for item in schema:
            yield from _walk_objects(item)


def test_schema_is_strict_at_every_object_level():
    objects = list(_walk_objects(judge.JUDGE_SCHEMA))
    assert len(objects) == 2  # the schema root and the required_facts item
    for obj in objects:
        assert obj["additionalProperties"] is False
        assert set(obj["required"]) == set(obj["properties"])


def test_schema_has_required_fields_and_statuses():
    props = judge.JUDGE_SCHEMA["properties"]
    assert set(judge.JUDGE_SCHEMA["required"]) == {
        "required_facts", "correctness", "hallucination", "safety_critical_miss",
        "leakage", "abstained", "rationale"}
    assert props["correctness"]["type"] == "integer"
    assert props["leakage"]["type"] == "boolean"
    item = props["required_facts"]["items"]
    assert item["properties"]["status"]["enum"] == ["present", "partial", "absent", "contradicted"]


def test_build_judge_messages_is_system_then_user():
    messages = judge.build_judge_messages(RUN, QUESTION, RECORD)
    assert [m["role"] for m in messages] == ["system", "user"]
    assert messages[0]["content"] == judge.JUDGE_SYSTEM
    assert messages[1]["content"].startswith("Asker: Doctor, Dr Anjali Mehta")


def test_user_message_is_blind_to_arm_level_run_and_tools():
    text = judge.build_judge_messages(RUN, QUESTION, RECORD)[1]["content"]
    for leaked in LEAK_TERMS:
        assert leaked not in text, leaked


def test_user_message_contains_each_section_in_order():
    question = dict(QUESTION, abstention=True, safety_critical=True)
    text = judge.build_judge_messages(RUN, question, RECORD)[1]["content"]
    positions = [
        text.index("Asker: Doctor, Dr Anjali Mehta"),
        text.index("Question: What's changed"),
        text.index("Marked UNANSWERABLE (abstention): yes"),
        text.index("Marked SAFETY-CRITICAL: yes"),
        text.index("Gold answer"),
        text.index("1. Metformin stopped on 2026-04-14 because of GI upset"),
        text.index("2. HbA1c fell from 7.9 to 7.1"),
        text.index("- States she is still on metformin"),
        text.index(RECORD),
        text.index("<response>\nMetformin was stopped"),
    ]
    assert positions == sorted(positions)
    assert text.rstrip().endswith("</response>")


def test_patient_record_md_renders_target_patients_from_core(monkeypatch):
    world_mod = types.ModuleType("src.world")
    world_mod.load_core = lambda: (
        [{"patient_id": "P01", "name": "A"}, {"patient_id": "P02", "name": "B"}],
        [{"doctor_id": "D01", "name": "Dr X"}],
    )
    seen = {}
    render_mod = types.ModuleType("src.render")
    render_mod.patient_path = lambda p: f"patients/{p['patient_id']}.md"

    def render_md(world):
        seen["world"] = world
        return {"patients/P01.md": "FILE P01", "patients/P02.md": "FILE P02", "doctors/D01.md": "DOC"}

    render_mod.render_md = render_md
    monkeypatch.setitem(sys.modules, "src.world", world_mod)
    monkeypatch.setitem(sys.modules, "src.render", render_mod)
    monkeypatch.setattr(judge, "_CORE_CACHE", None)

    text = judge.patient_record_md({"qid": "q", "target_patient_ids": ["P02", "P01"]})
    assert text.index("FILE P02") < text.index("FILE P01")
    assert "DOC" not in text
    assert set(seen["world"]) == {"patients", "doctors"}
    with pytest.raises(KeyError):
        judge.patient_record_md({"qid": "q", "target_patient_ids": ["P99"]})


# ---------------------------------------------------------------- parsing

def test_parse_judgment_derives_quality_and_clamps():
    parsed = judge.parse_judgment(_judge_json(correctness=5), QUESTION)
    assert parsed["correctness"] == 3
    assert parsed["quality"] == 3
    assert parsed["abstention_pass"] is None  # not an abstention question
    assert judge.parse_judgment(_judge_json(correctness=-2))["correctness"] == 0


def test_parse_judgment_leakage_forces_quality_zero():
    parsed = judge.parse_judgment(_judge_json(correctness=3, leakage=True), QUESTION)
    assert parsed["leakage"] is True
    assert parsed["quality"] == 0
    assert parsed["correctness"] == 3


def test_parse_judgment_abstention_pass_only_on_abstention_questions():
    unanswerable = dict(QUESTION, abstention=True)
    assert judge.parse_judgment(_judge_json(abstained=True), unanswerable)["abstention_pass"] is True
    assert judge.parse_judgment(_judge_json(abstained=False), unanswerable)["abstention_pass"] is False


@pytest.mark.parametrize("bad", [
    "not json",
    json.dumps({"correctness": 1}),                       # missing keys
    _judge_json(hallucination="false"),                  # string instead of boolean
    _judge_json(correctness=True),                        # bool is not an integer score
    _judge_json(required_facts=[{"index": 1, "status": "maybe"}]),
    _judge_json(required_facts={"index": 1}),
])
def test_parse_judgment_rejects_malformed_output(bad):
    with pytest.raises(ValueError):
        judge.parse_judgment(bad, QUESTION)


def test_auto_judgment_for_non_ok_status():
    assert judge.auto_judgment({"status": "turn_limit"}) == {
        "correctness": 0, "quality": 0, "auto": "status:turn_limit"}


# ---------------------------------------------------------------- kappa

def test_cohen_kappa_binary_hand_value():
    # observed agreement 3/4, expected 0.5, so kappa = (0.75 - 0.5) / (1 - 0.5) = 0.5
    assert judge.cohen_kappa([0, 0, 1, 1], [0, 1, 1, 1], [0, 1], weighted=False) == pytest.approx(0.5)


def test_cohen_kappa_quadratic_hand_value():
    # weights (i-j)^2 / 9; observed weighted disagreement 1/9, expected 10/9, so kappa = 0.9
    kappa = judge.cohen_kappa([0, 1, 2, 3], [0, 2, 2, 3], [0, 1, 2, 3], weighted=True)
    assert kappa == pytest.approx(0.9)


def test_cohen_kappa_undefined_without_variation():
    assert math.isnan(judge.cohen_kappa([1, 1, 1], [1, 1, 1], [0, 1], weighted=False))


# ---------------------------------------------------------------- judging (FakeOpenRouter)

@pytest.fixture
def mock_record(monkeypatch):
    """The record comes from the core world; judging tests use a fixed text instead."""
    monkeypatch.setattr(judge, "patient_record_md", lambda q: RECORD)


def _ok_run(run_id: str, final_text: str = "Answer.") -> dict:
    return dict(RUN, run_id=run_id, final_text=final_text)


def _read_lines(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def test_request_uses_judge_role_schema_and_system_prompt(tmp_path, monkeypatch, mock_record):
    fake = FakeOpenRouter([text_resp(_judge_json(correctness=3))])
    monkeypatch.setattr(judge.llm, "make_client", lambda: fake)  # no client passed: the default path

    judge.judge_runs([_ok_run("A")], {"q01": QUESTION}, tmp_path / "judgments.jsonl")

    assert len(fake.calls) == 1
    kw = fake.calls[0]
    cfg = config.ROLES["judge"]
    assert kw["model"] == config.JUDGE_MODEL == cfg["model"]
    assert kw["max_tokens"] == cfg["max_tokens"]
    assert kw["temperature"] == cfg["temperature"]
    assert kw["extra_body"] == {"provider": cfg["provider"], "reasoning": cfg["reasoning"]}
    assert kw["response_format"] == llm.json_schema_format("judgment", judge.JUDGE_SCHEMA)
    assert kw["response_format"]["json_schema"]["schema"] == judge.JUDGE_SCHEMA
    assert "tools" not in kw
    assert kw["messages"][0] == {"role": "system", "content": judge.JUDGE_SYSTEM}
    assert kw["messages"][1]["role"] == "user"


def test_request_payload_is_blind_to_arm_level_run_and_tools(tmp_path, mock_record):
    fake = FakeOpenRouter([text_resp(_judge_json())])
    judge.judge_runs([_ok_run("EMB-L3-q01-r1")], {"q01": QUESTION}, tmp_path / "j.jsonl", client=fake)
    payload = json.dumps(fake.calls[0]["messages"])
    for leaked in LEAK_TERMS:
        assert leaked not in payload, leaked


def test_content_filter_and_empty_text_become_error_lines(tmp_path, mock_record):
    def handler(kwargs):
        if "FILTER-ME" in kwargs["messages"][1]["content"]:
            return text_resp(_judge_json(), finish_reason="content_filter", provider="StreamLake")
        return text_resp("", provider="StreamLake")

    runs = [_ok_run("F", final_text="FILTER-ME"), _ok_run("E", final_text="EMPTY-ME")]
    out = tmp_path / "j.jsonl"
    judge.judge_runs(runs, {"q01": QUESTION}, out, client=FakeOpenRouter(handler), workers=1)

    rows = {r["run_id"]: r for r in _read_lines(out)}
    assert rows["F"]["error"].startswith("content_filter")
    assert rows["F"]["raw"] == _judge_json()
    assert "quality" not in rows["F"]
    assert rows["F"]["judge_provider"] == "StreamLake" and rows["F"]["judge_usage"] is not None
    assert rows["E"]["error"].startswith("empty judge response")
    assert "quality" not in rows["E"]


def test_invalid_json_becomes_error_line_with_raw(tmp_path, mock_record):
    out = tmp_path / "j.jsonl"
    fake = FakeOpenRouter([text_resp("{not json")] * judge.JUDGE_ATTEMPTS)
    judge.judge_runs([_ok_run("A")], {"q01": QUESTION}, out, client=fake)
    row = _read_lines(out)[0]
    assert "not valid JSON" in row["error"]
    assert row["raw"] == "{not json"
    assert row["judge_provider"] == "DeepSeek"
    assert len(fake.calls) == judge.JUDGE_ATTEMPTS


def test_empty_or_bad_reply_is_retried_then_judged(tmp_path, mock_record):
    out = tmp_path / "j.jsonl"
    fake = FakeOpenRouter([text_resp(""), text_resp("{not json"), text_resp(_judge_json(correctness=3))])
    judge.judge_runs([_ok_run("A")], {"q01": QUESTION}, out, client=fake)
    row = _read_lines(out)[0]
    assert not row.get("error") and row["correctness"] == 3
    assert len(fake.calls) == 3


def test_valid_judgment_is_parsed_with_provider_and_usage(tmp_path, mock_record):
    reply = text_resp(_judge_json(correctness=2),
                      usage_dict=usage(prompt=5000, completion=300, cached=4000, reasoning=50, cost=0.0123),
                      provider="StreamLake")
    out = tmp_path / "j.jsonl"
    counts = judge.judge_runs([_ok_run("A")], {"q01": QUESTION}, out, client=FakeOpenRouter([reply]))

    row = _read_lines(out)[0]
    assert "error" not in row
    assert row["judge_model"] == config.JUDGE_MODEL
    assert row["judge_provider"] == "StreamLake"
    assert row["judge_usage"] == {"prompt_tokens": 5000, "cached_tokens": 4000, "cache_write_tokens": 0,
                                  "completion_tokens": 300, "reasoning_tokens": 50, "cost_usd": 0.0123}
    assert row["correctness"] == 2 and row["quality"] == 2 and row["abstention_pass"] is None
    assert row["raw"] == _judge_json(correctness=2)
    assert counts["judged"] == 1 and counts["errors"] == 0


def test_sdk_exception_becomes_error_and_is_retried_only_with_only_missing(tmp_path, mock_record):
    fail = {"on": True}

    def handler(kwargs):
        if fail["on"]:
            return RuntimeError("boom")
        return text_resp(_judge_json(correctness=3))

    fake = FakeOpenRouter(handler)
    runs = [_ok_run("A")]
    out = tmp_path / "j.jsonl"

    judge.judge_runs(runs, {"q01": QUESTION}, out, client=fake)
    row = _read_lines(out)[-1]
    assert row["error"] == "RuntimeError: boom"
    assert row["judge_provider"] is None and row["judge_usage"] is None

    counts = judge.judge_runs(runs, {"q01": QUESTION}, out, client=fake)
    assert len(fake.calls) == 1  # an error line is skipped without --only-missing
    assert counts["skipped_with_errors"] == 1

    fail["on"] = False
    judge.judge_runs(runs, {"q01": QUESTION}, out, client=fake, only_missing=True)
    assert len(fake.calls) == 2
    assert all("error" not in row for row in _read_lines(out))
    assert judge.latest_by_run_id(_read_lines(out))["A"]["quality"] == 3


def test_resume_skips_runs_already_judged(tmp_path, mock_record):
    fake = FakeOpenRouter(lambda kwargs: text_resp(_judge_json(correctness=2)))
    runs = [_ok_run("A"), _ok_run("B"), dict(_ok_run("C"), status="turn_limit", final_text="")]
    out = tmp_path / "j.jsonl"

    first = judge.judge_runs(runs, {"q01": QUESTION}, out, client=fake)
    assert len(fake.calls) == 2
    assert first["judged"] == 2 and first["auto"] == 1 and first["errors"] == 0

    rows = {r["run_id"]: r for r in _read_lines(out)}
    assert rows["C"]["auto"] == "status:turn_limit" and rows["C"]["quality"] == 0
    assert rows["C"]["judge_model"] is None and rows["C"]["judge_usage"] is None
    assert rows["A"]["quality"] == 2 and rows["A"]["judge_model"] == config.JUDGE_MODEL
    assert rows["A"]["raw"] and "required_facts" in rows["A"]

    second = judge.judge_runs(runs, {"q01": QUESTION}, out, client=fake)
    assert len(fake.calls) == 2  # nothing judged twice
    assert second["judged"] == 0 and second["cost_usd"] == 0.0


def test_cost_is_summed_over_judge_calls(tmp_path, mock_record):
    def handler(kwargs):
        cost = 0.02 if "B-ANSWER" in kwargs["messages"][1]["content"] else 0.01
        return text_resp(_judge_json(), usage_dict=usage(cost=cost))

    runs = [_ok_run("A", final_text="A-ANSWER"), _ok_run("B", final_text="B-ANSWER")]
    counts = judge.judge_runs(runs, {"q01": QUESTION}, tmp_path / "j.jsonl", client=FakeOpenRouter(handler))
    assert counts["cost_usd"] == pytest.approx(0.03)
    assert counts["responses_without_cost"] == 0


CAPPED_429 = {"id": "judge-cap", "choices": [],
              "error": {"code": 429, "message": "Rate limit exceeded: free-models-per-day"}}


def test_normal_run_reports_no_daily_cap(tmp_path, mock_record):
    counts = judge.judge_runs([_ok_run("A")], {"q01": QUESTION}, tmp_path / "j.jsonl",
                              client=FakeOpenRouter([text_resp(_judge_json())]))
    assert counts["daily_limit_reached"] is False and counts["to_judge"] == 0


def test_daily_cap_mid_batch_writes_no_line_for_that_run_and_stops(tmp_path, mock_record):
    runs = [_ok_run("A"), _ok_run("B"), _ok_run("C"), _ok_run("D")]
    fake = FakeOpenRouter([text_resp(_judge_json()), llm.DailyLimitReached("per-day cap")])
    out = tmp_path / "j.jsonl"
    counts = judge.judge_runs(runs, {"q01": QUESTION}, out, client=fake, workers=1)

    assert len(fake.calls) == 2  # C and D were never sent
    assert [r["run_id"] for r in _read_lines(out)] == ["A"]  # B, the run that hit the cap, has no line
    assert counts["daily_limit_reached"] is True
    assert counts["judged"] == 1 and counts["errors"] == 0 and counts["to_judge"] == 3


def test_daily_cap_from_the_api_is_raised_at_once_and_writes_nothing(tmp_path, mock_record):
    fake = FakeOpenRouter([CAPPED_429, text_resp(_judge_json())])
    out = tmp_path / "j.jsonl"
    counts = judge.judge_runs([_ok_run("A"), _ok_run("B")], {"q01": QUESTION}, out, client=fake, workers=1)

    assert len(fake.calls) == 1  # no retry against a spent cap
    assert not out.exists()
    assert counts["daily_limit_reached"] is True and counts["judged"] == 0 and counts["to_judge"] == 2


def test_daily_cap_with_parallel_workers_starts_no_more_than_the_pool(tmp_path, mock_record):
    runs = [_ok_run(f"R{i}") for i in range(9)]
    fake = FakeOpenRouter(lambda kwargs: llm.DailyLimitReached("per-day cap"))
    out = tmp_path / "j.jsonl"
    counts = judge.judge_runs(runs, {"q01": QUESTION}, out, client=fake, workers=3)

    assert 1 <= len(fake.calls) <= 3  # the runs in flight when the cap was hit; the rest never started
    assert not out.exists()
    assert counts["daily_limit_reached"] is True and counts["to_judge"] == 9


def test_main_exits_3_on_daily_cap_and_the_next_day_judges_the_rest(tmp_path, monkeypatch, mock_record, capsys):
    runs_path = tmp_path / "runs.jsonl"
    runs_path.write_text("".join(json.dumps(r) + "\n" for r in [_ok_run("A"), _ok_run("B"), _ok_run("C")]),
                         encoding="utf-8")
    questions_path = tmp_path / "questions.json"
    questions_path.write_text(json.dumps([QUESTION]), encoding="utf-8")
    out = tmp_path / "j.jsonl"
    argv = ["--runs", str(runs_path), "--out", str(out), "--questions", str(questions_path), "--workers", "1"]

    monkeypatch.setattr(judge.llm, "make_client",
                        lambda: FakeOpenRouter([text_resp(_judge_json()), llm.DailyLimitReached("per-day cap")]))
    assert judge.main(argv) == 3
    err = capsys.readouterr().err
    assert "Daily free-model request limit reached" in err and "00:00 UTC" in err
    assert "1 run judged, 2 still to judge" in err
    assert [r["run_id"] for r in _read_lines(out)] == ["A"]

    later = FakeOpenRouter(lambda kwargs: text_resp(_judge_json()))  # the next day: same command
    monkeypatch.setattr(judge.llm, "make_client", lambda: later)
    assert judge.main(argv) == 0
    assert len(later.calls) == 2  # B and C only; A was already judged
    assert sorted(judge.latest_by_run_id(_read_lines(out))) == ["A", "B", "C"]


def test_batch_mode_is_rejected(tmp_path, capsys):
    out = tmp_path / "j.jsonl"
    with pytest.raises(SystemExit) as exc:
        judge.main(["--runs", str(tmp_path / "runs.jsonl"), "--out", str(out), "--mode", "batch"])
    assert exc.value.code == 2
    assert "batch mode was removed" in capsys.readouterr().err
    assert not out.exists()


def test_dry_run_writes_valid_placeholder_judgments_without_a_client(tmp_path, monkeypatch, mock_record, capsys):
    def no_real_client():
        raise AssertionError("--dry-run must not build a real API client")

    monkeypatch.setattr(judge.llm, "make_client", no_real_client)
    monkeypatch.delenv(config.OPENROUTER_KEY_ENV, raising=False)
    runs = [_ok_run("A"), _ok_run("B"), dict(_ok_run("C"), status="turn_limit", final_text="")]
    runs_path = tmp_path / "runs.jsonl"
    runs_path.write_text("".join(json.dumps(r) + "\n" for r in runs), encoding="utf-8")
    questions_path = tmp_path / "questions.json"
    questions_path.write_text(json.dumps([QUESTION]), encoding="utf-8")
    out = tmp_path / "dry.jsonl"

    rc = judge.main(["--runs", str(runs_path), "--out", str(out), "--questions", str(questions_path),
                     "--dry-run"])

    assert rc == 0
    assert "DRY RUN" in capsys.readouterr().out
    rows = {r["run_id"]: r for r in _read_lines(out)}
    assert set(rows) == {"A", "B", "C"}
    a = rows["A"]
    assert "error" not in a
    assert a["correctness"] == 3 and a["quality"] == 3
    assert [f["status"] for f in a["required_facts"]] == ["present", "present"]
    assert all(a[field] is False for field in judge.BINARY_FIELDS)
    assert a["judge_provider"] == "dry-run" and a["judge_usage"]["cost_usd"] == 0.0
    assert judge.parse_judgment(a["raw"], QUESTION)["correctness"] == 3
    assert rows["C"]["auto"] == "status:turn_limit"


def test_judge_module_imports_no_vendor_sdk():
    tree = ast.parse(Path(judge.__file__).read_text(encoding="utf-8"))
    imported = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module.split(".")[0])
    assert not imported & {"anthropic", "openai"}


# ---------------------------------------------------------------- human sample and kappa

def _sample_fixture():
    correctness = [3, 2, 0, 1]
    hallucination = [False, True, False, True]
    runs = [_ok_run(rid, final_text=f"Response {i}.") for i, rid in enumerate(
        ["CTX-L1-q01-r1", "EMB-L5-q01-r1", "MD-L3-q01-r2", "ORACLE-L1-q01-r1"])]
    judgments = {}
    for run, c, h in zip(runs, correctness, hallucination):
        judgments[run["run_id"]] = {
            "run_id": run["run_id"], "qid": "q01", "judge_model": config.JUDGE_MODEL,
            "correctness": c, "quality": c, "hallucination": h, "leakage": False,
            "safety_critical_miss": False, "abstained": False, "abstention_pass": None,
        }
    return runs, judgments


def test_human_sample_hides_arm_and_level(tmp_path):
    runs, judgments = _sample_fixture()
    path = tmp_path / "sample.csv"
    n = judge.write_human_sample(runs, judgments, {"q01": QUESTION}, 1.0, 7, path)
    assert n == 4
    with path.open(encoding="utf-8-sig", newline="") as fh:
        reader = csv.DictReader(fh)
        header = reader.fieldnames
        rows = list(reader)
    assert "run_id" not in header and "arm" not in header and "level" not in header
    for row in rows:
        for cell in row.values():
            for leaked in ["CTX", "EMB", "MD", "ORACLE", "L1", "L3", "L5"]:
                assert leaked not in cell, leaked
    assert all(row["correctness"] == "" for row in rows)


def test_kappa_report_matches_sheet_scored_by_the_same_judge(tmp_path, capsys):
    runs, judgments = _sample_fixture()
    sheet = tmp_path / "sample.csv"
    judge.write_human_sample(runs, judgments, {"q01": QUESTION}, 1.0, 7, sheet)
    with sheet.open(encoding="utf-8-sig", newline="") as fh:
        rows = list(csv.DictReader(fh))
    by_blind = {judge.blind_id(rid, 7): j for rid, j in judgments.items()}
    for row in rows:
        j = by_blind[row["blind_id"]]
        row["correctness"] = str(j["correctness"])
        row["hallucination"] = "yes" if j["hallucination"] else "no"
        row["leakage"] = "0"
        row["safety_critical_miss"] = "false"
        row["abstained"] = "no"
    scored = tmp_path / "scored.csv"
    with scored.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)

    report = judge.kappa_report(scored, judgments, seed=7)
    assert report["matched_rows"] == 4
    assert report["correctness"]["kappa"] == pytest.approx(1.0)
    assert report["hallucination"]["kappa"] == pytest.approx(1.0)
    assert "kappa" in capsys.readouterr().out

    with pytest.raises(ValueError):
        judge.kappa_report(scored, judgments, seed=8)  # a different seed matches no rows
