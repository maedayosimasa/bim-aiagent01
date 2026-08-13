from backend.agent.pricing import calc_cost_usd


def test_calc_cost_usd_opus_5():
    # $5/$25 per 1Mトークン(2026-08時点のAnthropic公式料金)。
    cost = calc_cost_usd("claude-opus-5", input_tokens=1_000_000, output_tokens=1_000_000)

    assert cost == 30.00


def test_calc_cost_usd_zero_tokens():
    assert calc_cost_usd("claude-opus-5", 0, 0) == 0.0


def test_calc_cost_usd_unknown_model_returns_none():
    assert calc_cost_usd("some-future-model", 100, 50) is None
