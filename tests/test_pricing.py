"""Hand-computed cost checks for src/pricing.py: four scenarios, cacheable prefix, cold/warm position,
subject and judge cards, None handling, and the provider-reported cost.

The tests replace config.PRICE_CARDS with fixed cards (the `fixed_cards` fixture), so the expected values
do not move when the study's real price cards change. The only test that reads the live cards checks their
shape, not their values.

Hand values are in micro-dollars (tokens x $/MTok) and divided by 1e6 to give USD.

Subject card: input 1.00, output 4.00, cache read 0.20, cache write 1.25 ($/MTok).
Judge card:   input 2.00, output 8.00, cache read 0.40, cache write 2.50 ($/MTok).

Turn A, used throughout: input 2,000, cache write 60,000, cache read 30,000, output 500, so P = 92,000.
"""

import pytest

from src import config, pricing

SUBJECT_CARD = {"input": 1.0, "output": 4.0, "cache_read": 0.2, "cache_write": 1.25}
JUDGE_CARD = {"input": 2.0, "output": 8.0, "cache_read": 0.4, "cache_write": 2.5}
PRICE_KEYS = ("input", "output", "cache_read", "cache_write")


@pytest.fixture
def fixed_cards(monkeypatch):
    monkeypatch.setattr(config, "PRICE_CARDS", {"subject": SUBJECT_CARD, "judge": JUDGE_CARD})


def turn(input_tokens=0, cache_write=0, cache_read=0, output=0, **extra):
    return {
        "input_tokens": input_tokens,
        "cache_creation_input_tokens": cache_write,
        "cache_read_input_tokens": cache_read,
        "output_tokens": output,
        **extra,
    }


def turn_a(**extra):
    return turn(input_tokens=2_000, cache_write=60_000, cache_read=30_000, output=500, **extra)


def test_scenario_names():
    assert pricing.SCENARIOS == ("observed", "uncached", "cached_cold", "cached_warm")


def test_live_config_has_all_four_price_keys_for_every_role():
    # Reads the real cards on purpose. Checks the shape only; the values are the study's and change over time.
    assert set(config.ROLES) <= set(config.PRICE_CARDS)
    for role in config.ROLES:
        card = config.PRICE_CARDS[role]
        assert set(PRICE_KEYS) <= set(card), role
        for key in PRICE_KEYS:
            assert isinstance(card[key], (int, float)) and card[key] >= 0, (role, key)


# ---------------------------------------------------------------- four scenarios

def test_subject_card_without_a_cacheable_prefix(fixed_cards):
    t = turn_a()
    # observed:    2,000*1 (2,000) + 60,000*1.25 (75,000) + 30,000*0.2 (6,000) + 500*4 (2,000) = 85,000
    assert pricing.turn_cost(t, "observed") == pytest.approx(85_000 / 1e6)
    # uncached:    92,000*1 (92,000) + 2,000 = 94,000
    assert pricing.turn_cost(t, "uncached") == pytest.approx(94_000 / 1e6)
    # cached cold, no prefix logged, so the observed cache fields are written at the write price:
    #              2,000 + 90,000*1.25 (112,500) + 2,000 = 116,500
    assert pricing.turn_cost(t, "cached_cold") == pytest.approx(116_500 / 1e6)
    # cached warm, no prefix logged, so the observed cache fields are read at the read price:
    #              2,000 + 90,000*0.2 (18,000) + 2,000 = 22,000
    assert pricing.turn_cost(t, "cached_warm") == pytest.approx(22_000 / 1e6)


@pytest.mark.parametrize("scenario", ["observed", "uncached", "cached_cold", "cached_warm"])
def test_none_prefix_is_the_same_as_a_missing_prefix(fixed_cards, scenario):
    assert pricing.turn_cost(turn_a(cacheable_prefix_tokens=None), scenario) == pytest.approx(
        pricing.turn_cost(turn_a(), scenario))


def test_subject_card_with_a_cacheable_prefix(fixed_cards):
    # C = 50,000 of P = 92,000, so 42,000 tokens are priced at input.
    t = turn_a(cacheable_prefix_tokens=50_000)
    # observed ignores the prefix:  85,000
    assert pricing.turn_cost(t, "observed") == pytest.approx(85_000 / 1e6)
    # uncached ignores the prefix:  94,000
    assert pricing.turn_cost(t, "uncached") == pytest.approx(94_000 / 1e6)
    # cached warm:  50,000*0.2 (10,000) + 42,000*1 (42,000) + 500*4 (2,000) = 54,000
    assert pricing.turn_cost(t, "cached_warm") == pytest.approx(54_000 / 1e6)
    # cached cold, first turn of a run writes the prefix:
    #               50,000*1.25 (62,500) + 42,000 + 2,000 = 106,500
    assert pricing.turn_cost(t, "cached_cold") == pytest.approx(106_500 / 1e6)
    assert pricing.turn_cost(t, "cached_cold", first_in_run=True) == pytest.approx(106_500 / 1e6)
    # cached cold, a later turn reads the prefix, so it equals cached warm: 54,000
    assert pricing.turn_cost(t, "cached_cold", first_in_run=False) == pytest.approx(54_000 / 1e6)


def test_cacheable_prefix_is_capped_at_the_prompt_size(fixed_cards):
    # C = min(10**9, 92,000) = 92,000, so the whole prompt is cacheable and nothing is priced at input.
    t = turn_a(cacheable_prefix_tokens=10 ** 9)
    # cached warm: 92,000*0.2 (18,400) + 500*4 (2,000) = 20,400
    assert pricing.turn_cost(t, "cached_warm") == pytest.approx(20_400 / 1e6)
    # cached cold, first turn: 92,000*1.25 (115,000) + 2,000 = 117,000
    assert pricing.turn_cost(t, "cached_cold") == pytest.approx(117_000 / 1e6)
    # A zero prefix means nothing is cacheable, so warm and cold both equal uncached: 94,000
    nothing = turn_a(cacheable_prefix_tokens=0)
    assert pricing.turn_cost(nothing, "cached_warm") == pytest.approx(94_000 / 1e6)
    assert pricing.turn_cost(nothing, "cached_cold") == pytest.approx(94_000 / 1e6)


def test_judge_card_all_four_scenarios(fixed_cards):
    t = turn_a()
    # observed:    2,000*2 (4,000) + 60,000*2.5 (150,000) + 30,000*0.4 (12,000) + 500*8 (4,000) = 170,000
    assert pricing.turn_cost(t, "observed", role="judge") == pytest.approx(170_000 / 1e6)
    # uncached:    92,000*2 (184,000) + 4,000 = 188,000
    assert pricing.turn_cost(t, "uncached", role="judge") == pytest.approx(188_000 / 1e6)
    # cached cold (no prefix): 4,000 + 90,000*2.5 (225,000) + 4,000 = 233,000
    assert pricing.turn_cost(t, "cached_cold", role="judge") == pytest.approx(233_000 / 1e6)
    # cached warm (no prefix): 4,000 + 90,000*0.4 (36,000) + 4,000 = 44,000
    assert pricing.turn_cost(t, "cached_warm", role="judge") == pytest.approx(44_000 / 1e6)
    # With C = 50,000 on the judge card:
    t_prefix = turn_a(cacheable_prefix_tokens=50_000)
    # cached warm:       50,000*0.4 (20,000) + 42,000*2 (84,000) + 4,000 = 108,000
    assert pricing.turn_cost(t_prefix, "cached_warm", role="judge") == pytest.approx(108_000 / 1e6)
    # cached cold, first: 50,000*2.5 (125,000) + 84,000 + 4,000 = 213,000
    assert pricing.turn_cost(t_prefix, "cached_cold", role="judge") == pytest.approx(213_000 / 1e6)


def test_first_in_run_only_changes_cached_cold(fixed_cards):
    t = turn_a(cacheable_prefix_tokens=50_000)
    for scenario in ("observed", "uncached", "cached_warm"):
        assert pricing.turn_cost(t, scenario, first_in_run=False) == pytest.approx(
            pricing.turn_cost(t, scenario, first_in_run=True))
    # Without a logged prefix, cached cold uses the observed fields whatever the position: 116,500
    assert pricing.turn_cost(turn_a(), "cached_cold", first_in_run=False) == pytest.approx(116_500 / 1e6)


def test_flat_card_has_no_price_boundary(fixed_cards):
    # The same per-token rate applies at 1,000 and at 2,000,000 prompt tokens.
    small = turn(input_tokens=1_000)
    huge = turn(input_tokens=2_000_000)
    for scenario in pricing.SCENARIOS:
        assert pricing.turn_cost(small, scenario) == pytest.approx(1_000 * 1.0 / 1e6)
        assert pricing.turn_cost(huge, scenario) == pytest.approx(2.0)


def test_reasoning_tokens_are_not_added_on_top_of_output_tokens(fixed_cards):
    # output_tokens already includes reasoning, so an extra reasoning field must not change the cost.
    plain = turn(input_tokens=1_000, output=500)
    with_reasoning = turn(input_tokens=1_000, output=500, reasoning_tokens=300)
    for scenario in pricing.SCENARIOS:
        # 1,000*1 (1,000) + 500*4 (2,000) = 3,000
        assert pricing.turn_cost(plain, scenario) == pytest.approx(3_000 / 1e6)
        assert pricing.turn_cost(with_reasoning, scenario) == pytest.approx(3_000 / 1e6)


def test_none_and_missing_fields_count_as_zero(fixed_cards):
    none_fields = {
        "input_tokens": None,
        "cache_creation_input_tokens": None,
        "cache_read_input_tokens": None,
        "output_tokens": None,
        "cacheable_prefix_tokens": None,
    }
    for role in ("subject", "judge"):
        for scenario in pricing.SCENARIOS:
            assert pricing.turn_cost(none_fields, scenario, role=role) == 0.0
            assert pricing.turn_cost({}, scenario, role=role) == 0.0
            # A logged prefix with no prompt tokens is still zero: C is capped at P = 0.
            assert pricing.turn_cost({"cacheable_prefix_tokens": 5_000}, scenario, role=role) == 0.0
    # Input and output only, cache fields missing: 1,000*1 (1,000) + 1,000*4 (4,000) = 5,000 in every scenario.
    partial = {"input_tokens": 1_000, "output_tokens": 1_000}
    for scenario in pricing.SCENARIOS:
        assert pricing.turn_cost(partial, scenario) == pytest.approx(5_000 / 1e6)
    # The same with a logged prefix of 500 (P = 1,000, C = 500):
    #   cached warm:       500*0.2 (100) + 500*1 (500) + 4,000 = 4,600
    #   cached cold, first: 500*1.25 (625) + 500 + 4,000 = 5,125
    with_prefix = {"input_tokens": 1_000, "output_tokens": 1_000, "cacheable_prefix_tokens": 500}
    assert pricing.turn_cost(with_prefix, "cached_warm") == pytest.approx(4_600 / 1e6)
    assert pricing.turn_cost(with_prefix, "cached_cold") == pytest.approx(5_125 / 1e6)
    assert pricing.turn_cost(with_prefix, "cached_cold", first_in_run=False) == pytest.approx(4_600 / 1e6)
    assert pricing.turn_cost(with_prefix, "observed") == pytest.approx(5_000 / 1e6)


# ---------------------------------------------------------------- runs

def test_run_cost_cached_warm_and_uncached(fixed_cards):
    t1 = turn(input_tokens=10_000, cache_read=80_000, output=2_000, cacheable_prefix_tokens=70_000)
    t2 = turn(input_tokens=150_000, output=1_000, cacheable_prefix_tokens=100_000)
    # uncached: t1 (90,000*1 + 2,000*4 = 98,000); t2 (150,000 + 4,000 = 154,000); total 252,000
    assert pricing.run_cost([t1, t2], "uncached") == pytest.approx(252_000 / 1e6)
    # cached warm: t1 (70,000*0.2 (14,000) + 20,000 + 8,000 = 42,000); t2 (100,000*0.2 (20,000) + 50,000 + 4,000 = 74,000)
    assert pricing.run_cost([t1, t2], "cached_warm") == pytest.approx(116_000 / 1e6)
    assert pricing.run_cost([t1, t2], "cached_warm") == pytest.approx(
        pricing.turn_cost(t1, "cached_warm") + pricing.turn_cost(t2, "cached_warm"))


def test_run_cost_cached_cold_writes_the_first_turn_and_reads_later_turns(fixed_cards):
    t1 = turn(input_tokens=10_000, cache_read=80_000, output=2_000, cacheable_prefix_tokens=70_000)
    t2 = turn(input_tokens=150_000, output=1_000, cacheable_prefix_tokens=100_000)
    # t1 is first and writes: 70,000*1.25 (87,500) + 20,000 + 2,000*4 (8,000) = 115,500
    # t2 is later and reads:  100,000*0.2 (20,000) + 50,000 + 1,000*4 (4,000) = 74,000
    assert pricing.run_cost([t1, t2], "cached_cold") == pytest.approx(189_500 / 1e6)
    assert pricing.run_cost([t1, t2], "cached_cold") == pytest.approx(
        pricing.turn_cost(t1, "cached_cold", first_in_run=True)
        + pricing.turn_cost(t2, "cached_cold", first_in_run=False))
    # Position is list order: t2 first writes 100,000*1.25 (125,000) + 50,000 + 4,000 = 179,000,
    # then t1 reads 42,000, so the run is 221,000.
    assert pricing.run_cost([t2, t1], "cached_cold") == pytest.approx(221_000 / 1e6)


def test_run_cost_mixes_prefix_and_fallback_turns(fixed_cards):
    t1 = turn(input_tokens=10_000, cache_read=80_000, output=2_000, cacheable_prefix_tokens=70_000)
    t3 = turn(input_tokens=5_000, cache_read=1_000, output=100)   # no prefix logged
    # t1 first, cached cold: 115,500. t3 has no prefix, so it uses the observed fields at the write price
    # wherever it sits: 5,000*1 + 1,000*1.25 (1,250) + 100*4 (400) = 6,650. Total 122,150.
    assert pricing.run_cost([t1, t3], "cached_cold") == pytest.approx(122_150 / 1e6)
    # cached warm: t1 42,000; t3 is 5,000 + 1,000*0.2 (200) + 400 = 5,600. Total 47,600.
    assert pricing.run_cost([t1, t3], "cached_warm") == pytest.approx(47_600 / 1e6)


def test_run_cost_judge_role_uses_the_judge_card(fixed_cards):
    t1 = turn_a()
    assert pricing.run_cost([t1], "observed", role="judge") == pytest.approx(170_000 / 1e6)
    assert pricing.run_cost([t1], "observed") == pytest.approx(85_000 / 1e6)


def test_run_cost_of_no_turns_is_zero_float(fixed_cards):
    for scenario in pricing.SCENARIOS:
        cost = pricing.run_cost([], scenario)
        assert cost == 0.0
        assert isinstance(cost, float)


# ---------------------------------------------------------------- rejected inputs

def test_unknown_scenario_is_rejected(fixed_cards):
    with pytest.raises(ValueError):
        pricing.turn_cost(turn(input_tokens=1), "cached")
    with pytest.raises(ValueError):
        pricing.run_cost([turn(input_tokens=1)], "warm")
    # Also rejected when there are no turns to price.
    with pytest.raises(ValueError):
        pricing.run_cost([], "warm")


def test_unknown_role_is_rejected(fixed_cards):
    with pytest.raises(ValueError):
        pricing.turn_cost(turn(input_tokens=1), "observed", role="generator_v2")
    with pytest.raises(ValueError):
        pricing.turn_cost(turn(input_tokens=1), "cached_cold", role="generator_v2")
    with pytest.raises(ValueError):
        pricing.run_cost([turn(input_tokens=1)], "observed", role="generator_v2")
    with pytest.raises(ValueError):
        pricing.run_cost([], "observed", role="generator_v2")


# ---------------------------------------------------------------- provider-reported cost

def test_actual_cost_sums_known_values_and_skips_none_and_missing():
    turns = [{"cost_usd": 0.01}, {"cost_usd": None}, {}, {"cost_usd": 0.02}]
    assert pricing.actual_cost(turns) == pytest.approx(0.03)


def test_actual_cost_is_none_when_no_turn_has_a_value():
    assert pricing.actual_cost([{"cost_usd": None}, {}]) is None
    assert pricing.actual_cost([{"input_tokens": 1_000, "output_tokens": 10}]) is None
    assert pricing.actual_cost([]) is None


def test_actual_cost_counts_zero_as_a_value():
    assert pricing.actual_cost([{"cost_usd": 0.0}]) == 0.0
    assert pricing.actual_cost([{"cost_usd": 0.0}, {}]) == 0.0


def test_actual_cost_ignores_token_counts():
    # Token fields are priced by the scenarios, never summed into the actual cost.
    t = turn(input_tokens=1_000_000, output=1_000_000, cache_read=500_000)
    t["cost_usd"] = 0.25
    assert pricing.actual_cost([t]) == pytest.approx(0.25)
