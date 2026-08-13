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

**(2026-08-13追加)`law_title`/`article`フィールド**: 元々`legal_sources`は
`law_id`(e-Govの法令ID、例: "325AC0000000201")と`raw_sentence`(条文本文、
条番号を含まない)のみを持っており、人間が読める条番号がどこにも無かった。
これは`agent/report_graph.py`のレポート生成LLMに「建築基準法第28条」のような
具体的な条文引用を書かせる材料が実際には与えられていない(LLMが書けば
それは取得した根拠ではなく一般知識由来である可能性が高い)ことを意味していた。
`node_id`(例: "325AC0000000201:Law:MP#1:Ch2:Art28:Para1")には条番号が
構造化された形で埋め込まれているため`_extract_article_label()`で抽出し、
`law_id`は`legal_client.list_laws()`(`GET /legal/laws`)で人間可読な法令名に
解決する。これにより`report_graph.py`のClaim Validator(Evidence Validation、
`_validate_citations()`)が「レポートが引用した条番号は、実際に取得した
legal_sourcesに含まれるか」を機械的に検証できるようになった。
"""

from __future__ import annotations

import json
import re
from collections.abc import Callable
from enum import Enum
from pathlib import Path

from pydantic import BaseModel

from ..legal_mcp import client as legal_client
from .adjacent_boundary_slant_envelope import calculate_adjacent_boundary_slant_compliance
from .building_coverage import calculate_building_coverage_ratio
from .code_engine import check_accessible_door_width, check_daylighting
from .effective_daylighting import calculate_effective_daylighting
from .evacuation_engine import compute_evacuation_walking_distances
from .evidence import EvidenceConfidence, tag as tag_evidence
from .floor_area_ratio import calculate_floor_area_ratio
from .height_district_envelope import calculate_height_district_compliance
from .legal_inputs import get_legal_input_definition, resolve_legal_input
from .north_slant_envelope import calculate_north_slant_compliance
from .road_slant_envelope import calculate_road_slant_compliance
from .site_frontage import calculate_site_road_frontage

_RULES_PATH = Path(__file__).parent / "legal_rules.json"

# node_idの例: "325AC0000000201:Law:MP#1:Ch2:Art28:Para1"
#              "325AC0000000201:Law:Suppl#95:Art1:Para1:Item2"(附則)
_ARTICLE_PATTERN = re.compile(r"(Suppl#\d+:)?Art(\d+)(?:_(\d+))?")


def _extract_article_label(node_id: str | None) -> str | None:
    """node_idから人間可読な条番号(例: "第28条"/"第1条の3"/"附則第95条")を抽出する。

    パターンに一致しない場合(将来Legal Knowledge Builder側のnode_id形式が
    変わった場合等)はNoneを返す(過検出よりも見逃しを許容する設計)。
    """
    if not node_id:
        return None
    m = _ARTICLE_PATTERN.search(node_id)
    if not m:
        return None
    is_suppl, main, sub = m.group(1), m.group(2), m.group(3)
    label = f"第{main}条の{sub}" if sub else f"第{main}条"
    return f"附則{label}" if is_suppl else label


async def _law_titles() -> dict[str, str]:
    if not legal_client.is_configured():
        return {}
    try:
        laws = await legal_client.list_laws()
    except Exception:
        # Legal Knowledge Builderが未起動・接続エラーでも判定自体は継続する。
        return {}
    return {law.get("law_id"): law.get("law_title") for law in laws}


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
    threshold: float | None = None
    unit: str | None = None
    threshold_from_input: str | None = None
    """(2026-08-13追加)thresholdをlegal_rules.jsonの静的値ではなく、
    engine/legal_inputs.pyのLEGAL_INPUT_DEFINITIONSからプロジェクト単位で
    解決する場合のキー(例: "yoseki_ritsu")。容積率のように、閾値自体が
    法令の固定値ではなく都市計画で指定される場合に使う(採光比1/7・
    ドア幅0.8m等の固定閾値はthresholdをそのまま使う)。設定されている場合、
    このキーはrequired_inputsと同様に不足チェックの対象になり、値は
    "%"表記(legal_inputs.pyの既存ドキュメント通り)として100で除して
    比率に変換してから比較する。thresholdとthreshold_from_inputは排他的
    (両方設定されている場合はthreshold_from_inputが優先される)。"""


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
    # (2026-08-13追加)換気の有効換気面積比(建築基準法28条2項)は採光
    # (28条1項)と同じ「窓面積/床面積」の単純比を使う(この単純化のレベルは
    # daylighting_ratioと同一、Archicad側にoperable/fixedの区別が無いため)。
    # 閾値(1/20)だけがlegal_rules.json側で異なるため、計算関数は共有する。
    "ventilation_ratio": _compute_daylighting_ratio,
    "floor_area_ratio": calculate_floor_area_ratio,
    "site_road_frontage": calculate_site_road_frontage,
    "evacuation_walking_distance": compute_evacuation_walking_distances,
    "building_coverage_ratio": calculate_building_coverage_ratio,
    # (2026-08-14追加)高さ制限4種。各calculate_*_compliance()は測定値として
    # 「実際の建物高さ - その位置での高さ上限」(m)を返す(threshold=0.0、
    # comparator="lte"と組み合わせ、0以下ならPASS)。戻り値の形状
    # (target_guid/target_name/measured_value/evidence)は他のcheck関数と
    # 完全に一致するため、追加のラッパーは不要。
    "road_slant_compliance": calculate_road_slant_compliance,
    "adjacent_boundary_slant_compliance": calculate_adjacent_boundary_slant_compliance,
    "north_slant_compliance": calculate_north_slant_compliance,
    "height_district_compliance": calculate_height_district_compliance,
}


def load_legal_rules() -> list[LegalRule]:
    data = json.loads(_RULES_PATH.read_text(encoding="utf-8"))
    return [LegalRule.model_validate(item) for item in data]


def get_legal_rule(rule_id: str) -> LegalRule | None:
    return next((rule for rule in load_legal_rules() if rule.rule_id == rule_id), None)


def _status_for(measured_value: float | None, comparator: RuleComparator, threshold: float) -> RuleCheckStatus:
    if measured_value is None:
        return RuleCheckStatus.UNKNOWN
    comparator_fn = _COMPARATOR_FNS[comparator]
    return RuleCheckStatus.PASS if comparator_fn(measured_value, threshold) else RuleCheckStatus.FAIL


async def _legal_sources_for_concept(concept_id: str) -> list[dict]:
    if not legal_client.is_configured():
        return []
    try:
        rules = await legal_client.get_rules_by_concept(concept_id)
    except Exception:
        # Legal Knowledge Builderが未起動・接続エラーでも判定自体は継続する
        # (legal_mcp/client.pyの他の呼び出しと同じ「クラッシュしない」方針)。
        return []
    law_titles = await _law_titles()
    return [
        {
            "rule_id": rule.get("rule_id"),
            "law_id": rule.get("law_id"),
            "law_title": law_titles.get(rule.get("law_id")),
            "node_id": rule.get("node_id"),
            "article": _extract_article_label(rule.get("node_id")),
            "raw_sentence": (rule.get("raw_sentence") or "").strip(),
            "modality": rule.get("modality"),
            "confidence": rule.get("confidence"),
        }
        for rule in rules
    ]


def _missing_inputs(rule: LegalRule) -> list[dict]:
    missing = []

    keys = list(rule.required_inputs)
    if rule.verification.threshold_from_input and rule.verification.threshold_from_input not in keys:
        keys.append(rule.verification.threshold_from_input)

    for key in keys:
        if resolve_legal_input(key) is not None:
            continue
        definition = get_legal_input_definition(key)
        missing.append({
            "key": key,
            "label": definition.label if definition else key,
            "description": definition.description if definition else None,
        })

    return missing


def _resolve_threshold(rule: LegalRule) -> float:
    """rule.verification.threshold_from_inputが設定されていれば、
    legal_inputs.pyから解決した値(%表記)を比率に変換して返す。未設定なら
    静的なverification.thresholdをそのまま返す。呼び出し前にmissing_inputs
    が空であることを確認済みである必要がある(値の存在は保証されている)。
    """
    if not rule.verification.threshold_from_input:
        return rule.verification.threshold

    raw = resolve_legal_input(rule.verification.threshold_from_input)
    try:
        return float(raw) / 100
    except (TypeError, ValueError) as exc:
        raise ValueError(
            f"{rule.verification.threshold_from_input}の値が数値として解釈できません: "
            f"{raw!r}(%表記の数値を設定してください、例: 200)"
        ) from exc


async def evaluate_legal_rule(rule: LegalRule) -> dict:
    # BIMデータからは判定できない法規条件(用途地域等)が不足している場合、
    # check関数を呼ばずに「何が足りないか」を返す。UNKNOWN(BIM実測値が
    # 個別要素で欠けている状態)とは異なり、判定そのものが原理的に開始
    # できない状態であることをAIエージェントに明確に伝えるため。
    # (2026-08-13追加)threshold_from_inputが設定されているルールは、
    # 閾値自体の不足もmissing_inputsに含める(容積率等)。
    missing_inputs = _missing_inputs(rule)

    base_result = {
        "rule_id": rule.rule_id,
        "title": rule.title,
        "concept_id": rule.concept_id,
        "threshold": None if missing_inputs else _resolve_threshold(rule),
        "threshold_unit": rule.verification.unit,
        "comparator": rule.verification.comparator.value,
        "disclaimer": rule.disclaimer,
    }

    if missing_inputs:
        return {**base_result, "legal_sources": [], "items": [], "missing_inputs": missing_inputs}

    compute = RULE_CHECK_REGISTRY.get(rule.check)
    if compute is None:
        raise ValueError(f"未登録のcheckです: {rule.check}(RULE_CHECK_REGISTRYに未登録)")

    threshold = base_result["threshold"]
    measurements = compute()
    legal_sources = tag_evidence(
        await _legal_sources_for_concept(rule.concept_id), EvidenceConfidence.CANDIDATE
    )

    items = tag_evidence(
        [
            {
                "target_guid": m["target_guid"],
                "target_name": m.get("target_name"),
                "status": _status_for(m["measured_value"], rule.verification.comparator, threshold).value,
                "measured_value": m["measured_value"],
                "unit": rule.verification.unit,
                "evidence": m.get("evidence", {}),
            }
            for m in measurements
        ],
        EvidenceConfidence.DETERMINISTIC,
    )

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
