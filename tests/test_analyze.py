"""Tests for src/analyze.py, on synthetic runs and judgments.

The synthetic study has 3 arms (CTX, EMB, MD) x 3 levels x 6 questions x 2 repeats. EMB is 1 point
below CTX at L1 and L2 and 1 point above at L3, so the planted crossover lies between L2 and L3.
"""

from __future__ import annotations

import json
import math
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest
from scipy.stats import wilcoxon

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src import analyze  # noqa: E402

SUBJECT_MODEL = "deepseek/deepseek-v4.1-flash"
JUDGE_MODEL = "deepseek/deepseek-v4-pro"
LEVELS = {"L1": 35_000, "L2": 95_000, "L3": 200_000}
LEVELS_JSON = {lvl: {"distractors": [], "ctx_tokens": tokens} for lvl, tokens in LEVELS.items()}

# Quality per question (q01..q06) for each (arm, level). Repeats are identical.
QUALITY = {
    ("CTX", "L1"): [3, 3, 3, 3, 2, 2],
    ("CTX", "L2"): [2, 2, 2, 2, 1, 1],
    ("CTX", "L3"): [1, 1, 1, 1, 0, 0],
    ("EMB", "L1"): [2, 2, 2, 2, 1, 1],   # d = -1 on every question
    ("EMB", "L2"): [1, 1, 1, 1, 0, 0],   # d = -1
    ("EMB", "L3"): [2, 2, 2, 2, 1, 1],   # d = +1
    ("MD", "L1"): [3, 3, 3, 3, 2, 2],    # d = 0
    ("MD", "L2"): [2, 2, 2, 2, 1, 1],    # d = 0
    ("MD", "L3"): [1, 1, 1, 1, 0, 0],    # d = 0
}
NO_COST_REASON = "no cost values logged for this arm or CTX"


def _questions() -> dict[str, dict]:
    questions = {}
    for i in range(1, 7):
        qid = f"q{i:02d}"
        use_case = (i - 1) % 3 + 1
        questions[qid] = {
            "qid": qid,
            "use_case": use_case,
            "use_case_name": f"UC{use_case}",
            "asker_role": "doctor",
            "asker_id": "D01",
            "asker_name": "Dr Test",
            "target_patient_ids": ["P01"],
            "question": f"Question {qid}?",
            "gold_answer": "gold",
            "required_facts": [{"fact": "fact", "memory_ids": [] if i == 6 else [f"P01-m{i:02d}"]}],
            "forbidden_facts": [],
            "abstention": i == 6,
            "safety_critical": False,
            "pilot": False,
        }
    return questions


def _runs(emb_input: int = 5_000, actual: bool = False) -> list[dict]:
    """Synthetic runs. With `actual=True` each turn logs cost_usd = input_tokens x $1/MTok, which is the
    same as _stub_cost, so the provider-reported crossover equals the stub uncached crossover."""
    runs = []
    questions = _questions()
    for arm in ("CTX", "EMB", "MD"):
        for level, store in LEVELS.items():
            for i in range(1, 7):
                qid = f"q{i:02d}"
                gold = questions[qid]["required_facts"][0]["memory_ids"]
                for repeat in (1, 2):
                    status = "turn_limit" if (arm, level, i, repeat) == ("EMB", "L2", 5, 2) else "ok"
                    input_tokens = store if arm == "CTX" else emb_input
                    runs.append({
                        "run_id": f"{arm}-{level}-{qid}-r{repeat}",
                        "arm": arm,
                        "level": level,
                        "qid": qid,
                        "repeat": repeat,
                        "model": SUBJECT_MODEL,
                        "provider": "DeepSeek",
                        "effort": "medium",
                        "started_at": "2026-10-12T10:00:00+00:00",
                        "status": status,
                        "final_text": "answer",
                        "turns": [{"input_tokens": input_tokens, "cache_creation_input_tokens": 0,
                                   "cache_read_input_tokens": 0, "output_tokens": 100,
                                   "reasoning_tokens": 40, "stop_reason": "end_turn", "latency_s": 1.0,
                                   "request_id": None,
                                   "cost_usd": input_tokens * 1e-6 if actual else None}],
                        "totals": {"reasoning_tokens": 40},
                        "tool_calls": [] if arm == "CTX" else [{"name": "search_memory"}],
                        "accessed_memory_ids": [] if arm == "CTX" else list(gold),
                        "latency_s": 1.0 + i / 10,
                        "error": None,
                    })
    return runs


def _by_id(runs: list[dict]) -> dict[str, dict]:
    return {r["run_id"]: r for r in runs}


def _run(run_id: str = "X-L1-q01-r1", arm: str = "EMB", level: str = "L1", qid: str = "q01", **overrides) -> dict:
    run = {
        "run_id": run_id, "arm": arm, "level": level, "qid": qid, "repeat": 1,
        "model": SUBJECT_MODEL, "provider": "DeepSeek", "status": "ok", "final_text": "",
        "turns": [], "tool_calls": [], "accessed_memory_ids": [], "latency_s": 1.0, "error": None,
    }
    run.update(overrides)
    return run


def _judgments(runs: list[dict], questions: dict[str, dict]) -> dict[str, dict]:
    judged = {}
    for run in runs:
        rid = run["run_id"]
        if run["status"] != "ok":
            judged[rid] = {"run_id": rid, "qid": run["qid"], "judge_model": None,
                           "correctness": 0, "quality": 0, "auto": "status:turn_limit"}
            continue
        i = int(run["qid"][1:])
        quality = QUALITY[(run["arm"], run["level"])][i - 1]
        abstention = questions[run["qid"]]["abstention"]
        judged[rid] = {
            "run_id": rid, "qid": run["qid"], "judge_model": JUDGE_MODEL,
            "required_facts": [], "correctness": quality, "quality": quality,
            "hallucination": False, "safety_critical_miss": False, "leakage": False,
            "abstained": bool(abstention and run["arm"] == "CTX"),
            "abstention_pass": (run["arm"] == "CTX") if abstention else None,
            "rationale": "", "raw": "",
        }
    return judged


def _stub_cost(turns, scenario):
    """Cost is the uncached input tokens at a flat rate, so the crossover is easy to check."""
    return sum(t["input_tokens"] for t in turns) * 1e-6


def _analysis(runs=None, judgments=None, levels_json=LEVELS_JSON, cost_fn=_stub_cost):
    questions = _questions()
    runs = runs if runs is not None else _runs()
    judgments = judgments if judgments is not None else _judgments(runs, questions)
    return analyze.run_analysis(runs, judgments, questions, levels_json=levels_json, cost_fn=cost_fn)


# ---------------------------------------------------------------- crossover interpolation

def test_crossover_interpolates_linearly_in_log_tokens():
    # d goes -1 at 95k and +1 at 200k, so the zero is halfway in log10 space: sqrt(95k * 200k)
    x, sign = analyze.find_crossover([35_000, 95_000, 200_000], [-1.0, -1.0, 1.0])
    assert x == pytest.approx(math.sqrt(95_000 * 200_000), rel=1e-12)
    assert sign == 1


def test_crossover_uses_the_first_sign_change_and_reports_direction():
    x, sign = analyze.find_crossover([10, 100, 1000, 10000], [1.0, -1.0, 1.0, -1.0])
    assert x == pytest.approx(10 ** 1.5, rel=1e-12)  # between 10 and 100, the first change
    assert sign == -1


def test_crossover_none_when_sign_never_changes():
    assert analyze.find_crossover([1, 2, 3], [-1.0, -0.5, -0.1]) == (None, None)
    assert analyze.find_crossover([1, 2, 3], [0.0, 0.0, 0.0]) == (None, None)


def test_crossover_at_an_exact_zero_level_and_skips_nan():
    x, _ = analyze.find_crossover([10, 100, 1000], [-1.0, 0.0, 1.0])
    assert x == pytest.approx(100.0)
    x, _ = analyze.find_crossover([10, 100, 1000], [-1.0, float("nan"), 1.0])
    assert x == pytest.approx(10 ** 2.0, rel=1e-9)


# ---------------------------------------------------------------- bootstrap

def test_bootstrap_is_reproducible_and_brackets_the_mean():
    values = np.array([0.0, 1.0, 1.0, 2.0, 3.0, 3.0, 2.0, 1.0])
    first = analyze.bootstrap_mean_ci(values)
    second = analyze.bootstrap_mean_ci(values)
    assert first == second
    mean, lo, hi = first
    assert mean == pytest.approx(values.mean())
    assert lo <= mean <= hi


def test_bootstrap_matrix_is_reproducible():
    matrix = np.arange(24, dtype=float).reshape(6, 4)
    a = analyze.bootstrap_nanmeans(matrix, n_boot=500)
    b = analyze.bootstrap_nanmeans(matrix, n_boot=500)
    assert np.array_equal(a, b)
    assert a.shape == (500, 4)


def test_bootstrap_constant_values_have_zero_width():
    assert analyze.bootstrap_mean_ci([2.0, 2.0, 2.0]) == (2.0, 2.0, 2.0)
    mean, lo, hi = analyze.bootstrap_mean_ci([])
    assert math.isnan(mean) and math.isnan(lo) and math.isnan(hi)


# ---------------------------------------------------------------- recall and precision

def test_recall_precision_hand_values():
    recall, precision = analyze.recall_precision(
        ["P01-m01", "P01-m02", "P02-m01", "P01-m01"], {"P01-m01", "P01-m03"})
    assert recall == pytest.approx(0.5)            # 1 of 2 gold memories
    assert precision == pytest.approx(1 / 3)       # 1 of 3 distinct accessed memories


def test_recall_precision_nan_edges():
    recall, precision = analyze.recall_precision([], {"P01-m01"})
    assert recall == 0.0 and math.isnan(precision)
    recall, precision = analyze.recall_precision(["P01-m01"], set())
    assert math.isnan(recall) and precision == 0.0


def test_wrong_patient_memories_counts_only_non_target_patients():
    accessed = ["P01-m01", "P07-m02", "D01-m01", "X0003-m01", "P07-m02"]
    assert analyze.wrong_patient_count(accessed, ["P01"]) == 2  # P07 (once) and X0003; doctors excluded


def test_gold_recall_is_nan_for_ctx_and_defined_for_emb():
    questions = _questions()
    runs = _runs()
    judgments = _judgments(runs, questions)
    per_run = analyze.build_per_run(runs, judgments, questions, cost_fn=_stub_cost)
    ctx = per_run[per_run["arm"] == "CTX"]
    emb = per_run[(per_run["arm"] == "EMB") & (per_run["qid"] == "q01")]
    assert ctx["gold_recall"].isna().all()
    assert (emb["gold_recall"] == 1.0).all() and (emb["gold_precision"] == 1.0).all()
    unanswerable = per_run[(per_run["arm"] == "EMB") & (per_run["qid"] == "q06")]
    assert unanswerable["gold_recall"].isna().all()


# ---------------------------------------------------------------- dry runs

def test_drop_dry_runs_counts_and_keeps_real_records():
    records = [{"run_id": "a"}, {"run_id": "b", "dry_run": True}, {"run_id": "c", "dry_run": False}]
    kept, dropped = analyze.drop_dry_runs(records)
    assert [r["run_id"] for r in kept] == ["a", "c"]
    assert dropped == 1


def test_run_analysis_never_analyses_dry_runs():
    questions = _questions()
    runs = _runs()
    judgments = _judgments(runs, questions)
    dry = dict(_by_id(runs)["EMB-L1-q01-r1"], run_id="DRY-EMB-L1-q01", dry_run=True)
    results = analyze.run_analysis([*runs, dry], judgments, questions, levels_json=LEVELS_JSON,
                                   cost_fn=_stub_cost)
    assert "DRY-EMB-L1-q01" not in set(results["per_run"]["run_id"])
    assert len(results["per_run"]) == len(runs)
    assert results["summary"].set_index(["arm", "level"]).loc[("EMB", "L1"), "n_runs"] == 12


# ---------------------------------------------------------------- per-run, per-question

def test_per_run_columns_and_token_totals():
    per_run = _analysis()["per_run"]
    expected = {"run_id", "arm", "level", "qid", "repeat", "use_case", "status", "quality",
                "correctness", "hallucination", "leakage", "safety_critical_miss",
                "abstention_pass", "prompt_tokens", "output_tokens", "input_tokens",
                "cache_creation_input_tokens", "cache_read_input_tokens", "cost_observed",
                "cost_uncached", "cost_cached_cold", "cost_cached_warm", "cost_actual",
                "reasoning_tokens", "provider", "latency_s", "n_tool_calls", "gold_recall",
                "gold_precision", "wrong_patient_memories"}
    assert expected <= set(per_run.columns)
    ctx_l1 = per_run[(per_run["arm"] == "CTX") & (per_run["level"] == "L1")].iloc[0]
    assert ctx_l1["prompt_tokens"] == 35_000
    assert ctx_l1["output_tokens"] == 100
    assert ctx_l1["cost_uncached"] == pytest.approx(0.035)


def test_cost_actual_provider_and_reasoning_per_run():
    questions = _questions()
    runs = [
        # Two turns, one cost logged and one None: cost_actual is the logged value.
        # totals.reasoning_tokens takes priority over the per-turn values.
        _run("r-logged",
             turns=[{"input_tokens": 100, "output_tokens": 10, "cost_usd": 0.001, "reasoning_tokens": 4},
                    {"input_tokens": 200, "output_tokens": 20, "cost_usd": None}],
             totals={"reasoning_tokens": 9}),
        # No cost logged, no totals: cost_actual is NaN and reasoning is summed from the turns.
        _run("r-unlogged", provider=None,
             turns=[{"input_tokens": 100, "output_tokens": 10, "reasoning_tokens": 3}]),
        # Neither cost nor reasoning logged: reasoning is 0.
        _run("r-bare", turns=[{"input_tokens": 100, "output_tokens": 10}]),
    ]
    per_run = analyze.build_per_run(runs, {}, questions).set_index("run_id")
    assert per_run.loc["r-logged", "cost_actual"] == pytest.approx(0.001)
    assert per_run.loc["r-logged", "reasoning_tokens"] == 9
    assert per_run.loc["r-logged", "provider"] == "DeepSeek"
    assert math.isnan(per_run.loc["r-unlogged", "cost_actual"])
    assert per_run.loc["r-unlogged", "reasoning_tokens"] == 3
    assert pd.isna(per_run.loc["r-unlogged", "provider"])
    assert math.isnan(per_run.loc["r-bare", "cost_actual"])
    assert per_run.loc["r-bare", "reasoning_tokens"] == 0


def test_auto_judgment_is_zero_quality_and_no_safety_flags():
    per_run = _analysis()["per_run"]
    row = per_run[per_run["run_id"] == "EMB-L2-q05-r2"].iloc[0]
    assert row["status"] == "turn_limit"
    assert row["quality"] == 0.0
    assert math.isnan(row["hallucination"])


def test_per_question_averages_repeats():
    questions = _questions()
    runs = _runs()
    judgments = _judgments(runs, questions)
    # Repeat 2 of EMB L1 q01 leaks: quality 0 on that repeat, 2 on repeat 1, so the mean is 1.
    judgments["EMB-L1-q01-r2"] = dict(judgments["EMB-L1-q01-r2"], quality=0, correctness=2, leakage=True)
    per_q = analyze.build_per_question(
        analyze.build_per_run(runs, judgments, questions, cost_fn=_stub_cost))
    row = per_q[(per_q["arm"] == "EMB") & (per_q["level"] == "L1") & (per_q["qid"] == "q01")].iloc[0]
    assert row["quality"] == pytest.approx(1.0)
    assert row["n_runs"] == 2


def test_summary_hand_values_and_counts():
    questions = _questions()
    runs = _runs()
    judgments = _judgments(runs, questions)
    judgments["EMB-L1-q01-r2"] = dict(judgments["EMB-L1-q01-r2"], quality=0, correctness=0, leakage=True)
    results = analyze.run_analysis(runs, judgments, questions, levels_json=LEVELS_JSON, cost_fn=_stub_cost)
    summary = results["summary"].set_index(["arm", "level"])

    emb_l1 = summary.loc[("EMB", "L1")]
    assert emb_l1["n_questions"] == 6
    assert emb_l1["quality_mean"] == pytest.approx((1.0 + 2 + 2 + 2 + 1 + 1) / 6)
    assert emb_l1["leakage_total"] == 1
    assert emb_l1["tokens_median"] == 5_100          # 5,000 input + 100 output
    assert emb_l1["store_tokens"] == 35_000
    assert emb_l1["gold_recall_mean"] == pytest.approx(1.0)
    assert emb_l1["abstention_pass_rate"] == pytest.approx(0.0)
    assert emb_l1["n_abstention_runs"] == 2
    # Abstention pass comes from correctness == 3, not the `abstained` flag: CTX L1 scored 2 on q06.
    assert summary.loc[("CTX", "L1")]["abstention_pass_rate"] == pytest.approx(0.0)
    assert math.isnan(summary.loc[("CTX", "L1")]["gold_recall_mean"])
    assert summary.loc[("EMB", "L2")]["n_runs"] == 12  # includes the auto (turn_limit) run

    lo, hi = emb_l1["quality_ci_low"], emb_l1["quality_ci_high"]
    assert lo <= emb_l1["quality_mean"] <= hi


def test_summary_cost_actual_providers_and_reasoning_columns():
    questions = _questions()
    runs = _runs(actual=True)
    _by_id(runs)["EMB-L1-q02-r1"]["provider"] = "Baidu"   # a second provider within one cell
    _by_id(runs)["EMB-L1-q03-r1"]["provider"] = None      # a missing provider is left out of the list
    judgments = _judgments(runs, questions)
    summary = analyze.run_analysis(runs, judgments, questions, levels_json=LEVELS_JSON,
                                   cost_fn=_stub_cost)["summary"].set_index(["arm", "level"])

    # Logged cost is input tokens x $1/MTok: EMB L1 is 5,000 tokens (0.005), CTX L1 is 35,000 (0.035).
    assert summary.loc[("EMB", "L1"), "cost_actual_mean"] == pytest.approx(0.005)
    assert summary.loc[("CTX", "L1"), "cost_actual_mean"] == pytest.approx(0.035)
    assert summary.loc[("EMB", "L1"), "reasoning_tokens_median"] == 40
    assert summary.loc[("EMB", "L1"), "providers"] == "Baidu|DeepSeek"
    assert summary.loc[("CTX", "L1"), "providers"] == "DeepSeek"


def test_summary_cost_actual_is_nan_when_nothing_was_logged():
    summary = _analysis()["summary"].set_index(["arm", "level"])
    assert math.isnan(summary.loc[("EMB", "L1"), "cost_actual_mean"])


def test_per_use_case_means():
    per_uc = _analysis()["per_use_case"]
    row = per_uc[(per_uc["use_case"] == 1) & (per_uc["arm"] == "CTX") & (per_uc["level"] == "L1")].iloc[0]
    assert row["n_questions"] == 2                   # q01 and q04
    assert row["mean_quality"] == pytest.approx(3.0)  # both are 3 at CTX L1


# ---------------------------------------------------------------- crossovers

def test_quality_crossover_is_planted_between_l2_and_l3():
    crossover = _analysis()["crossover"]
    emb = crossover["quality"]["EMB"]
    assert emb["crossover_tokens"] == pytest.approx(math.sqrt(95_000 * 200_000), rel=1e-9)
    assert emb["reason"] is None
    assert emb["beyond_crossover_favours"] == "EMB"
    assert emb["per_level"]["L1"]["mean_d"] == pytest.approx(-1.0)
    assert emb["per_level"]["L3"]["mean_d"] == pytest.approx(1.0)
    # Every resample has the same paired differences, so the CI collapses onto the estimate.
    assert emb["crossover_ci_low"] == pytest.approx(emb["crossover_tokens"], rel=1e-9)
    assert emb["crossover_ci_high"] == pytest.approx(emb["crossover_tokens"], rel=1e-9)
    assert emb["fraction_resamples_no_crossover"] == 0.0


def test_quality_crossover_null_with_reason_when_arm_never_changes_sign():
    md = _analysis()["crossover"]["quality"]["MD"]
    assert md["crossover_tokens"] is None
    assert md["reason"] == "no crossover in tested range"
    assert md["fraction_resamples_no_crossover"] == 1.0


def test_crossover_is_reproducible_across_calls():
    first = json.dumps(analyze._jsonable(_analysis()["crossover"]), sort_keys=True)
    second = json.dumps(analyze._jsonable(_analysis()["crossover"]), sort_keys=True)
    assert first == second


def test_cost_crossover_between_l1_and_l2_when_emb_is_flat():
    # CTX cost follows the store (35k, 95k, 200k tokens); EMB is a flat 50k tokens per question.
    crossover = _analysis(runs=_runs(emb_input=50_000))["crossover"]
    emb_cost = crossover["cost"]["uncached"]["EMB"]
    expected = 10 ** (math.log10(35_000) + 0.25 * (math.log10(95_000) - math.log10(35_000)))
    assert emb_cost["crossover_tokens"] == pytest.approx(expected, rel=1e-9)
    assert emb_cost["arm_cheaper_at_levels"] == ["L2", "L3"]
    assert emb_cost["beyond_crossover_cheaper"] == "EMB"


def test_cost_crossover_none_when_arm_is_always_cheaper():
    crossover = _analysis()["crossover"]
    emb_cost = crossover["cost"]["observed"]["EMB"]
    assert emb_cost["crossover_tokens"] is None
    assert emb_cost["arm_cheaper_at_levels"] == ["L1", "L2", "L3"]


def test_actual_cost_crossover_matches_the_uncached_one_when_logged():
    # Logged cost is the same flat $1/MTok as _stub_cost, so the actual crossover equals the uncached one.
    crossover = _analysis(runs=_runs(emb_input=50_000, actual=True))["crossover"]
    expected = 10 ** (math.log10(35_000) + 0.25 * (math.log10(95_000) - math.log10(35_000)))
    emb_actual = crossover["cost"]["actual"]["EMB"]
    assert emb_actual["crossover_tokens"] == pytest.approx(expected, rel=1e-9)
    assert emb_actual["arm_cheaper_at_levels"] == ["L2", "L3"]
    assert emb_actual["beyond_crossover_cheaper"] == "EMB"
    assert set(crossover["cost"]) == {"observed", "uncached", "cached_cold", "cached_warm", "actual"}


def test_actual_cost_crossover_is_skipped_when_nothing_was_logged():
    crossover = _analysis()["crossover"]
    assert crossover["cost"]["actual"]["EMB"] == {"skipped": NO_COST_REASON}
    assert crossover["cost"]["actual"]["MD"] == {"skipped": NO_COST_REASON}


def test_actual_cost_crossover_is_null_when_every_logged_cost_is_zero(tmp_path):
    # Free endpoint: every turn logs cost_usd = 0.0. There is no price gap, so the crossover is null
    # with a reason, not a number and not a crash. The rest of the analysis still renders.
    runs = _runs(emb_input=50_000)
    for run in runs:
        for turn in run["turns"]:
            turn["cost_usd"] = 0.0
    results = _analysis(runs=runs)

    emb_actual = results["crossover"]["cost"]["actual"]["EMB"]
    assert emb_actual["crossover_tokens"] is None
    assert emb_actual["beyond_crossover_cheaper"] is None
    assert emb_actual["arm_cheaper_at_levels"] == []
    assert emb_actual["reason"] == analyze.ZERO_ACTUAL_REASON
    assert emb_actual["per_level"]["L1"] == {"arm_mean_cost_usd": 0.0, "ctx_mean_cost_usd": 0.0}
    # The modelled scenarios do not read the logged cost, so their crossovers are unchanged.
    assert results["crossover"]["cost"]["uncached"]["EMB"]["crossover_tokens"] is not None

    summary = results["summary"].set_index(["arm", "level"])
    assert summary.loc[("EMB", "L1"), "cost_actual_mean"] == 0.0
    assert (results["per_run"]["cost_actual"] == 0.0).all()

    analyze.write_outputs(results, tmp_path)
    crossover = json.loads((tmp_path / "crossover.json").read_text(encoding="utf-8"))
    assert crossover["cost"]["actual"]["EMB"]["crossover_tokens"] is None
    assert crossover["cost"]["actual"]["EMB"]["reason"] == analyze.ZERO_ACTUAL_REASON


def test_store_sizes_fall_back_to_median_ctx_prompt_when_levels_json_missing():
    results = _analysis(levels_json=None)
    assert results["crossover"]["store_tokens"] == {"L1": 35_000.0, "L2": 95_000.0, "L3": 200_000.0}
    assert set(results["crossover"]["store_tokens_source"].values()) == {"median CTX prompt_tokens"}


# ---------------------------------------------------------------- tests and Holm

def test_holm_matches_hand_calculation():
    adjusted = analyze.holm([0.01, 0.04, 0.03, 0.005])
    # sorted: 0.005*4 = 0.02; 0.01*3 = 0.03; 0.03*2 = 0.06; 0.04*1 = 0.04, cumulative max 0.06
    assert adjusted.tolist() == pytest.approx([0.03, 0.06, 0.06, 0.02])


def test_holm_caps_at_one_and_ignores_nan():
    adjusted = analyze.holm([0.5, 0.9, float("nan")])
    assert adjusted[0] == pytest.approx(1.0)
    assert adjusted[1] == pytest.approx(1.0)
    assert math.isnan(adjusted[2])


def test_tests_csv_uses_wilcoxon_zsplit_and_holm_across_all_tests():
    results = _analysis()
    tests = results["tests"]
    assert len(tests) == 6  # 3 levels x (EMB, MD)
    assert set(tests["comparison"]) == {"EMB_vs_CTX", "MD_vs_CTX"}

    emb_l1 = tests[(tests["level"] == "L1") & (tests["comparison"] == "EMB_vs_CTX")].iloc[0]
    d = np.full(6, -1.0)
    expected = wilcoxon(d, zero_method="zsplit")
    assert emb_l1["p_raw"] == pytest.approx(float(expected.pvalue))
    assert emb_l1["mean_d"] == pytest.approx(-1.0)
    assert emb_l1["n"] == 6

    md_row = tests[tests["comparison"] == "MD_vs_CTX"]
    assert (md_row["p_raw"] == 1.0).all()  # all differences are zero

    finite = tests.dropna(subset=["p_raw"])
    assert (finite["p_holm"] >= finite["p_raw"] - 1e-12).all()
    expected_holm = analyze.holm(tests["p_raw"].to_numpy(dtype=float))
    assert np.allclose(tests["p_holm"].to_numpy(dtype=float), expected_holm, equal_nan=True)


# ---------------------------------------------------------------- CLI

def test_cli_writes_all_outputs_and_drops_dry_runs(tmp_path, capsys):
    questions = _questions()
    runs = _runs(actual=True)
    judgments = _judgments(runs, questions)
    # A dry run that reuses a real run_id, written after the real record, must not replace it.
    dry_same_id = dict(_by_id(runs)["EMB-L1-q01-r1"], dry_run=True)
    dry_same_id["turns"] = [dict(dry_same_id["turns"][0], input_tokens=999_999)]
    dry_new_id = dict(_by_id(runs)["MD-L3-q01-r1"], run_id="DRY-MD-L3-q01-r1", dry_run=True)
    runs_path = tmp_path / "runs.jsonl"
    judgments_path = tmp_path / "judgments.jsonl"
    questions_path = tmp_path / "questions.json"
    levels_path = tmp_path / "levels.json"
    all_rows = [*runs, dry_same_id, dry_new_id]
    runs_path.write_text("\n".join(json.dumps(r) for r in all_rows) + "\n", encoding="utf-8")
    judgments_path.write_text("\n".join(json.dumps(j) for j in judgments.values()) + "\n", encoding="utf-8")
    questions_path.write_text(json.dumps(list(questions.values())), encoding="utf-8")
    levels_path.write_text(json.dumps(LEVELS_JSON), encoding="utf-8")
    out_dir = tmp_path / "out"

    code = analyze.main(["--runs", str(runs_path), "--judgments", str(judgments_path),
                         "--out", str(out_dir), "--questions", str(questions_path),
                         "--levels", str(levels_path)])
    assert code == 0
    assert "dropped 2 dry-run record(s)" in capsys.readouterr().out

    for name in ("per_run.csv", "per_question.csv", "summary.csv", "tests.csv", "per_use_case.csv",
                 "crossover.json"):
        assert (out_dir / name).exists(), name

    per_run = pd.read_csv(out_dir / "per_run.csv").set_index("run_id")
    assert "DRY-MD-L3-q01-r1" not in per_run.index
    assert per_run.loc["EMB-L1-q01-r1", "prompt_tokens"] == 5_000   # not the 999,999 dry copy
    assert per_run.loc["EMB-L1-q01-r1", "provider"] == "DeepSeek"
    assert per_run.loc["EMB-L1-q01-r1", "reasoning_tokens"] == 40
    assert per_run.loc["EMB-L1-q01-r1", "cost_actual"] == pytest.approx(0.005)

    crossover = json.loads((out_dir / "crossover.json").read_text(encoding="utf-8"))
    assert crossover["quality"]["EMB"]["crossover_tokens"] == pytest.approx(
        math.sqrt(95_000 * 200_000), rel=1e-9)
    assert crossover["cost"]["actual"]["EMB"]["arm_cheaper_at_levels"] == ["L1", "L2", "L3"]

    summary = pd.read_csv(out_dir / "summary.csv").set_index(["arm", "level"])
    assert {"quality_ci_low", "quality_ci_high", "cost_cached_warm_mean", "cost_actual_mean",
            "reasoning_tokens_median", "providers"} <= set(summary.columns)
    assert summary.loc[("EMB", "L1"), "n_runs"] == 12
    assert summary.loc[("EMB", "L1"), "providers"] == "DeepSeek"


def test_cli_refuses_a_runs_file_that_is_all_dry_runs(tmp_path, capsys):
    dry = dict(_runs()[0], dry_run=True)
    runs_path = tmp_path / "runs.jsonl"
    judgments_path = tmp_path / "judgments.jsonl"
    questions_path = tmp_path / "questions.json"
    runs_path.write_text(json.dumps(dry) + "\n", encoding="utf-8")
    judgments_path.write_text("", encoding="utf-8")
    questions_path.write_text(json.dumps(list(_questions().values())), encoding="utf-8")

    code = analyze.main(["--runs", str(runs_path), "--judgments", str(judgments_path),
                         "--out", str(tmp_path / "out"), "--questions", str(questions_path),
                         "--levels", str(tmp_path / "missing_levels.json")])
    assert code == 1
    assert "no analysable runs" in capsys.readouterr().err



def test_abstention_pass_uses_correctness_not_the_abstained_flag():
    q = {"qid": "q99", "abstention": True}
    assert analyze._abstention_pass({"correctness": 3, "abstained": False, "abstention_pass": False}, q) == 1.0
    assert analyze._abstention_pass({"correctness": 2, "abstained": True, "abstention_pass": True}, q) == 0.0
    assert analyze._abstention_pass({"correctness": None}, q) == 0.0
    assert pd.isna(analyze._abstention_pass(None, q))
    assert pd.isna(analyze._abstention_pass({"correctness": 3}, {"qid": "q1", "abstention": False}))
