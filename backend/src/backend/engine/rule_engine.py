"""Rule Engine: 法規チェックをコード(Python関数)ではなくJSON(`legal_rules.json`)で
宣言し、BIMデータの実測値と突き合わせてPASS/FAIL/UNKNOWN判定を行う。

各`LegalRule`は「どのBIM実測値を使うか(check)」「その値をどう判定するか
(verification: comparator+threshold+unit)」「どの法概念に基づく参考値か
(concept_id)」を宣言するだけの静的データで、Legal Knowledge Builderの
Rule(正規表現抽出、confidence付き)とは別物である。measured_valueの計算式
自体はLLMが行うのではなく、`check`に登録されたPython関数(`RULE_CHECK_REGISTRY`、
実体は`code_engine.py`)が決定的に計算する。JSON側は「この関数の結果をどの
閾値と比べるか」を宣言するだけで、判定式そのものをJSONから自動生成しては
いない(そこまでの汎化はしていない。新しいチェックを追加するには
RULE_CHECK_REGISTRYにPython関数を登録した上でlegal_rules.jsonにエントリを足す)。

legal_sourcesはLegal Knowledge Builder(`/rules/by_concept`)から取得した、
concept_idに紐づく法令Rule(law_id/node_id/raw_sentence/modality/confidence)
をそのまま転記するだけで、この判定式がその条文から自動導出されたことを
意味しない(あくまで「この参考値に関連しそうな条文」の一覧)。

Legal Knowledge Builder(別プロセス)が未接続・未起動の場合でも判定自体は
継続し、legal_sourcesが空になるだけにする(`legal_mcp/client.py`と同じ
「未接続でもクラッシュしない」方針)。
"""

from __future__ import annotations

import json
from collections.abc import Callable
from enum import Enum
from pathlib import Path

from pydantic import BaseModel

from ..legal_mcp import client as legal_client
from .code_engine import check_accessible_door_width, check_daylighting
from .effective_daylighting import calculate_effective_daylighting
from .legal_inputs import get_legal_input_definition, resolve_legal_input

_RULES_PATH = Path(__file__).parent / "legal_rules.json"


class RuleCheckStatus(str, Enum):
    PASS = "pass"
    FAIL = "fail"
    UNKNOWN = "unknown"  # 判定に必要な実測値が欠けている(例: 幅が取得できないドア)
    NOT_APPLICABLE = "not_applicable"  # このBIMモデルに対象要素が存在しない


class RuleComparator(str, Enum):
    GTE = "gte"
    LTE = "lte"
    GT = "gt"
    LT = "lt"
    EQ = "eq"


_COMPARATOR_FNS: dict[RuleComparator, Callable[[float, float], bool]] = {
    RuleComparator.GTE: lambda value, threshold: value >= threshold,
    RuleComparator.LTE: lambda value, threshold: value <= threshold,
    RuleComparator.GT: lambda value, threshold: value > threshold,
    RuleComparator.LT: lambda value, threshold: value < threshold,
    RuleComparator.EQ: lambda value, threshold: value == threshold,
}


class Verification(BaseModel):
    comparator: RuleComparator
    threshold: float
    unit: str | None = None


class LegalRule(BaseModel):
    rule_id: str
    title: str
    concept_id: str
    """Legal Knowledge Builderのontology concept_id(legal_sources取得キー)。"""
    applies_when: dict = {}
    """このルールが対象とするBIM要素の表示用メタデータ(例: entity_type)。
    現状は実際の絞り込みには使わない(絞り込みはcheck関数側が行う)。将来、
    複数checkを1関数に集約する際のフィルタ条件として使う余地を残している。"""
    check: str
    """RULE_CHECK_REGISTRYに登録されたPython関数のキー。実測値の計算は
    ここで指定された関数が行う(JSON側は計算式を持たない)。"""
    verification: Verification
    disclaimer: str
    required_inputs: list[str] = []
    """BIMデータからは判定できず、外部から明示的に与える必要がある法規条件
    のキー一覧(engine/legal_inputs.pyのLEGAL_INPUT_DEFINITIONSを参照)。
    evaluate_legal_rule()は判定を実行する前にこれらが揃っているか確認し、
    不足していればcheck関数を呼ばずにmissing_inputsとして返す。"""


def _compute_daylighting_ratio() -> list[dict]:
    result = check_daylighting()
    return [
        {
            "target_guid": room["room_guid"],
            "target_name": room["room_name"],
            "measured_value": room["ratio"],
            "evidence": {
                "floor_area_m2": room["floor_area_m2"],
                "window_area_m2": room["window_area_m2"],
                "window_count": room["window_count"],
            },
        }
        for room in result["rooms"]
    ]


def _compute_accessible_door_width() -> list[dict]:
    result = check_accessible_door_width()
    return [
        {
            "target_guid": door["door_guid"],
            "target_name": door["door_name"],
            "measured_value": door["width_m"],
            "evidence": {},
        }
        for door in result["doors"]
    ]


def _compute_effective_daylighting_ratio() -> list[dict]:
    result = calculate_effective_daylighting()
    return [
        {
            "target_guid": room["room_guid"],
            "target_name": room["room_name"],
            "measured_value": room["ratio"],
            "evidence": {
                "floor_area_m2": room["floor_area_m2"],
                "effective_window_area_m2": room["effective_window_area_m2"],
                "window_count": room["window_count"],
                "unresolved_window_count": room["unresolved_window_count"],
                "land_use_category": result["land_use_category"],
            },
        }
        for room in result["rooms"]
    ]


# checkキー→実測値を計算するPython関数。新しい法令Ruleを足す場合、まず
# ここに計算関数を登録してから legal_rules.json にエントリを追加する
# (逆にJSON側だけを足しても、対応するcheck関数が無ければevaluate_legal_rule()
# がエラーにする)。
RULE_CHECK_REGISTRY: dict[str, Callable[[], list[dict]]] = {
    "daylighting_ratio": _compute_daylighting_ratio,
    "accessible_door_width": _compute_accessible_door_width,
    "effective_daylighting_ratio": _compute_effective_daylighting_ratio,
}


def load_legal_rules() -> list[LegalRule]:
    data = json.loads(_RULES_PATH.read_text(encoding="utf-8"))
    return [LegalRule.model_validate(item) for item in data]


def get_legal_rule(rule_id: str) -> LegalRule | None:
    return next((rule for rule in load_legal_rules() if rule.rule_id == rule_id), None)


def _status_for(measured_value: float | None, verification: Verification) -> RuleCheckStatus:
    if measured_value is None:
        return RuleCheckStatus.UNKNOWN
    comparator_fn = _COMPARATOR_FNS[verification.comparator]
    return RuleCheckStatus.PASS if comparator_fn(measured_value, verification.threshold) else RuleCheckStatus.FAIL


async def _legal_sources_for_concept(concept_id: str) -> list[dict]:
    if not legal_client.is_configured():
        return []
    try:
        rules = await legal_client.get_rules_by_concept(concept_id)
    except Exception:
        # Legal Knowledge Builderが未起動・接続エラーでも判定自体は継続する
        # (legal_mcp/client.pyの他の呼び出しと同じ「クラッシュしない」方針)。
        return []
    return [
        {
            "rule_id": rule.get("rule_id"),
            "law_id": rule.get("law_id"),
            "node_id": rule.get("node_id"),
            "raw_sentence": (rule.get("raw_sentence") or "").strip(),
            "modality": rule.get("modality"),
            "confidence": rule.get("confidence"),
        }
        for rule in rules
    ]


def _missing_inputs(rule: LegalRule) -> list[dict]:
    missing = []

    for key in rule.required_inputs:
        if resolve_legal_input(key) is not None:
            continue
        definition = get_legal_input_definition(key)
        missing.append({
            "key": key,
            "label": definition.label if definition else key,
            "description": definition.description if definition else None,
        })

    return missing


async def evaluate_legal_rule(rule: LegalRule) -> dict:
    base_result = {
        "rule_id": rule.rule_id,
        "title": rule.title,
        "concept_id": rule.concept_id,
        "threshold": rule.verification.threshold,
        "threshold_unit": rule.verification.unit,
        "comparator": rule.verification.comparator.value,
        "disclaimer": rule.disclaimer,
    }

    # BIMデータからは判定できない法規条件(用途地域等)が不足している場合、
    # check関数を呼ばずに「何が足りないか」を返す。UNKNOWN(BIM実測値が
    # 個別要素で欠けている状態)とは異なり、判定そのものが原理的に開始
    # できない状態であることをAIエージェントに明確に伝えるため。
    missing_inputs = _missing_inputs(rule)

    if missing_inputs:
        return {**base_result, "legal_sources": [], "items": [], "missing_inputs": missing_inputs}

    compute = RULE_CHECK_REGISTRY.get(rule.check)
    if compute is None:
        raise ValueError(f"未登録のcheckです: {rule.check}(RULE_CHECK_REGISTRYに未登録)")

    measurements = compute()
    legal_sources = await _legal_sources_for_concept(rule.concept_id)

    items = [
        {
            "target_guid": m["target_guid"],
            "target_name": m.get("target_name"),
            "status": _status_for(m["measured_value"], rule.verification).value,
            "measured_value": m["measured_value"],
            "unit": rule.verification.unit,
            "evidence": m.get("evidence", {}),
        }
        for m in measurements
    ]

    return {**base_result, "legal_sources": legal_sources, "items": items, "missing_inputs": []}


async def evaluate_legal_rule_by_id(rule_id: str) -> dict:
    rule = get_legal_rule(rule_id)
    if rule is None:
        raise ValueError(f"未登録のルールです: {rule_id}")
    return await evaluate_legal_rule(rule)


async def run_daylighting_check() -> dict:
    return await evaluate_legal_rule_by_id("daylighting_ratio")


async def run_accessible_door_width_check() -> dict:
    return await evaluate_legal_rule_by_id("accessible_door_width")
