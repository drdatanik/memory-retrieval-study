"""Offline tests for src.gen_distractors and src.size_levels. No API calls are made and nothing is downloaded."""

from __future__ import annotations

import copy
import json
import random
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src import config, llm  # noqa: E402
from src import gen_distractors as gd  # noqa: E402
from src import size_levels as sl  # noqa: E402
from tests.fake_openrouter import FakeOpenRouter, text_resp, usage  # noqa: E402


# --- helpers -----------------------------------------------------------------------------------------
@pytest.fixture(scope="module")
def core_names() -> list[str]:
    return gd.core_patient_names()


@pytest.fixture(scope="module")
def core_norm(core_names) -> frozenset:
    return frozenset(gd.norm_name(n) for n in core_names)


def valid_memories() -> list[dict]:
    """Thirteen memories, authored only by patient, lab and front_desk, so valid for any doctor."""
    return copy.deepcopy(gd.DRY_RUN_MEMORIES)


def memories_resp(memories=None, **kwargs):
    """A generator response (OpenRouter chat completion) whose text is the memories JSON."""
    payload = valid_memories() if memories is None else memories
    return text_resp(json.dumps({"memories": payload}), **kwargs)


# --- skeletons -----------------------------------------------------------------------------------------
def test_skeleton_is_deterministic_and_index_sensitive(core_norm):
    a = gd.draw_skeleton(7, core_norm)
    assert a == gd.draw_skeleton(7, core_norm)
    assert a != gd.draw_skeleton(8, core_norm)


def test_skeleton_fields_are_in_range(core_norm):
    for i in range(1, 301):
        s = gd.draw_skeleton(i, core_norm)
        assert s["patient_id"] == f"X{i:04d}"
        assert gd.AGE_RANGE[0] <= s["age"] <= gd.AGE_RANGE[1]
        assert s["sex"] in ("F", "M")
        assert 12 <= s["n_memories"] <= 18
        names = [c["name"] for c in s["conditions"]]
        assert 1 <= len(names) <= 3 and len(set(names)) == len(names)
        assert s["primary_doctor"] in config.DISTRACTOR_DOCTORS
        for c in s["conditions"]:
            assert c.get("sex") in (None, s["sex"])
            assert s["age"] >= c.get("min_age", 0)


def test_round_robin_over_distractor_doctors(core_norm):
    docs = [gd.draw_skeleton(i, core_norm)["primary_doctor"] for i in range(1, 9)]
    assert docs == ["D03", "D04", "D05", "D06", "D03", "D04", "D05", "D06"]


def test_name_lists_and_catalogue_are_big_enough():
    males = sum(len(v["M"]) for v in gd.FIRST_NAMES.values())
    females = sum(len(v["F"]) for v in gd.FIRST_NAMES.values())
    surnames = {s for v in gd.SURNAMES.values() for s in v}
    assert males >= 60 and females >= 60
    assert len(surnames) >= 120
    assert set(gd.FIRST_NAMES) == set(gd.SURNAMES)
    assert len(gd.CONDITIONS) >= 30
    for c in gd.CONDITIONS:
        assert c["meds"] and c["tests"]


def test_core_names_are_read_from_roster(core_names):
    assert len(core_names) == 20
    assert "Sunita Rao" in core_names
    assert "Joseph D'Souza" in core_names


def test_no_distractor_name_matches_a_core_patient(core_norm):
    for i in range(1, 601):
        name = gd.draw_skeleton(i, core_norm)["name"]
        assert gd.norm_name(name) not in core_norm, name


def test_clashing_name_is_redrawn(core_norm):
    first = gd.draw_skeleton(3, frozenset())["name"]
    redrawn = gd.draw_skeleton(3, frozenset({gd.norm_name(first)}))
    assert redrawn["name"] != first
    assert gd.norm_name(redrawn["name"]) != gd.norm_name(first)
    # Deterministic: the same clash gives the same redraw.
    assert redrawn == gd.draw_skeleton(3, frozenset({gd.norm_name(first)}))


# --- validator -----------------------------------------------------------------------------------------
def test_valid_memories_pass(core_names):
    assert gd.validate_memories(valid_memories(), "D03", core_names) == []


def test_window_boundaries_are_inclusive(core_names):
    mems = valid_memories()
    mems[0]["date"] = config.WINDOW_START
    mems[-1]["date"] = config.WINDOW_END
    assert gd.validate_memories(mems, "D03", core_names) == []


@pytest.mark.parametrize("bad_date", ["2025-03-31", "2026-10-12", "2025-13-01", "2025-4-1", "yesterday", None])
def test_dates_outside_the_window_are_rejected(core_names, bad_date):
    mems = valid_memories()
    mems[2]["date"] = bad_date
    problems = gd.validate_memories(mems, "D03", core_names)
    assert any("outside the window" in p for p in problems), problems


@pytest.mark.parametrize("bad_type", ["doctor_preference", "note", "", None])
def test_disallowed_types_are_rejected(core_names, bad_type):
    mems = valid_memories()
    mems[1]["type"] = bad_type
    assert any("type" in p for p in gd.validate_memories(mems, "D03", core_names))


def test_authors_must_be_the_patients_doctor_or_a_patient_side_author(core_names):
    mems = valid_memories()
    mems[0]["author"] = "D03"
    mems[0]["type"] = "visit_note"
    assert gd.validate_memories(mems, "D03", core_names) == []
    assert any("author" in p for p in gd.validate_memories(mems, "D04", core_names))
    mems[0]["author"] = "D01"
    assert any("author" in p for p in gd.validate_memories(mems, "D03", core_names))


@pytest.mark.parametrize("text", [
    "Dr Anjali Mehta reviewed the labs today.",
    "reviewed by anjali mehta at the desk",
    "Advice from Dr Rohan Iyer on 2025-06-01",
    "Referred to Dr. Mehta for a second opinion",
    "Dr Iyer's cardiology note follows",
    "Continue the plan in D01's note from April",
    "as D02 advised previously",
    "Same as P01 in the core panel",
    "She is the sister of P12",
    "Sunita Rao asked the same question",
    "joseph d'souza also had the same reaction",
])
def test_hard_rules_reject_forbidden_references(core_names, text):
    mems = valid_memories()
    mems[4]["text"] = text
    problems = gd.validate_memories(mems, "D03", core_names)
    assert any("memory 5" in p for p in problems), problems


@pytest.mark.parametrize("text", [
    "Referred to Dr Priya Natarajan (D03) for a review.",
    "Dr Karthik Hegde (D04) changed the dose on 2026-02-01.",
    "Front desk reference X0002 for the same family visit.",
    "Doctor-reported ECG, sinus rhythm, no ischaemic change.",
])
def test_allowed_references_pass(core_names, text):
    mems = valid_memories()
    mems[4]["text"] = text
    assert gd.text_violations(text, core_names) == []
    assert gd.validate_memories(mems, "D03", core_names) == []


def test_memory_count_band_is_enforced(core_names):
    base = valid_memories()
    eleven = (base * 2)[:11]
    nineteen = (base * 2)[:19]
    twelve = (base * 2)[:12]
    eighteen = (base * 2)[:18]
    assert any("expected 12 to 18" in p for p in gd.validate_memories(eleven, "D03", core_names))
    assert any("expected 12 to 18" in p for p in gd.validate_memories(nineteen, "D03", core_names))
    assert gd.validate_memories(twelve, "D03", core_names) == []
    assert gd.validate_memories(eighteen, "D03", core_names) == []


def test_empty_text_and_non_list_are_rejected(core_names):
    mems = valid_memories()
    mems[3]["text"] = "   "
    assert any("empty text" in p for p in gd.validate_memories(mems, "D03", core_names))
    assert gd.validate_memories({"memories": mems}, "D03", core_names) == ["memories is not a list"]


def test_finalize_sorts_by_date_and_assigns_ids():
    mems = [
        {"date": "2026-01-01", "type": "lab_result", "author": "lab", "text": " later "},
        {"date": "2025-05-01", "type": "appointment", "author": "front_desk", "text": "first"},
        {"date": "2025-05-01", "type": "patient_context", "author": "patient", "text": "tie second"},
    ]
    out = gd.finalize_memories("X0009", mems)
    assert [m["memory_id"] for m in out] == ["X0009-m01", "X0009-m02", "X0009-m03"]
    assert [m["date"] for m in out] == ["2025-05-01", "2025-05-01", "2026-01-01"]
    assert out[0]["text"] == "first" and out[2]["text"] == "later"
    assert out[1]["type"] == "patient_context"  # stable on ties


def test_parse_memories_reads_the_json_and_rejects_bad_output():
    assert len(gd.parse_memories(json.dumps({"memories": valid_memories()}))) == 13
    with pytest.raises(ValueError):
        gd.parse_memories("not json")
    with pytest.raises(ValueError):
        gd.parse_memories(json.dumps({"rows": []}))
    with pytest.raises(ValueError):
        gd.parse_memories(json.dumps([1, 2]))


# --- generation through a fake OpenRouter client ------------------------------------------------------
def test_request_shape_is_as_specified(tmp_path):
    ctx = gd.make_context(tmp_path)
    fake = FakeOpenRouter([memories_resp()])
    res = gd.generate_patient(fake, 1, ctx)
    assert res.status == "written"
    kw = fake.calls[0]
    cfg = config.ROLES["generator"]
    assert kw["model"] == cfg["model"]
    assert kw["temperature"] == cfg["temperature"]
    assert kw["max_tokens"] == cfg["max_tokens"] == 12000
    assert kw["extra_body"] == {"provider": cfg["provider"], "reasoning": cfg["reasoning"]}
    assert kw["response_format"] == llm.json_schema_format("memories", gd.MEMORIES_SCHEMA)
    assert kw["response_format"]["json_schema"]["strict"] is True
    assert "tools" not in kw
    system, user = kw["messages"]
    assert system["role"] == "system" and user["role"] == "user"
    assert gd.GEN_SYSTEM in system["content"]
    assert "Dr Priya Natarajan" in system["content"]  # D03's style block
    assert "Treating doctor: Dr Priya Natarajan (D03)" in user["content"]


def test_schema_matches_spec():
    schema = gd.MEMORIES_SCHEMA
    item = schema["properties"]["memories"]["items"]
    assert schema["required"] == ["memories"] and schema["additionalProperties"] is False
    assert item["required"] == ["date", "type", "author", "text"]
    assert item["additionalProperties"] is False
    assert set(item["properties"]["type"]["enum"]) == set(gd.MEMORY_TYPES)
    assert "doctor_preference" not in gd.MEMORY_TYPES


def test_retries_until_output_is_valid(tmp_path):
    ctx = gd.make_context(tmp_path)
    bad = valid_memories()
    bad[0]["text"] = "Dr Anjali Mehta advised"
    fake = FakeOpenRouter([
        text_resp("not json at all"),
        memories_resp(bad),
        memories_resp(),
    ])
    res = gd.generate_patient(fake, 1, ctx)
    assert res.status == "written"
    assert res.attempts == 3
    assert len(res.turns) == 3
    assert res.record["memories"][0]["memory_id"] == "X0001-m01"


def test_gives_up_after_three_retries(tmp_path):
    ctx = gd.make_context(tmp_path)
    bad = valid_memories()
    bad[2]["text"] = "Linked to D01 notes"
    fake = FakeOpenRouter(lambda kwargs: memories_resp(bad))
    res = gd.generate_patient(fake, 1, ctx)
    assert res.status == "failed"
    assert res.record is None
    assert res.attempts == 4  # one try plus three retries
    assert len(fake.calls) == 4
    assert "core doctor ID" in res.problem or "mentions" in res.problem


def test_api_errors_count_as_attempts(tmp_path):
    ctx = gd.make_context(tmp_path)
    fake = FakeOpenRouter([RuntimeError("overloaded")] * 4)
    res = gd.generate_patient(fake, 2, ctx)
    assert res.status == "failed"
    assert res.attempts == 4
    assert res.turns == []
    assert "API error" in res.problem


def test_provider_error_with_no_choices_is_retried(tmp_path):
    ctx = gd.make_context(tmp_path)
    no_choice = {"id": "x", "choices": [], "error": {"message": "provider down"}}
    fake = FakeOpenRouter([no_choice, memories_resp()])
    res = gd.generate_patient(fake, 1, ctx)
    assert res.status == "written" and res.attempts == 2
    assert len(res.turns) == 1  # the failed call returned no usage


def test_content_filter_and_empty_text_are_failed_attempts(tmp_path):
    ctx = gd.make_context(tmp_path)
    fake = FakeOpenRouter([
        text_resp("", finish_reason="content_filter"),
        text_resp("   ", finish_reason="stop"),
        memories_resp(),
    ])
    res = gd.generate_patient(fake, 1, ctx)
    assert res.status == "written" and res.attempts == 3
    assert len(res.turns) == 3  # billed attempts are still counted


def test_content_filter_and_empty_text_reasons_are_recorded(tmp_path):
    ctx = gd.make_context(tmp_path)
    res = gd.generate_patient(FakeOpenRouter(lambda kw: text_resp("", finish_reason="content_filter")), 1, ctx)
    assert res.status == "failed" and res.problem == "content_filter finish"
    res = gd.generate_patient(FakeOpenRouter(lambda kw: text_resp("", finish_reason="stop")), 1, ctx)
    assert res.status == "failed" and res.problem.startswith("empty text")


def test_record_schema_and_validity(tmp_path, core_names):
    ctx = gd.make_context(tmp_path)
    res = gd.generate_patient(FakeOpenRouter([memories_resp()]), 4, ctx)
    rec = res.record
    assert set(rec) == {"patient_id", "name", "age", "sex", "primary_doctor", "conditions", "memories"}
    assert rec["patient_id"] == "X0004" and rec["primary_doctor"] == "D06"
    assert all(set(m) == {"memory_id", "date", "type", "author", "text"} for m in rec["memories"])
    assert gd.validate_memories(rec["memories"], rec["primary_doctor"], core_names) == []


def test_valid_response_writes_a_file(tmp_path):
    summary = gd.generate_all(FakeOpenRouter([memories_resp()]), [4], tmp_path, workers=1)
    assert summary.written == ["X0004"] and summary.failed == []
    rec = json.loads((tmp_path / "X0004.json").read_text(encoding="utf-8"))
    assert rec["patient_id"] == "X0004" and rec["primary_doctor"] == "D06"
    assert [m["memory_id"] for m in rec["memories"]][:2] == ["X0004-m01", "X0004-m02"]
    assert not (tmp_path / "X0004.json.tmp").exists()


def test_dry_run_client_returns_a_valid_payload(core_names):
    r = llm.chat(gd.dry_run_client(), "generator",
                 [{"role": "system", "content": gd.GEN_SYSTEM}, {"role": "user", "content": "u"}],
                 response_format=llm.json_schema_format("memories", gd.MEMORIES_SCHEMA))
    assert r.finish_reason == "stop"
    assert gd.validate_memories(json.loads(r.text)["memories"], "D03", core_names) == []


def test_generate_all_is_resumable(tmp_path):
    first = gd.generate_all(gd.dry_run_client(), [1, 2, 3], tmp_path, workers=2)
    assert first.written == ["X0001", "X0002", "X0003"]
    assert first.failed == []
    assert all((tmp_path / f"{pid}.json").exists() for pid in first.written)
    rec = json.loads((tmp_path / "X0002.json").read_text(encoding="utf-8"))
    assert rec["patient_id"] == "X0002"

    second = gd.generate_all(gd.dry_run_client(), [1, 2, 3, 4], tmp_path, workers=2)
    assert second.skipped == ["X0001", "X0002", "X0003"]
    assert second.written == ["X0004"]


def test_failed_patients_are_logged_and_skipped(tmp_path, monkeypatch):
    def fake_generate(client, index, ctx):
        if index == 2:
            return gd.PatientResult("X0002", "failed", None, [], 4, "bad output: test")
        return gd.PatientResult(f"X{index:04d}", "written", gd.build_record(
            gd.draw_skeleton(index, ctx.core_norm), gd.finalize_memories(f"X{index:04d}", valid_memories())),
            [], 1, None)

    monkeypatch.setattr(gd, "generate_patient", fake_generate)
    summary = gd.generate_all(None, [1, 2, 3], tmp_path, workers=1)
    assert summary.failed == [("X0002", "bad output: test")]
    assert sorted(summary.written) == ["X0001", "X0003"]
    assert not (tmp_path / "X0002.json").exists()


# --- daily free-model cap -------------------------------------------------------------------------------
CAPPED_429 = {"id": "gen-cap", "choices": [],
              "error": {"code": 429, "message": "Rate limit exceeded: free-models-per-day"}}


def test_normal_run_reports_no_daily_cap(tmp_path):
    summary = gd.generate_all(gd.dry_run_client(), [1, 2], tmp_path, workers=1)
    assert summary.daily_limit_hit is False and summary.pending == []


def test_daily_cap_mid_batch_writes_nothing_for_that_patient_and_stops(tmp_path):
    fake = FakeOpenRouter([memories_resp(), llm.DailyLimitReached("per-day cap")])
    summary = gd.generate_all(fake, [1, 2, 3], tmp_path, workers=1)
    assert summary.written == ["X0001"]
    assert summary.failed == []  # the cap is not a failed patient
    assert summary.daily_limit_hit is True
    assert summary.pending == ["X0002", "X0003"]
    assert len(fake.calls) == 2  # X0003 never reached the API
    assert sorted(p.name for p in tmp_path.glob("*.json")) == ["X0001.json"]


def test_daily_cap_from_the_api_is_raised_at_once_and_not_retried(tmp_path):
    ctx = gd.make_context(tmp_path)
    fake = FakeOpenRouter([CAPPED_429, memories_resp()])
    with pytest.raises(llm.DailyLimitReached):
        gd.generate_patient(fake, 1, ctx)
    assert len(fake.calls) == 1  # no retry against a spent cap


def test_daily_cap_with_parallel_workers_starts_no_more_than_the_pool(tmp_path):
    fake = FakeOpenRouter(lambda kwargs: llm.DailyLimitReached("per-day cap"))
    summary = gd.generate_all(fake, list(range(1, 10)), tmp_path, workers=3)
    assert summary.daily_limit_hit is True
    assert summary.written == [] and summary.failed == []
    assert 1 <= len(fake.calls) <= 3  # the patients in flight when the cap was hit; the rest never started
    assert summary.pending == [f"X{i:04d}" for i in range(1, 10)]
    assert list(tmp_path.glob("*.json")) == []


def test_main_exits_3_on_daily_cap_and_the_next_day_resumes(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(gd, "DISTRACTOR_DIR", tmp_path / "distractors")
    monkeypatch.setattr(gd.llm, "make_client",
                        lambda: FakeOpenRouter([memories_resp(), llm.DailyLimitReached("per-day cap")]))
    argv = ["--n", "3", "--workers", "1", "--start", "1"]
    assert gd.main(argv) == 3
    err = capsys.readouterr().err
    assert "Daily free-model request limit reached" in err
    assert "2 still to generate" in err and "00:00 UTC" in err
    assert sorted(p.name for p in (tmp_path / "distractors").glob("*.json")) == ["X0001.json"]

    monkeypatch.setattr(gd.llm, "make_client", gd.dry_run_client)  # the next day: same command
    assert gd.main(argv) == 0
    assert sorted(p.name for p in (tmp_path / "distractors").glob("*.json")) == [
        "X0001.json", "X0002.json", "X0003.json"]


# --- usage and cost ---------------------------------------------------------------------------------------
def test_usage_totals_split_cached_input_and_sum_output():
    turns = [
        {"prompt_tokens": 50_000, "cached_tokens": 48_000, "cache_write_tokens": 0,
         "completion_tokens": 300, "reasoning_tokens": 120, "cost_usd": 0.0123},
        {"prompt_tokens": 1_000, "cached_tokens": 0, "cache_write_tokens": 0,
         "completion_tokens": 20, "reasoning_tokens": 5, "cost_usd": None},
    ]
    t = gd.usage_totals(turns)
    assert t["input_tokens"] == 2_000 + 1_000
    assert t["cache_read_tokens"] == 48_000
    assert t["output_tokens"] == 320
    assert t["reasoning_tokens"] == 125
    assert t["cost_usd"] == pytest.approx(0.0123)
    assert t["cost_reported"] == 1


def test_usage_totals_tolerate_missing_fields_and_empty_runs():
    t = gd.usage_totals([{"prompt_tokens": None, "cached_tokens": None, "completion_tokens": None,
                          "reasoning_tokens": None, "cost_usd": None}])
    assert t == {"input_tokens": 0, "cache_read_tokens": 0, "output_tokens": 0,
                 "reasoning_tokens": 0, "cost_usd": 0.0, "cost_reported": 0}
    empty = gd.usage_totals([])
    assert empty["input_tokens"] == 0 and empty["cost_usd"] == 0.0 and empty["cost_reported"] == 0


def test_cost_is_summed_over_the_calls_that_report_it(tmp_path, capsys):
    costs = [0.0100, None, 0.0250]
    fake = FakeOpenRouter([memories_resp(usage_dict=usage(cost=c)) for c in costs])
    summary = gd.generate_all(fake, [1, 2, 3], tmp_path, workers=1)
    assert len(summary.turns) == 3
    t = gd.usage_totals(summary.turns)
    assert t["cost_usd"] == pytest.approx(0.0350)
    assert t["cost_reported"] == 2
    capsys.readouterr()
    gd.print_summary(summary, dry_run=False)
    out = capsys.readouterr().out
    assert "$0.0350" in out
    assert "summed over 2 of 3 calls" in out


def test_no_direct_sdk_imports_in_the_builders():
    for mod in (gd, sl):
        text = Path(mod.__file__).read_text(encoding="utf-8")
        for banned in ("import anthropic", "from anthropic", "import openai", "from openai"):
            assert banned not in text, (mod.__name__, banned)


# --- size_levels ----------------------------------------------------------------------------------------
def monotone_counter(seed: int, n_max: int, base: int = 30_000):
    rng = random.Random(seed)
    sizes = [base]
    for _ in range(n_max):
        sizes.append(sizes[-1] + rng.randint(0, 2_000))
    return lambda n: sizes[n]


@pytest.mark.parametrize("seed", range(8))
def test_binary_search_matches_brute_force(seed):
    n_max = 250
    fn = monotone_counter(seed, n_max)
    for target in (35_000, 95_000, 200_000, 400_000, 800_000):
        sizer = sl.Sizer(fn)
        if fn(0) > target:
            with pytest.raises(ValueError):
                sl.largest_n_within(sizer, target, n_max)
            continue
        expected = max(n for n in range(n_max + 1) if fn(n) <= target)
        assert sl.largest_n_within(sizer, target, n_max) == expected


def test_each_size_is_counted_at_most_once():
    calls: dict[int, int] = {}
    fn = monotone_counter(3, 600, base=10_000)

    def counted(n):
        calls[n] = calls.get(n, 0) + 1
        return fn(n)

    sizer = sl.Sizer(counted)
    targets = {"L1": None, "L2": 95_000, "L3": 200_000, "L4": 400_000, "L5": 800_000}
    chosen = sl.choose_levels(sizer, 600, targets)
    assert chosen["L1"] == 0
    assert set(calls.values()) == {1}
    assert len(calls) == len(sizer.cache)
    # Re-asking for any size does not count again.
    sl.levels_payload([f"X{i:04d}" for i in range(1, 601)], chosen, sizer)
    assert set(calls.values()) == {1}


def test_search_is_logarithmic_in_the_number_of_distractors():
    sizer = sl.Sizer(monotone_counter(11, 600, base=10_000))
    sl.largest_n_within(sizer, 400_000, 600)
    assert len(sizer.cache) <= 12  # about log2(600) + 2


def test_target_above_full_store_is_capped_at_n_max():
    sizer = sl.Sizer(monotone_counter(5, 40))
    assert sl.largest_n_within(sizer, 10**9, 40) == 40


def test_core_above_target_is_an_error():
    sizer = sl.Sizer(lambda n: 50_000 + n)
    with pytest.raises(ValueError):
        sl.largest_n_within(sizer, 35_000, 10)


def test_levels_payload_is_nested_prefixes():
    ids = [f"X{i:04d}" for i in range(1, 51)]
    sizer = sl.Sizer(lambda n: 1_000 + 100 * n)
    chosen = sl.choose_levels(sizer, 50, {"L1": None, "L2": 2_000, "L3": 3_500, "L4": 4_000, "L5": 99_999})
    payload = sl.levels_payload(ids, chosen, sizer)
    assert list(payload) == ["L1", "L2", "L3", "L4", "L5"]
    assert payload["L1"] == {"distractors": [], "ctx_tokens": 1_000}
    prev: list[str] = []
    for level in payload:
        cur = payload[level]["distractors"]
        assert cur[:len(prev)] == prev  # each level extends the one before it
        assert payload[level]["ctx_tokens"] == sizer(len(cur))
        prev = cur
    assert payload["L2"]["distractors"] == ids[:10]
    assert payload["L5"]["distractors"] == ids  # capped at all 50


def test_format_table_has_header_and_rows():
    table = sl.format_table([("L1", "-", 0, 300, 35000), ("L2", 95000, 40, 900, 94800)])
    lines = table.splitlines()
    assert lines[0].split()[:3] == ["level", "target", "distractors"]
    assert lines[2].split()[0] == "L2"


class WordTokenizer:
    """Stands in for the subject tokenizer: one token per whitespace-separated word. Offline."""

    def encode(self, text):
        return SimpleNamespace(ids=text.split())


class RecordingTokenizer(WordTokenizer):
    def __init__(self):
        self.seen: list[str] = []

    def encode(self, text):
        self.seen.append(text)
        return super().encode(text)


def test_count_tokens_is_the_number_of_ids():
    assert sl.count_tokens(WordTokenizer(), "a b c") == 3


def test_live_counter_counts_the_system_text_the_model_receives(monkeypatch):
    blocks = [{"type": "text", "text": "one"},
              {"type": "text", "text": "two three", "cache_control": {"type": "ephemeral"}}]
    monkeypatch.setattr(sl, "ctx_system", lambda world: blocks)
    tok = RecordingTokenizer()
    count = sl.make_counter(SimpleNamespace(world=lambda n: {"n": n}), dry_run=False, tokenizer=tok)
    # llm.system_text joins the blocks with no separator, so "one" and "two" run together.
    assert count(0) == 2
    assert tok.seen == [llm.system_text(blocks)] == ["onetwo three"]


def test_dry_run_counter_is_chars_over_3_24_and_loads_no_tokenizer(monkeypatch):
    monkeypatch.setattr(sl, "ctx_system", lambda world: [{"type": "text", "text": "x" * 33}])
    monkeypatch.setattr(sl, "load_tokenizer", lambda: pytest.fail("a dry run must not load the tokenizer"))
    count = sl.make_counter(SimpleNamespace(world=lambda n: {}), dry_run=True)
    assert count(0) == int(33 / 3.24) == 10


def test_load_tokenizer_reads_the_local_cache_only(tmp_path, monkeypatch):
    import huggingface_hub
    from tokenizers import Tokenizer, models, pre_tokenizers

    tiny = Tokenizer(models.WordLevel({"a": 0, "b": 1, "[UNK]": 2}, unk_token="[UNK]"))
    tiny.pre_tokenizer = pre_tokenizers.WhitespaceSplit()
    path = tmp_path / "tokenizer.json"
    tiny.save(str(path))
    seen: dict = {}

    def fake_download(repo_id, filename, **kwargs):
        seen.update(repo_id=repo_id, filename=filename, **kwargs)
        return str(path)

    monkeypatch.setattr(huggingface_hub, "hf_hub_download", fake_download)
    tok = sl.load_tokenizer()
    assert seen["repo_id"] == config.SUBJECT_TOKENIZER_REPO
    assert seen["filename"] == "tokenizer.json"
    assert seen["local_files_only"] is True
    assert sl.count_tokens(tok, "a b a") == 3


@pytest.fixture(scope="module")
def cached_subject_tokenizer():
    """The subject tokenizer from runs/hf_cache. The tests that use it skip when it is not there."""
    try:
        return sl.load_tokenizer()
    except Exception as exc:  # not in runs/hf_cache on this machine
        pytest.skip(f"subject tokenizer is not in the local cache: {exc!r}")


def test_real_subject_tokenizer_counts_when_it_is_cached(cached_subject_tokenizer):
    # A plausible range, not an exact count: exact counts differ between tokenizers (the subject repo has
    # changed once already; DeepSeek gives 12 for this sentence, the current Inkling tokenizer gives 11).
    sentence = "Hello world, this is a test of the tokenizer."
    n = sl.count_tokens(cached_subject_tokenizer, sentence)
    assert 8 <= n <= 20
    assert sl.count_tokens(cached_subject_tokenizer, sentence + " " + sentence) > n


def test_core_ctx_prompt_size_is_plausible_when_tokenizer_is_cached(cached_subject_tokenizer):
    # L1 is the core world alone (about 41k tokens with the subject tokenizer; see config.LEVEL_TARGETS).
    # The band survives a tokenizer change but catches a wrong world, prompt or unit.
    counter = sl.make_counter(sl.WorldLoader([]), dry_run=False, tokenizer=cached_subject_tokenizer)
    assert 35_000 <= counter(0) <= 50_000


# --- end-to-end wiring (stubs for modules written by other builders) ----------------------------------
def _fake_world_module(core_patients, core_doctors):
    import types

    mod = types.ModuleType("src.world")
    mod.load_core = lambda: (core_patients, core_doctors)

    def load_distractors(ids):
        out = []
        for pid in ids:
            out.append(json.loads((sl.DISTRACTOR_DIR / f"{pid}.json").read_text(encoding="utf-8")))
        return out

    mod.load_distractors = load_distractors
    return mod


def _patch_world(monkeypatch, tmp_path, dist_dir):
    core_patients = [{"patient_id": f"P{i:02d}", "memories": [{"memory_id": f"P{i:02d}-m01"}]}
                     for i in range(1, 4)]
    core_doctors = [json.loads((ROOT / "world" / "core" / "D01.json").read_text(encoding="utf-8"))]
    monkeypatch.setattr(sl, "world_mod", _fake_world_module(core_patients, core_doctors))
    monkeypatch.setattr(sl, "DISTRACTOR_DIR", dist_dir)
    levels_path = tmp_path / "levels.json"
    monkeypatch.setattr(sl, "LEVELS_PATH", levels_path)
    monkeypatch.setattr(sl, "ctx_system", lambda world: [{"type": "text", "text": "\n".join(
        json.dumps(p) for p in world["patients"] + world["doctors"])}])
    return levels_path


def test_main_dry_run_prints_table_without_writing_levels(tmp_path, monkeypatch, capsys):
    dist_dir = tmp_path / "distractors"
    gd.generate_all(gd.dry_run_client(), list(range(1, 9)), dist_dir, workers=2)
    levels_path = _patch_world(monkeypatch, tmp_path, dist_dir)
    ids = sorted(p.stem for p in dist_dir.glob("X*.json"))
    dry = sl.make_counter(sl.WorldLoader(ids), dry_run=True)
    monkeypatch.setattr(config, "LEVEL_TARGETS", {"L1": None, "L2": dry(2), "L3": dry(5)})

    assert sl.main(["--dry-run"]) == 0
    out = capsys.readouterr().out
    rows = {line.split()[0]: line.split() for line in out.splitlines() if line.split()[:1] in (["L1"], ["L2"], ["L3"])}
    assert "level" in out
    assert rows["L2"][2] == "2" and rows["L3"][2] == "5"
    assert not levels_path.exists()


def test_main_dry_run_with_no_distractors_sizes_only_l1(tmp_path, monkeypatch, capsys):
    dist_dir = tmp_path / "distractors"
    dist_dir.mkdir()
    levels_path = _patch_world(monkeypatch, tmp_path, dist_dir)
    assert sl.main(["--dry-run"]) == 0
    captured = capsys.readouterr()
    labels = [line.split()[0] for line in captured.out.splitlines() if line.strip()]
    assert labels[:2] == ["level", "L1"]
    assert "L2" not in captured.out
    assert "only L1" in captured.err
    assert not levels_path.exists()


def test_main_live_run_needs_distractors_first(tmp_path, monkeypatch, capsys):
    dist_dir = tmp_path / "distractors"
    dist_dir.mkdir()
    levels_path = _patch_world(monkeypatch, tmp_path, dist_dir)
    assert sl.main([]) == 1
    assert "gen_distractors" in capsys.readouterr().err
    assert not levels_path.exists()


def test_main_writes_nested_levels_json_from_the_tokenizer(tmp_path, monkeypatch):
    dist_dir = tmp_path / "distractors"
    gd.generate_all(gd.dry_run_client(), list(range(1, 9)), dist_dir, workers=2)
    levels_path = _patch_world(monkeypatch, tmp_path, dist_dir)
    ids = sorted(p.stem for p in dist_dir.glob("X*.json"))
    live = sl.make_counter(sl.WorldLoader(ids), dry_run=False, tokenizer=WordTokenizer())
    monkeypatch.setattr(config, "LEVEL_TARGETS", {"L1": None, "L2": live(2), "L3": live(5)})
    monkeypatch.setattr(sl, "load_tokenizer", lambda: WordTokenizer())  # no download in tests

    assert sl.main([]) == 0

    payload = json.loads(levels_path.read_text(encoding="utf-8"))
    assert list(payload) == ["L1", "L2", "L3"]
    assert payload["L1"] == {"distractors": [], "ctx_tokens": live(0)}
    assert payload["L2"]["distractors"] == ids[:2]
    assert payload["L3"]["distractors"] == ids[:5]  # nested: L3 extends L2
    for level in ("L2", "L3"):
        target = config.LEVEL_TARGETS[level]
        assert payload[level]["ctx_tokens"] == live(len(payload[level]["distractors"])) <= target
    assert live(6) > config.LEVEL_TARGETS["L3"]  # one more distractor would not fit


def test_generator_main_dry_run_writes_only_to_the_dry_run_dir(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(gd, "DRY_RUN_DIR", tmp_path / "dry")
    assert gd.main(["--n", "4", "--workers", "2", "--start", "10", "--dry-run"]) == 0
    assert sorted(p.name for p in (tmp_path / "dry").glob("*.json")) == [
        "X0010.json", "X0011.json", "X0012.json", "X0013.json"]
    assert "DRY RUN" in capsys.readouterr().out


def test_console_text_is_ascii_safe():
    out = gd._ascii("memory 3: date '2025–04–01' outside the window")
    assert out.isascii() and "outside the window" in out


def test_failed_patient_reason_with_unicode_does_not_crash_the_run(tmp_path, monkeypatch, capsys):
    def fake_generate(client, index, ctx):
        return gd.PatientResult("X0001", "failed", None, [], 4, "memory 1: type 'nóte' not allowed")

    monkeypatch.setattr(gd, "generate_patient", fake_generate)
    summary = gd.generate_all(None, [1], tmp_path, workers=1)
    assert summary.failed and "not allowed" in summary.failed[0][1]
    assert "FAILED" in capsys.readouterr().err
