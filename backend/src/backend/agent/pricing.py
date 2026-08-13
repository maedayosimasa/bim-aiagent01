"""Claude APIモデルごとの概算料金(USD/1Mトークン)。

Anthropic公式の1Mトークンあたり料金(2026-08時点)。ANTHROPIC_AGENT_MODEL
(既定claude-opus-5)を変更した場合に備え主要モデルを列挙しているが、
未登録モデルはcalc_cost_usd()がNoneを返し、呼び出し側は料金不明として扱う
(実測トークン数自体は常に記録するので、料金だけが欠ける形になる)。
"""

_PRICE_PER_MTOK_USD: dict[str, dict[str, float]] = {
    "claude-opus-5": {"input": 5.00, "output": 25.00},
    "claude-opus-4-8": {"input": 5.00, "output": 25.00},
    "claude-opus-4-7": {"input": 5.00, "output": 25.00},
    "claude-opus-4-6": {"input": 5.00, "output": 25.00},
    "claude-sonnet-5": {"input": 3.00, "output": 15.00},
    "claude-sonnet-4-6": {"input": 3.00, "output": 15.00},
    "claude-haiku-4-5": {"input": 1.00, "output": 5.00},
}


def calc_cost_usd(model: str, input_tokens: int, output_tokens: int) -> float | None:
    pricing = _PRICE_PER_MTOK_USD.get(model)

    if pricing is None:
        return None

    return (input_tokens * pricing["input"] + output_tokens * pricing["output"]) / 1_000_000
