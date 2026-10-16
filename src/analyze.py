"""Summarise judged runs into the tables and crossover estimates in DESIGN.md section 10.

  python -m src.analyze --runs runs/main.jsonl --judgments results/judgments_main.jsonl --out results/

Writes per_run.csv, per_question.csv, summary.csv, crossover.json, tests.csv and per_use_case.csv
to --out. Relative paths are resolved against the project root.
"""

from __future__ import annotations

import argparse
import json
import math
import re
import sys
import warnings
from pathlib import Path

import numpy as np
import pandas as pd
from scipy.stats import wilcoxon

from src import pricing

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_QUESTIONS = ROOT / "questions" / "questions.json"
DEFAULT_LEVELS = ROOT / "world" / "levels.json"

N_BOOT = 10_000
BOOT_SEED = 0
BASE_ARM = "CTX"
COMPARATORS = ("EMB", "MD")
NO_RETRIEVAL_ARMS = ("CTX", "ORACLE", "FLOOR")   # gold recall and precision are not defined for these
ARM_ORDER = ("CTX", "EMB", "MD", "ORACLE", "FLOOR")
SCENARIOS = pricing.SCENARIOS
ACTUAL = "actual"                                # provider-reported cost (cost_usd), not a scenario
COST_KEYS = (*SCENARIOS, ACTUAL)                 # cost crossovers are reported for each of these
NO_CROSSOVER = "no crossover in tested range"
ZERO_ACTUAL_REASON = "every logged provider cost is 0 (free endpoint); no actual-cost crossover"

PER_QUESTION_METRICS = [
    "quality", "correctness", "hallucination", "leakage", "safety_critical_miss", "abstention_pass",
    "prompt_tokens", "output_tokens", "input_tokens", "cache_creation_input_tokens",
    "cache_read_input_tokens", "cost_observed", "cost_uncached", "cost_cached_cold",
    "cost_cached_warm", "cost_actual", "reasoning_tokens", "latency_s", "n_tool_calls",
    "gold_recall", "gold_precision", "wrong_patient_memories",
]
JUDGMENT_BINARY = ("hallucination", "leakage", "safety_critical_miss")
_PATIENT_OWNER = re.compile(r"[PX]\d{2,4}")


# ---------------------------------------------------------------- small helpers

def _nan() -> float:
    return float("nan")


def _finite(value) -> bool:
    try:
        return math.isfinite(float(value))
    except (TypeError, ValueError):
        return False


def _f(value) -> float:
    """Float or NaN. None becomes NaN; booleans become 0.0 or 1.0."""
    if value is None:
        return _nan()
    try:
        return float(value)
    except (TypeError, ValueError):
        return _nan()


def _level_key(level: str) -> int:
    match = re.search(r"\d+", level)
    return int(match.group()) if match else 0


def _arm_key(arm: str) -> int:
    return ARM_ORDER.index(arm) if arm in ARM_ORDER else len(ARM_ORDER)


def _nanmean(series) -> float:
    values = pd.to_numeric(pd.Series(series), errors="coerce").dropna()
    return float(values.mean()) if len(values) else _nan()


def _nansum(series) -> float:
    values = pd.to_numeric(pd.Series(series), errors="coerce").dropna()
    return float(values.sum()) if len(values) else _nan()


def _nanmedian(series) -> float:
    values = pd.to_numeric(pd.Series(series), errors="coerce").dropna()
    return float(values.median()) if len(values) else _nan()


def _nanquantile(values, q: float) -> float:
    arr = np.asarray(values, dtype=float)
    arr = arr[np.isfinite(arr)]
    return float(np.percentile(arr, q)) if arr.size else _nan()


def _sort_by_arm_level(df: pd.DataFrame) -> pd.DataFrame:
    order = df.assign(_a=df["arm"].map(_arm_key), _l=df["level"].map(_level_key))
    return order.sort_values(["_a", "_l"], kind="stable").drop(columns=["_a", "_l"])


def _arm_levels(df: pd.DataFrame) -> list[tuple[str, str]]:
    pairs = {(a, lvl) for a, lvl in zip(df["arm"], df["level"])}
    return sorted(pairs, key=lambda p: (_arm_key(p[0]), _level_key(p[1])))


def _jsonable(obj):
    if isinstance(obj, dict):
        return {str(k): _jsonable(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_jsonable(v) for v in obj]
    if isinstance(obj, (bool, np.bool_)):
        return bool(obj)
    if isinstance(obj, (int, np.integer)):
        return int(obj)
    if isinstance(obj, (float, np.floating)):
        return float(obj) if math.isfinite(float(obj)) else None
    return obj


# ---------------------------------------------------------------- input

def read_jsonl(path: Path) -> list[dict]:
    rows = []
    with path.open(encoding="utf-8") as fh:
        for lineno, line in enumerate(fh, start=1):
            if not line.strip():
                continue
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError:
                print(f"warning: skipping malformed line {lineno} in {path}", file=sys.stderr)
    return rows


def latest_by_run_id(rows: list[dict]) -> dict[str, dict]:
    latest: dict[str, dict] = {}
    for row in rows:
        latest[row["run_id"]] = row
    return latest


def latest_judgments(rows: list[dict]) -> dict[str, dict]:
    """Last non-error judgment per run. Error lines are treated as missing."""
    judged: dict[str, dict] = {}
    for row in rows:
        if "error" not in row:
            judged[row["run_id"]] = row
    return judged


def drop_dry_runs(records: list[dict]) -> tuple[list[dict], int]:
    """Records without `"dry_run": true`, and how many were dropped. Call before deduplicating by run_id,
    so a dry run that reuses a real run_id cannot replace the real record."""
    kept = [r for r in records if not r.get("dry_run")]
    return kept, len(records) - len(kept)


# ---------------------------------------------------------------- per-run metrics

def gold_memory_ids(question: dict) -> set[str]:
    return {mid for fact in question.get("required_facts") or [] for mid in fact.get("memory_ids") or []}


def owner_of(memory_id: str) -> str:
    """'P01-m03' -> 'P01'; 'D02-m01' -> 'D02'; 'X0004-m02' -> 'X0004'."""
    return memory_id.rsplit("-", 1)[0]


def recall_precision(accessed, gold) -> tuple[float, float]:
    """Gold recall and precision of a set of accessed memory IDs (duplicates ignored).

    Recall is NaN when there are no gold IDs; precision is NaN when nothing was accessed.
    """
    accessed_set = set(accessed)
    hits = len(accessed_set & set(gold))
    recall = hits / len(gold) if gold else _nan()
    precision = hits / len(accessed_set) if accessed_set else _nan()
    return recall, precision


def wrong_patient_count(accessed, target_patient_ids) -> int:
    """Accessed memories owned by a patient who is not a target of the question."""
    targets = set(target_patient_ids)
    count = 0
    for mid in set(accessed):
        owner = owner_of(mid)
        if _PATIENT_OWNER.fullmatch(owner) and owner not in targets:
            count += 1
    return count


def _turn_sums(turns: list[dict]) -> tuple[int, int, int, int]:
    inp = cache_write = cache_read = out = 0
    for turn in turns:
        inp += int(turn.get("input_tokens") or 0)
        cache_write += int(turn.get("cache_creation_input_tokens") or 0)
        cache_read += int(turn.get("cache_read_input_tokens") or 0)
        out += int(turn.get("output_tokens") or 0)
    return inp, cache_write, cache_read, out


def _reasoning_tokens(run: dict) -> int:
    """run["totals"]["reasoning_tokens"] if logged, else the sum over turns, else 0. Already inside output tokens."""
    totals = run.get("totals") or {}
    if totals.get("reasoning_tokens") is not None:
        return int(totals["reasoning_tokens"])
    return sum(int(t.get("reasoning_tokens") or 0) for t in run.get("turns") or [])


def _subject_run_cost(turns: list[dict], scenario: str) -> float:
    return pricing.run_cost(turns, scenario, role="subject")


def _abstention_pass(judgment: dict | None, question: dict) -> float:
    """On an abstention question: 1 when the judge scored correctness 3, which its rubric gives only to a
    response that clearly says there is no record and invents nothing; else 0. NaN on other questions.

    (Changed after the main run: the judge's separate `abstained` flag was False for 46 responses it had
    scored 3 for correctly saying there was no record, so the flag is not used. See the DESIGN.md log.)
    """
    if not question.get("abstention"):
        return _nan()
    if judgment is None:
        return _nan()
    if judgment.get("correctness") is None:
        return 0.0  # non-ok runs carry no judgment fields; they did not answer "no record"
    return 1.0 if int(judgment["correctness"]) == 3 else 0.0


def build_per_run(runs: list[dict], judgments: dict[str, dict], questions: dict[str, dict],
                  cost_fn=None) -> pd.DataFrame:
    """One row per run. `cost_fn(turns, scenario)` defaults to the subject card in src.pricing.run_cost.
    `cost_actual` is the provider-reported cost (NaN when no turn logged one)."""
    if cost_fn is None:
        cost_fn = _subject_run_cost

    rows = []
    for run in runs:
        question = questions[run["qid"]]
        judgment = judgments.get(run["run_id"])
        turns = run.get("turns") or []
        inp, cache_write, cache_read, out = _turn_sums(turns)
        accessed = list(dict.fromkeys(run.get("accessed_memory_ids") or []))
        if run["arm"] in NO_RETRIEVAL_ARMS:
            recall, precision = _nan(), _nan()
        else:
            recall, precision = recall_precision(accessed, gold_memory_ids(question))

        row = {
            "run_id": run["run_id"],
            "arm": run["arm"],
            "level": run["level"],
            "qid": run["qid"],
            "repeat": run.get("repeat"),
            "use_case": question.get("use_case"),
            "status": run.get("status"),
            "quality": _f(judgment.get("quality")) if judgment else _nan(),
            "correctness": _f(judgment.get("correctness")) if judgment else _nan(),
            "hallucination": _f(judgment.get("hallucination")) if judgment else _nan(),
            "leakage": _f(judgment.get("leakage")) if judgment else _nan(),
            "safety_critical_miss": _f(judgment.get("safety_critical_miss")) if judgment else _nan(),
            "abstention_pass": _abstention_pass(judgment, question),
            "prompt_tokens": inp + cache_write + cache_read,
            "output_tokens": out,
            "input_tokens": inp,
            "cache_creation_input_tokens": cache_write,
            "cache_read_input_tokens": cache_read,
        }
        for scenario in SCENARIOS:
            row[f"cost_{scenario}"] = float(cost_fn(turns, scenario))
        row.update({
            "cost_actual": _f(pricing.actual_cost(turns)),
            "reasoning_tokens": _reasoning_tokens(run),
            "provider": run.get("provider"),
            "latency_s": _f(run.get("latency_s")),
            "n_tool_calls": len(run.get("tool_calls") or []),
            "gold_recall": recall,
            "gold_precision": precision,
            # Only retrieval arms choose what enters context; CTX holds every patient by design.
            "wrong_patient_memories": (
                wrong_patient_count(accessed, question["target_patient_ids"])
                if run["arm"] in ("EMB", "MD") else _nan()
            ),
        })
        rows.append(row)
    return pd.DataFrame(rows)


def build_per_question(per_run: pd.DataFrame) -> pd.DataFrame:
    """Repeats averaged within (arm, level, qid)."""
    grouped = per_run.groupby(["arm", "level", "qid"], sort=False)
    agg = grouped[PER_QUESTION_METRICS].mean()
    agg["n_runs"] = grouped.size()
    agg["use_case"] = grouped["use_case"].first()
    agg = agg.reset_index()
    agg = agg[["arm", "level", "qid", "use_case", "n_runs", *PER_QUESTION_METRICS]]
    return _sort_by_arm_level(agg)


# ---------------------------------------------------------------- store sizes

def level_sizes(levels_json: dict | None, per_run: pd.DataFrame) -> tuple[dict[str, float], dict[str, str]]:
    """Store size in tokens per level: world/levels.json if present, else the median CTX prompt."""
    sizes: dict[str, float] = {}
    source: dict[str, str] = {}
    ctx = per_run[per_run["arm"] == BASE_ARM]
    for level in sorted(set(per_run["level"]), key=_level_key):
        declared = ((levels_json or {}).get(level) or {}).get("ctx_tokens")
        if declared is not None:
            sizes[level] = float(declared)
            source[level] = "world/levels.json"
            continue
        median = ctx.loc[ctx["level"] == level, "prompt_tokens"].median()
        if pd.notna(median):
            sizes[level] = float(median)
            source[level] = "median CTX prompt_tokens"
    return sizes, source


# ---------------------------------------------------------------- bootstrap and crossovers

def bootstrap_mean_ci(values, n_boot: int = N_BOOT, seed: int = BOOT_SEED) -> tuple[float, float, float]:
    """Mean with a percentile bootstrap CI over the values (resampled with replacement)."""
    arr = np.asarray(values, dtype=float)
    arr = arr[np.isfinite(arr)]
    n = arr.size
    if n == 0:
        return _nan(), _nan(), _nan()
    mean = float(arr.mean())
    if n == 1:
        return mean, mean, mean
    rng = np.random.default_rng(seed)
    means = arr[rng.integers(0, n, size=(n_boot, n))].mean(axis=1)
    lo, hi = np.percentile(means, [2.5, 97.5])
    return mean, float(lo), float(hi)


def bootstrap_nanmeans(matrix: np.ndarray, n_boot: int = N_BOOT, seed: int = BOOT_SEED) -> np.ndarray:
    """Column means of `matrix` for each bootstrap resample of its rows (questions). Shape (n_boot, cols)."""
    rng = np.random.default_rng(seed)
    n = matrix.shape[0]
    idx = rng.integers(0, n, size=(n_boot, n))
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", category=RuntimeWarning)
        return np.nanmean(matrix[idx], axis=1)


def find_crossover(sizes, d) -> tuple[float | None, int | None]:
    """First sign change of the mean paired difference `d` across ascending store `sizes`.

    Between adjacent measured levels the crossing is linear in log10(tokens). If a level has an
    exact zero, the crossing is taken at that level. Returns (tokens, sign_after), where sign_after
    is +1 if d ends positive and -1 if negative, or (None, None) when the sign never changes.
    NaN entries are skipped.
    """
    nonzero = [i for i, v in enumerate(d) if _finite(v) and v != 0]
    for p, q in zip(nonzero, nonzero[1:]):
        if (d[p] > 0) == (d[q] > 0):
            continue
        zeros = [m for m in range(p + 1, q) if _finite(d[m]) and d[m] == 0]
        if zeros:
            x = float(sizes[zeros[0]])
        else:
            frac = d[p] / (d[p] - d[q])
            lo_log, hi_log = math.log10(sizes[p]), math.log10(sizes[q])
            x = 10 ** (lo_log + frac * (hi_log - lo_log))
        return float(x), (1 if d[q] > 0 else -1)
    return None, None


def _quality_lookup(per_q: pd.DataFrame) -> dict[tuple[str, str, str], float]:
    return {(r.arm, r.level, r.qid): float(r.quality) for r in per_q.itertuples()}


def paired_matrix(lookup: dict, arm: str, levels: list[str], qids: list[str]) -> np.ndarray:
    """Rows are questions, columns are levels. Entries are quality(arm) - quality(CTX), NaN if missing."""
    matrix = np.full((len(qids), len(levels)), np.nan)
    for i, qid in enumerate(qids):
        for j, level in enumerate(levels):
            a = lookup.get((arm, level, qid))
            c = lookup.get((BASE_ARM, level, qid))
            if a is not None and c is not None and _finite(a) and _finite(c):
                matrix[i, j] = a - c
    return matrix


def _cost_by_level(per_run: pd.DataFrame, arm: str, level: str, scenario: str) -> float:
    rows = per_run[(per_run["arm"] == arm) & (per_run["level"] == level)]
    return _nanmean(rows[f"cost_{scenario}"])


def _any_finite(values) -> bool:
    return any(_finite(v) for v in values)


def _all_zero(values) -> bool:
    """True when there is at least one finite value and every finite value is exactly 0. NaN is ignored."""
    finite = [float(v) for v in values if _finite(v)]
    return bool(finite) and all(v == 0.0 for v in finite)


def build_crossover(per_run: pd.DataFrame, per_q: pd.DataFrame, sizes: dict[str, float],
                    size_source: dict[str, str]) -> dict:
    levels = sorted(sizes, key=lambda lvl: sizes[lvl])
    size_list = [sizes[lvl] for lvl in levels]
    arms = set(per_q["arm"])
    lookup = _quality_lookup(per_q)
    qids = sorted(set(per_q["qid"]))

    result: dict = {
        "n_boot": N_BOOT,
        "bootstrap_seed": BOOT_SEED,
        "store_tokens": {lvl: sizes[lvl] for lvl in levels},
        "store_tokens_source": {lvl: size_source[lvl] for lvl in levels},
        "quality": {},
        "cost": {},
    }

    for arm in COMPARATORS:
        if arm not in arms or BASE_ARM not in arms:
            result["quality"][arm] = {"skipped": f"needs {arm} and CTX runs"}
            continue
        matrix = paired_matrix(lookup, arm, levels, qids)
        means = _column_nanmeans(matrix)
        boots = bootstrap_nanmeans(matrix)
        lo = _column_nanpercentile(boots, 2.5)
        hi = _column_nanpercentile(boots, 97.5)
        x, sign = find_crossover(size_list, means)
        found = [xb for xb in (find_crossover(size_list, row)[0] for row in boots) if xb is not None]
        result["quality"][arm] = {
            "per_level": {
                lvl: {"mean_d": means[j], "ci_low": lo[j], "ci_high": hi[j],
                      "n_questions": int(np.isfinite(matrix[:, j]).sum())}
                for j, lvl in enumerate(levels)
            },
            "crossover_tokens": x,
            "reason": None if x is not None else NO_CROSSOVER,
            "beyond_crossover_favours": None if sign is None else (arm if sign > 0 else BASE_ARM),
            "crossover_ci_low": float(np.percentile(found, 2.5)) if found else None,
            "crossover_ci_high": float(np.percentile(found, 97.5)) if found else None,
            "n_resamples_with_crossover": len(found),
            "fraction_resamples_no_crossover": 1 - len(found) / N_BOOT,
        }

    for scenario in COST_KEYS:
        result["cost"][scenario] = {}
        for arm in COMPARATORS:
            if arm not in arms or BASE_ARM not in arms:
                result["cost"][scenario][arm] = {"skipped": f"needs {arm} and CTX runs"}
                continue
            arm_cost = [_cost_by_level(per_run, arm, lvl, scenario) for lvl in levels]
            ctx_cost = [_cost_by_level(per_run, BASE_ARM, lvl, scenario) for lvl in levels]
            if not (_any_finite(arm_cost) and _any_finite(ctx_cost)):
                result["cost"][scenario][arm] = {"skipped": "no cost values logged for this arm or CTX"}
                continue
            diff = [a - c for a, c in zip(arm_cost, ctx_cost)]
            if scenario == ACTUAL and _all_zero(arm_cost + ctx_cost):
                # Free endpoint: nothing was charged, so there is no price gap to cross. Null, with the reason.
                x, sign, reason = None, None, ZERO_ACTUAL_REASON
            else:
                x, sign = find_crossover(size_list, diff)
                reason = None if x is not None else NO_CROSSOVER
            result["cost"][scenario][arm] = {
                "per_level": {lvl: {"arm_mean_cost_usd": arm_cost[j], "ctx_mean_cost_usd": ctx_cost[j]}
                              for j, lvl in enumerate(levels)},
                "arm_cheaper_at_levels": [lvl for lvl, v in zip(levels, diff) if _finite(v) and v < 0],
                "crossover_tokens": x,
                "reason": reason,
                "beyond_crossover_cheaper": None if sign is None else (BASE_ARM if sign > 0 else arm),
            }
    return result


def _column_nanmeans(matrix: np.ndarray) -> np.ndarray:
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", category=RuntimeWarning)
        return np.nanmean(matrix, axis=0)


def _column_nanpercentile(matrix: np.ndarray, q: float) -> np.ndarray:
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", category=RuntimeWarning)
        return np.nanpercentile(matrix, q, axis=0)


# ---------------------------------------------------------------- summary and tests

def build_summary(per_run: pd.DataFrame, per_q: pd.DataFrame, sizes: dict[str, float]) -> pd.DataFrame:
    rows = []
    for arm, level in _arm_levels(per_run):
        pr = per_run[(per_run["arm"] == arm) & (per_run["level"] == level)]
        pq = per_q[(per_q["arm"] == arm) & (per_q["level"] == level)]
        q_mean, q_lo, q_hi = bootstrap_mean_ci(pq["quality"].to_numpy(dtype=float))
        tokens = (pr["prompt_tokens"] + pr["output_tokens"]).to_numpy(dtype=float)
        rows.append({
            "arm": arm,
            "level": level,
            "store_tokens": sizes.get(level, _nan()),
            "n_questions": int(pq["quality"].notna().sum()),
            "n_runs": len(pr),
            "quality_mean": q_mean,
            "quality_ci_low": q_lo,
            "quality_ci_high": q_hi,
            "tokens_median": _nanquantile(tokens, 50),
            "tokens_q25": _nanquantile(tokens, 25),
            "tokens_q75": _nanquantile(tokens, 75),
            "reasoning_tokens_median": _nanmedian(pr["reasoning_tokens"]),
            "cost_observed_mean": _nanmean(pr["cost_observed"]),
            "cost_uncached_mean": _nanmean(pr["cost_uncached"]),
            "cost_cached_cold_mean": _nanmean(pr["cost_cached_cold"]),
            "cost_cached_warm_mean": _nanmean(pr["cost_cached_warm"]),
            "cost_actual_mean": _nanmean(pr["cost_actual"]),
            "providers": "|".join(sorted({p for p in pr["provider"] if isinstance(p, str) and p})),
            "leakage_total": _nansum(pr["leakage"]),
            "safety_critical_miss_total": _nansum(pr["safety_critical_miss"]),
            "hallucination_total": _nansum(pr["hallucination"]),
            "abstention_pass_rate": _nanmean(pr["abstention_pass"]),
            "n_abstention_runs": int(pr["abstention_pass"].notna().sum()),
            "gold_recall_mean": _nanmean(pr["gold_recall"]),
            "gold_precision_mean": _nanmean(pr["gold_precision"]),
            "wrong_patient_memories_mean": _nanmean(pr["wrong_patient_memories"]),
            "tool_calls_median": _nanmedian(pr["n_tool_calls"]),
            "latency_s_median": _nanmedian(pr["latency_s"]),
        })
    return pd.DataFrame(rows)


def holm(pvals) -> np.ndarray:
    """Holm step-down adjusted p-values. NaN entries are left out of the family."""
    p = np.asarray(pvals, dtype=float)
    adjusted = np.full(p.shape, np.nan)
    finite = np.flatnonzero(np.isfinite(p))
    m = finite.size
    order = finite[np.argsort(p[finite], kind="stable")]
    running = 0.0
    for rank, idx in enumerate(order):
        running = max(running, (m - rank) * p[idx])
        adjusted[idx] = min(1.0, running)
    return adjusted


def _wilcoxon(d: np.ndarray) -> tuple[float, float]:
    if d.size == 0:
        return _nan(), _nan()
    if np.all(d == 0):
        return _nan(), 1.0  # no non-zero differences: nothing to test
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        result = wilcoxon(d, zero_method="zsplit")
    return float(result.statistic), float(result.pvalue)


def build_tests(per_q: pd.DataFrame) -> pd.DataFrame:
    lookup = _quality_lookup(per_q)
    qids = sorted(set(per_q["qid"]))
    arms = set(per_q["arm"])
    levels = sorted(set(per_q["level"]), key=_level_key)
    rows = []
    for level in levels:
        for arm in COMPARATORS:
            if arm not in arms or BASE_ARM not in arms:
                continue
            column = paired_matrix(lookup, arm, [level], qids)[:, 0]
            d = column[np.isfinite(column)]
            mean_d, lo, hi = bootstrap_mean_ci(d)
            stat, p = _wilcoxon(d)
            rows.append({"level": level, "comparison": f"{arm}_vs_{BASE_ARM}", "n": int(d.size),
                         "mean_d": mean_d, "mean_d_ci_low": lo, "mean_d_ci_high": hi,
                         "wilcoxon_stat": stat, "p_raw": p})
    df = pd.DataFrame(rows, columns=["level", "comparison", "n", "mean_d", "mean_d_ci_low",
                                     "mean_d_ci_high", "wilcoxon_stat", "p_raw"])
    df["p_holm"] = holm(df["p_raw"].to_numpy(dtype=float)) if len(df) else []
    df["significant_holm_0.05"] = df["p_holm"] < 0.05
    return df


def build_per_use_case(per_q: pd.DataFrame, questions: dict[str, dict]) -> pd.DataFrame:
    names = {q["use_case"]: q.get("use_case_name", "") for q in questions.values()}
    grouped = per_q.groupby(["use_case", "arm", "level"])["quality"]
    out = pd.DataFrame({"n_questions": grouped.count(), "mean_quality": grouped.mean()}).reset_index()
    out.insert(1, "use_case_name", out["use_case"].map(names))
    return out.assign(_a=out["arm"].map(_arm_key), _l=out["level"].map(_level_key)) \
        .sort_values(["use_case", "_a", "_l"], kind="stable").drop(columns=["_a", "_l"])


# ---------------------------------------------------------------- orchestration

def run_analysis(runs: list[dict], judgments: dict[str, dict], questions: dict[str, dict],
                 levels_json: dict | None = None, cost_fn=None) -> dict:
    runs, _ = drop_dry_runs(runs)   # dry runs are never analysed, whichever entry point is used
    per_run = build_per_run(runs, judgments, questions, cost_fn=cost_fn)
    per_q = build_per_question(per_run)
    sizes, size_source = level_sizes(levels_json, per_run)
    return {
        "per_run": per_run,
        "per_question": per_q,
        "summary": build_summary(per_run, per_q, sizes),
        "crossover": build_crossover(per_run, per_q, sizes, size_source),
        "tests": build_tests(per_q),
        "per_use_case": build_per_use_case(per_q, questions),
    }


def write_outputs(results: dict, out_dir: Path) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    results["per_run"].to_csv(out_dir / "per_run.csv", index=False)
    results["per_question"].to_csv(out_dir / "per_question.csv", index=False)
    results["summary"].to_csv(out_dir / "summary.csv", index=False)
    results["tests"].to_csv(out_dir / "tests.csv", index=False)
    results["per_use_case"].to_csv(out_dir / "per_use_case.csv", index=False)
    (out_dir / "crossover.json").write_text(json.dumps(_jsonable(results["crossover"]), indent=2),
                                            encoding="utf-8")


def _resolve(path: Path) -> Path:
    return path if path.is_absolute() else ROOT / path


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Summarise judged runs (tables, crossovers, tests).")
    ap.add_argument("--runs", type=Path, required=True)
    ap.add_argument("--judgments", type=Path, required=True)
    ap.add_argument("--out", type=Path, required=True, help="output directory")
    ap.add_argument("--questions", type=Path, default=DEFAULT_QUESTIONS)
    ap.add_argument("--levels", type=Path, default=DEFAULT_LEVELS)
    args = ap.parse_args(argv)

    # Dry runs are dropped before the per-run_id dedupe, so they cannot overwrite a real run.
    rows, n_dry = drop_dry_runs(read_jsonl(_resolve(args.runs)))
    if n_dry:
        print(f"note: dropped {n_dry} dry-run record(s) from {args.runs} before analysis")
    runs = list(latest_by_run_id(rows).values())
    if not runs:
        print(f"error: no analysable runs in {args.runs} (all records are dry runs or the file is empty)",
              file=sys.stderr)
        return 1
    judgments = latest_judgments(read_jsonl(_resolve(args.judgments)))
    questions = {q["qid"]: q for q in json.loads(_resolve(args.questions).read_text(encoding="utf-8"))}
    levels_path = _resolve(args.levels)
    levels_json = json.loads(levels_path.read_text(encoding="utf-8")) if levels_path.exists() else None
    if levels_json is None:
        print(f"note: {levels_path} not found; store sizes come from median CTX prompt tokens")

    unjudged = [r["run_id"] for r in runs if r["run_id"] not in judgments]
    if unjudged:
        print(f"warning: {len(unjudged)} runs have no judgment; their quality is blank")

    results = run_analysis(runs, judgments, questions, levels_json)
    out_dir = _resolve(args.out)
    write_outputs(results, out_dir)
    print(f"wrote per_run, per_question, summary, crossover.json, tests, per_use_case to {out_dir}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
