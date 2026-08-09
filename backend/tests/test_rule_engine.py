import asyncio
import json

from backend.engine import rule_engine
from backend.legal_mcp import client as legal_client


def test_status_for_uses_comparator():
    verification = rule_engine.Verification(comparator="gte", threshold=0.8, unit="m")

    assert rule_engine._status_for(0.9, verification) is rule_engine.RuleCheckStatus.PASS
    assert rule_engine._status_for(0.7, verification) is rule_engine.RuleCheckStatus.FAIL
    assert rule_engine._status_for(None, verification) is rule_engine.RuleCheckStatus.UNKNOWN


def test_load_legal_rules_returns_known_rules():
    rules = {r.rule_id: r for r in rule_engine.load_legal_rules()}

    assert set(rules) == {"daylighting_ratio", "accessible_door_width"}
    assert rules["daylighting_ratio"].check == "daylighting_ratio"
    assert rules["daylighting_ratio"].concept_id == "daylighting"
    assert rules["accessible_door_width"].verification.threshold == 0.8


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
                "node_id": "n1",
                "raw_sentence": "  居室には採光のための窓その他の開口部を設けなければならない。  ",
                "modality": "obligation",
                "conditions": [],
                "concept_ids": ["daylighting"],
                "confidence": 0.5,
            }
        ]

    monkeypatch.setattr(legal_client, "get_rules_by_concept", fake_get_rules_by_concept)

    sources = asyncio.run(rule_engine._legal_sources_for_concept("daylighting"))

    assert sources == [
        {
            "rule_id": "rule:1",
            "law_id": "325AC0000000201",
            "node_id": "n1",
            "raw_sentence": "居室には採光のための窓その他の開口部を設けなければならない。",
            "modality": "obligation",
            "confidence": 0.5,
        }
    ]


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


def test_evaluate_legal_rule_by_id_raises_for_unknown_rule():
    try:
        asyncio.run(rule_engine.evaluate_legal_rule_by_id("does_not_exist"))
        assert False, "ValueErrorが発生するはず"
    except ValueError:
        pass


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
