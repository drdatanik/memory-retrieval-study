"""Per-turn and per-run cost under four pricing scenarios, plus the provider-reported cost.

Rates come from config.PRICE_CARDS[role], in USD per million tokens (keys: input, output, cache_read,
cache_write). There is no price boundary: one flat card per role. Token fields are read from a turn as
logged by the harness:

    input_tokens, cache_creation_input_tokens, cache_read_input_tokens, output_tokens, cacheable_prefix_tokens

output_tokens already includes reasoning tokens (OpenAI convention), so reasoning is not added again.
Missing or None fields count as 0. Prompt tokens P = input + cache_creation + cache_read.

Scenarios (C = cacheable prefix, capped at P):
    observed     what the provider actually cached: uncached input at input, cache writes at cache_write,
                 cache reads at cache_read, output at output.
    uncached     every prompt token at the input rate, output at output (no caching at all).
    cached_cold  the store is written to the cache for every question: the first turn of a run writes its
                 prefix (C at cache_write, rest at input); later turns read the previous prefix (as cached_warm).
    cached_warm  an ideal prefix cache in steady traffic: C at cache_read, the rest at input.

cached_cold and cached_warm use cacheable_prefix_tokens. If a turn does not log it (missing or None), they
fall back to the observed cache fields: cached_warm reads cache creation and cache read tokens at cache_read,
cached_cold writes them at cache_write.
"""

from __future__ import annotations

from src import config

SCENARIOS = ("observed", "uncached", "cached_cold", "cached_warm")


def _tokens(turn: dict, key: str):
    return turn.get(key) or 0


def _card(role: str) -> dict:
    try:
        return config.PRICE_CARDS[role]
    except KeyError:
        raise ValueError(f"unknown pricing role: {role!r}") from None


def _check_scenario(scenario: str) -> None:
    if scenario not in SCENARIOS:
        raise ValueError(f"unknown pricing scenario: {scenario!r}")


def _prefix(turn: dict, prompt_tokens: int) -> int | None:
    """The turn's cacheable prefix capped to [0, prompt_tokens], or None when the turn does not log it."""
    raw = turn.get("cacheable_prefix_tokens")
    if raw is None:
        return None
    return max(0, min(raw, prompt_tokens))


def turn_cost(turn: dict, scenario: str, role: str = "subject", first_in_run: bool = True) -> float:
    """USD cost of one model request under one pricing scenario, at the rates of `role`.

    `first_in_run` matters only for cached_cold: True means this turn writes its prefix to the cache,
    False means it reads a prefix that an earlier turn of the run wrote.
    """
    _check_scenario(scenario)
    card = _card(role)
    ri, ro = card["input"], card["output"]
    crp, cwp = card["cache_read"], card["cache_write"]

    inp = _tokens(turn, "input_tokens")
    cw = _tokens(turn, "cache_creation_input_tokens")
    cr = _tokens(turn, "cache_read_input_tokens")
    out = _tokens(turn, "output_tokens")
    prompt = inp + cw + cr

    if scenario == "observed":
        usd = inp * ri + cw * cwp + cr * crp + out * ro
    elif scenario == "uncached":
        usd = prompt * ri + out * ro
    elif scenario == "cached_warm":
        prefix = _prefix(turn, prompt)
        if prefix is None:
            usd = inp * ri + (cw + cr) * crp + out * ro
        else:
            usd = prefix * crp + (prompt - prefix) * ri + out * ro
    else:  # cached_cold
        prefix = _prefix(turn, prompt)
        if prefix is None:
            usd = inp * ri + (cw + cr) * cwp + out * ro
        else:
            prefix_rate = cwp if first_in_run else crp
            usd = prefix * prefix_rate + (prompt - prefix) * ri + out * ro
    return usd / 1e6


def run_cost(turns: list[dict], scenario: str, role: str = "subject") -> float:
    """USD cost of a run: the sum of its turn costs under `scenario` (empty run is 0.0).

    Turn position matters for cached_cold: the turn at index 0 writes its prefix, the others read it.
    """
    _check_scenario(scenario)
    _card(role)
    return sum(
        (turn_cost(t, scenario, role, first_in_run=(i == 0)) for i, t in enumerate(turns or [])),
        0.0,
    )


def actual_cost(turns: list[dict]) -> float | None:
    """Sum of the provider-reported per-turn `cost_usd`, skipping turns where it is None or missing.

    Returns None when no turn has a value (all None, missing, or no turns). A 0.0 counts as a value.
    """
    values = [t.get("cost_usd") for t in (turns or [])]
    known = [float(v) for v in values if v is not None]
    return sum(known, 0.0) if known else None
