import asyncio
import json

import pytest

from backend.engine import rule_engine
from backend.legal_mcp import client as legal_client


def test_status_for_uses_comparator():
    comparator = rule_engine.RuleComparator.GTE

    assert rule_engine._status_for(0.9, comparator, 0.8) is rule_engine.RuleCheckStatus.PASS
    assert rule_engine._status_for(0.7, comparator, 0.8) is rule_engine.RuleCheckStatus.FAIL
    assert rule_engine._status_for(None, comparator, 0.8) is rule_engine.RuleCheckStatus.UNKNOWN


def test_load_legal_rules_returns_known_rules():
    rules = {r.rule_id: r for r in rule_engine.load_legal_rules()}

    assert set(rules) == {
        "daylighting_ratio", "accessible_door_width", "effective_daylighting_ratio",
        "ventilation_ratio", "floor_area_ratio", "site_road_frontage",
        "evacuation_walking_distance",
    }
    assert rules["daylighting_ratio"].check == "daylighting_ratio"
    assert rules["daylighting_ratio"].concept_id == "daylighting"
    assert rules["accessible_door_width"].verification.threshold == 0.8
    assert rules["effective_daylighting_ratio"].check == "effective_daylighting_ratio"
    assert rules["effective_daylighting_ratio"].concept_id == "daylighting"
    assert rules["ventilation_ratio"].check == "ventilation_ratio"
    assert rules["ventilation_ratio"].verification.threshold == 0.05
    assert rules["floor_area_ratio"].verification.threshold_from_input == "yoseki_ritsu"
    assert rules["site_road_frontage"].verification.threshold == 2.0
    assert rules["evacuation_walking_distance"].verification.threshold == 30.0


def test_get_legal_rule_returns_none_for_unknown_id():
    assert rule_engine.get_legal_rule("does_not_exist") is None


def test_legal_sources_for_concept_empty_when_not_configured(monkeypatch):
    monkeypatch.delenv("LEGAL_API_URL", raising=False)
    legal_client.set_connection_url(None)

    sources = asyncio.run(rule_engine._legal_sources_for_concept("daylighting"))

    assert sources == []


def test_legal_sources_for_concept_empty_when_backend_unreachable(monkeypatch):
    monkeypatch.setenv("LEGAL_API_URL", "http://legal.example.invalid")

    async def failing_get_rules_by_concept(concept_id):
        raise RuntimeError("connection refused")

    monkeypatch.setattr(legal_client, "get_rules_by_concept", failing_get_rules_by_concept)

    sources = asyncio.run(rule_engine._legal_sources_for_concept("daylighting"))

    assert sources == []


def test_legal_sources_for_concept_maps_fields(monkeypatch):
    monkeypatch.setenv("LEGAL_API_URL", "http://legal.example.invalid")

    async def fake_get_rules_by_concept(concept_id):
        assert concept_id == "daylighting"
        return [
            {
                "rule_id": "rule:1",
                "law_id": "325AC0000000201",
                "node_id": "325AC0000000201:Law:MP#1:Ch2:Art28:Para1",
                "raw_sentence": "  居室には採光のための窓その他の開口部を設けなければならない。  ",
                "modality": "obligation",
                "conditions": [],
                "concept_ids": ["daylighting"],
                "confidence": 0.5,
            }
        ]

    async def fake_list_laws():
        return [{"law_id": "325AC0000000201", "law_title": "建築基準法"}]

    monkeypatch.setattr(legal_client, "get_rules_by_concept", fake_get_rules_by_concept)
    monkeypatch.setattr(legal_client, "list_laws", fake_list_laws)

    sources = asyncio.run(rule_engine._legal_sources_for_concept("daylighting"))

    assert sources == [
        {
            "rule_id": "rule:1",
            "law_id": "325AC0000000201",
            "law_title": "建築基準法",
            "node_id": "325AC0000000201:Law:MP#1:Ch2:Art28:Para1",
            "article": "第28条",
            "raw_sentence": "居室には採光のための窓その他の開口部を設けなければならない。",
            "modality": "obligation",
            "confidence": 0.5,
        }
    ]


def test_legal_sources_for_concept_law_titles_unavailable_leaves_none(monkeypatch):
    # list_laws()が失敗しても(接続エラー等)判定自体は継続し、law_titleが
    # Noneになるだけにする(legal_mcp/client.pyの「未接続でもクラッシュ
    # しない」方針)。
    monkeypatch.setenv("LEGAL_API_URL", "http://legal.example.invalid")

    async def fake_get_rules_by_concept(concept_id):
        return [{
            "rule_id": "rule:1", "law_id": "325AC0000000201",
            "node_id": "325AC0000000201:Law:MP#1:Ch2:Art28:Para1",
            "raw_sentence": "text", "modality": "obligation", "confidence": 0.5,
        }]

    async def failing_list_laws():
        raise RuntimeError("connection refused")

    monkeypatch.setattr(legal_client, "get_rules_by_concept", fake_get_rules_by_concept)
    monkeypatch.setattr(legal_client, "list_laws", failing_list_laws)

    sources = asyncio.run(rule_engine._legal_sources_for_concept("daylighting"))

    assert sources[0]["law_title"] is None
    assert sources[0]["article"] == "第28条"


def test_extract_article_label_handles_various_node_id_shapes():
    assert rule_engine._extract_article_label(
        "325AC0000000201:Law:MP#1:Ch2:Art28:Para1"
    ) == "第28条"
    assert rule_engine._extract_article_label(
        "325M50004000040:Law:MP#1:Art1_3:Para1"
    ) == "第1条の3"
    assert rule_engine._extract_article_label(
        "325AC0000000201:Law:Suppl#95:Art1:Para1:Item2"
    ) == "附則第1条"
    assert rule_engine._extract_article_label("no-article-here") is None
    assert rule_engine._extract_article_label(None) is None


def test_run_daylighting_check_structures_result(test_db, monkeypatch):
    monkeypatch.delenv("LEGAL_API_URL", raising=False)
    legal_client.set_connection_url(None)

    test_db.insert_element(
        "room1", "Room", "居室A",
        json.dumps({}),
        json.dumps({"type": "polygon", "points": [[0, 0], [10000, 0], [10000, 5000], [0, 5000]]}),
    )
    test_db.insert_element(
        "window1", "Window", "窓",
        json.dumps({"archicad_details": {"width": 2, "height": 1}}),
        json.dumps({"type": "point", "x": 0, "y": 2500}),
    )

    result = asyncio.run(rule_engine.run_daylighting_check())

    assert result["concept_id"] == "daylighting"
    assert result["threshold"] == 1 / 7
    assert result["legal_sources"] == []
    item = result["items"][0]
    assert item["target_guid"] == "room1"
    assert item["status"] == "fail"  # 2/50 < 1/7
    assert item["evidence"]["window_count"] == 1
    # (2026-08-13追加)Evidence Layer: BIM実測値から直接算出した項目は
    # deterministicタグが付く(engine/evidence.py)。
    assert item["evidence_confidence"] == "deterministic"


def test_evaluate_legal_rule_by_id_raises_for_unknown_rule():
    try:
        asyncio.run(rule_engine.evaluate_legal_rule_by_id("does_not_exist"))
        assert False, "ValueErrorが発生するはず"
    except ValueError:
        pass


def test_evaluate_effective_daylighting_ratio_via_rule_id(test_db, monkeypatch):
    monkeypatch.delenv("LEGAL_API_URL", raising=False)
    monkeypatch.setenv("LAND_USE_CATEGORY", "residential")
    legal_client.set_connection_url(None)

    test_db.insert_element(
        "room1", "Room", "居室A",
        json.dumps({}),
        json.dumps({"type": "polygon", "points": [[0, 0], [10000, 0], [10000, 5000], [0, 5000]]}),
    )
    # owner壁(ownerElementId)を持たないため所属壁を特定できず、D/Hが
    # 計算できない(未解決)。engine/effective_daylighting.pyの詳しい
    # 幾何シナリオ(D/H/係数の実際の計算)はtest_effective_daylighting.py参照。
    test_db.insert_element(
        "window1", "Window", "窓",
        json.dumps({"archicad_details": {"width": 2, "height": 1}}),
        json.dumps({"type": "point", "x": 0, "y": 2500, "z_min": 900, "z_max": 2000}),
    )

    result = asyncio.run(rule_engine.evaluate_legal_rule_by_id("effective_daylighting_ratio"))

    assert result["concept_id"] == "daylighting"
    assert result["threshold"] == 1 / 7
    assert result["missing_inputs"] == []
    item = result["items"][0]
    assert item["target_guid"] == "room1"
    assert item["status"] == "unknown"
    assert item["evidence"]["unresolved_window_count"] == 1
    assert item["evidence"]["land_use_category"] == "residential"


def test_evaluate_effective_daylighting_ratio_reports_missing_inputs(test_db, monkeypatch):
    monkeypatch.delenv("LEGAL_API_URL", raising=False)
    monkeypatch.delenv("LAND_USE_CATEGORY", raising=False)
    legal_client.set_connection_url(None)

    test_db.insert_element(
        "room1", "Room", "居室A",
        json.dumps({}),
        json.dumps({"type": "polygon", "points": [[0, 0], [10000, 0], [10000, 5000], [0, 5000]]}),
    )

    result = asyncio.run(rule_engine.evaluate_legal_rule_by_id("effective_daylighting_ratio"))

    assert result["items"] == []
    assert result["legal_sources"] == []
    assert result["missing_inputs"] == [
        {
            "key": "land_use_category",
            "label": "用途地域",
            "description": rule_engine.get_legal_input_definition("land_use_category").description,
        }
    ]


def test_evaluate_legal_rule_without_required_inputs_has_empty_missing_inputs(test_db, monkeypatch):
    monkeypatch.delenv("LEGAL_API_URL", raising=False)
    legal_client.set_connection_url(None)

    test_db.insert_element(
        "door_wide", "Door", "広いドア",
        json.dumps({"archicad_details": {"width": 0.9}}),
        json.dumps({"type": "point", "x": 0, "y": 0}),
    )

    result = asyncio.run(rule_engine.evaluate_legal_rule_by_id("accessible_door_width"))

    assert result["missing_inputs"] == []
    assert result["items"] != []


def test_run_accessible_door_width_check_structures_result(test_db, monkeypatch):
    monkeypatch.delenv("LEGAL_API_URL", raising=False)
    legal_client.set_connection_url(None)

    test_db.insert_element(
        "door_wide", "Door", "広いドア",
        json.dumps({"archicad_details": {"width": 0.9}}),
        json.dumps({"type": "point", "x": 0, "y": 0}),
    )

    result = asyncio.run(rule_engine.run_accessible_door_width_check())

    assert result["concept_id"] == "barrier_free"
    assert result["threshold"] == 0.8
    item = result["items"][0]
    assert item["target_guid"] == "door_wide"
    assert item["status"] == "pass"


def test_evaluate_legal_rule_tags_legal_sources_as_candidate(test_db, monkeypatch):
    # (2026-08-13追加)Evidence Layer: legal_sourcesは正規表現抽出によるノイズを
    # 含む候補であり確定的な根拠ではないため、candidateタグが付く
    # (engine/evidence.py)。BIM実測値から算出するitemsのdeterministicタグとは
    # 明確に区別する。
    monkeypatch.setenv("LEGAL_API_URL", "http://legal.example.invalid")

    async def fake_get_rules_by_concept(concept_id):
        return [{
            "rule_id": "rule:1", "law_id": "325AC0000000201", "node_id": "n1",
            "raw_sentence": "居室には採光のための開口部を設けなければならない。",
            "modality": "obligation", "confidence": 0.5,
        }]

    monkeypatch.setattr(legal_client, "get_rules_by_concept", fake_get_rules_by_concept)

    test_db.insert_element(
        "room1", "Room", "居室A",
        json.dumps({}),
        json.dumps({"type": "polygon", "points": [[0, 0], [10000, 0], [10000, 5000], [0, 5000]]}),
    )

    result = asyncio.run(rule_engine.run_daylighting_check())

    source = result["legal_sources"][0]
    assert source["evidence_confidence"] == "candidate"
    assert source["confidence"] == 0.5  # Legal Knowledge Builder側の抽出confidenceとは別物


# (2026-08-13追加)容積率・接道長さ・避難歩行距離・換気の4チェックの回帰テスト。
# 個々の計算ロジック自体はengine/floor_area_ratio.py・site_frontage.py・
# evacuation_engine.pyで別途テスト済みのため、ここではRule Engine経由の
# 配線(threshold_from_input解決・missing_inputs・固定閾値との比較)を検証する。

def test_evaluate_floor_area_ratio_reports_missing_inputs_when_yoseki_ritsu_unset(test_db, monkeypatch):
    monkeypatch.delenv("YOSEKI_RITSU", raising=False)

    test_db.insert_element(
        "site1", "Zone", "敷地",
        json.dumps({}),
        json.dumps({"type": "polygon", "points": [[0, 0], [10000, 0], [10000, 10000], [0, 10000]]}),
    )

    result = asyncio.run(rule_engine.evaluate_legal_rule_by_id("floor_area_ratio"))

    assert result["threshold"] is None
    assert result["items"] == []
    assert [m["key"] for m in result["missing_inputs"]] == ["yoseki_ritsu"]


def test_evaluate_floor_area_ratio_resolves_threshold_from_percent_input(test_db, monkeypatch):
    # yoseki_ritsuは%表記(legal_inputs.pyのdescription通り)で設定し、
    # 比率に変換して比較に使われることを確認する。
    monkeypatch.setenv("YOSEKI_RITSU", "200")

    test_db.insert_element(
        "site1", "Zone", "敷地",
        json.dumps({}),
        json.dumps({"type": "polygon", "points": [[0, 0], [10000, 0], [10000, 10000], [0, 10000]]}),
    )
    test_db.insert_element(
        "room1", "Room", "居室",
        json.dumps({}),
        json.dumps({"type": "polygon", "points": [[0, 0], [10000, 0], [10000, 10000], [0, 10000]]}),
    )

    result = asyncio.run(rule_engine.evaluate_legal_rule_by_id("floor_area_ratio"))

    assert result["missing_inputs"] == []
    assert result["threshold"] == 2.0  # "200"(%) → 2.0(比率)
    item = result["items"][0]
    assert item["measured_value"] == 1.0  # 100m^2 / 100m^2
    assert item["status"] == "pass"  # 1.0 <= 2.0


def test_evaluate_floor_area_ratio_raises_for_unparseable_input(test_db, monkeypatch):
    monkeypatch.setenv("YOSEKI_RITSU", "たくさん")

    test_db.insert_element(
        "site1", "Zone", "敷地",
        json.dumps({}),
        json.dumps({"type": "polygon", "points": [[0, 0], [10000, 0], [10000, 10000], [0, 10000]]}),
    )

    try:
        asyncio.run(rule_engine.evaluate_legal_rule_by_id("floor_area_ratio"))
        assert False, "ValueErrorが発生するはず"
    except ValueError:
        pass


def test_evaluate_site_road_frontage_via_rule_id(test_db, monkeypatch):
    monkeypatch.delenv("LEGAL_API_URL", raising=False)
    legal_client.set_connection_url(None)

    test_db.insert_element(
        "site1", "Zone", "敷地",
        json.dumps({}),
        json.dumps({"type": "polygon", "points": [[0, 0], [10000, 0], [10000, 10000], [0, 10000]]}),
    )
    test_db.insert_element(
        "road1", "Zone", "前面道路",
        json.dumps({}),
        json.dumps({"type": "polygon", "points": [[-1000, -4000], [11000, -4000], [11000, 0], [-1000, 0]]}),
    )

    result = asyncio.run(rule_engine.evaluate_legal_rule_by_id("site_road_frontage"))

    assert result["threshold"] == 2.0
    item = result["items"][0]
    assert item["status"] == "pass"  # 約10.6m >= 2.0m


def test_evaluate_evacuation_walking_distance_via_rule_id(test_db, monkeypatch):
    monkeypatch.delenv("LEGAL_API_URL", raising=False)
    legal_client.set_connection_url(None)

    test_db.insert_element(
        "room1", "Room", "居室A",
        json.dumps({}),
        json.dumps({"type": "polygon", "points": [[0, 0], [4000, 0], [4000, 3000], [0, 3000]]}),
    )
    test_db.insert_element(
        "door1", "Door", "外部ドア",
        json.dumps({}),
        json.dumps({"type": "point", "x": 4150, "y": 1500}),
    )

    result = asyncio.run(rule_engine.evaluate_legal_rule_by_id("evacuation_walking_distance"))

    assert result["threshold"] == 30.0
    item = result["items"][0]
    assert item["target_guid"] == "room1"
    assert item["status"] == "pass"  # 0.15m <= 30.0m


def test_evaluate_ventilation_ratio_via_rule_id(test_db, monkeypatch):
    # daylighting_ratioと同じ計算関数を閾値だけ変えて再利用している
    # ことを確認する(RULE_CHECK_REGISTRY["ventilation_ratio"]参照)。
    monkeypatch.delenv("LEGAL_API_URL", raising=False)
    legal_client.set_connection_url(None)

    test_db.insert_element(
        "room1", "Room", "居室A",
        json.dumps({}),
        json.dumps({"type": "polygon", "points": [[0, 0], [10000, 0], [10000, 5000], [0, 5000]]}),
    )
    # 窓面積3m^2/床面積50m^2 = 0.06 (>= 1/20だがpass、< 1/7でdaylightingならfail)。
    test_db.insert_element(
        "window1", "Window", "窓",
        json.dumps({"archicad_details": {"width": 3, "height": 1}}),
        json.dumps({"type": "point", "x": 0, "y": 2500}),
    )

    result = asyncio.run(rule_engine.evaluate_legal_rule_by_id("ventilation_ratio"))

    assert result["threshold"] == 0.05
    item = result["items"][0]
    assert item["measured_value"] == pytest.approx(0.06)
    assert item["status"] == "pass"  # 0.06 >= 1/20
