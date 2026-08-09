"""Rule Engine: BIMデータの実測値と、参考値としての法規チェック(`code_engine.py`)を
PASS/FAIL/UNKNOWN判定として構造化し、Legal Knowledge Builder側のRule(どの法令の
どの条文に基づく参考値か)をlegal_sourcesとして結果に添付する。

計算式自体はLLMが行うのではなく、`code_engine.py`と同じくPythonの定数+比較式で
決定的に評価する(このモジュールは判定式を変えない。既存チェックの結果を
構造化し、法令根拠を紐付けるだけ)。legal_sourcesはLegal Knowledge Builder側の
Rule(正規表現抽出、confidence付き)をそのまま転記するだけで、この判定式が
その条文から自動導出されたことを意味しない(自動導出はしていない。あくまで
「この参考値に関連しそうな条文」の一覧)。

Legal Knowledge Builder(別プロセス)が未接続・未起動の場合でも判定自体は
継続し、legal_sourcesが空になるだけにする(`legal_mcp/client.py`と同じ
「未接続でもクラッシュしない」方針)。
"""

from __future__ import annotations

from enum import Enum

from ..legal_mcp import client as legal_client
from .code_engine import check_accessible_door_width, check_daylighting


class RuleCheckStatus(str, Enum):
    PASS = "pass"
    FAIL = "fail"
    UNKNOWN = "unknown"  # 判定に必要な実測値が欠けている(例: 幅が取得できないドア)
    NOT_APPLICABLE = "not_applicable"  # このBIMモデルに対象要素が存在しない


def _status_from_bool(meets: bool | None) -> RuleCheckStatus:
    if meets is None:
        return RuleCheckStatus.UNKNOWN
    return RuleCheckStatus.PASS if meets else RuleCheckStatus.FAIL


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


async def run_daylighting_check() -> dict:
    result = check_daylighting()
    legal_sources = await _legal_sources_for_concept("daylighting")

    items = [
        {
            "target_guid": room["room_guid"],
            "target_name": room["room_name"],
            "status": _status_from_bool(room["meets_reference_ratio"]).value,
            "measured_value": room["ratio"],
            "unit": "ratio",
            "evidence": {
                "floor_area_m2": room["floor_area_m2"],
                "window_area_m2": room["window_area_m2"],
                "window_count": room["window_count"],
            },
        }
        for room in result["rooms"]
    ]

    return {
        "concept_id": "daylighting",
        "threshold": result["reference_ratio"],
        "threshold_unit": "ratio",
        "disclaimer": result["disclaimer"],
        "legal_sources": legal_sources,
        "items": items,
    }


async def run_accessible_door_width_check() -> dict:
    result = check_accessible_door_width()
    legal_sources = await _legal_sources_for_concept("barrier_free")

    items = [
        {
            "target_guid": door["door_guid"],
            "target_name": door["door_name"],
            "status": _status_from_bool(door["meets_reference_width"]).value,
            "measured_value": door["width_m"],
            "unit": "m",
            "evidence": {},
        }
        for door in result["doors"]
    ]

    return {
        "concept_id": "barrier_free",
        "threshold": result["reference_min_width_m"],
        "threshold_unit": "m",
        "disclaimer": result["disclaimer"],
        "legal_sources": legal_sources,
        "items": items,
    }
