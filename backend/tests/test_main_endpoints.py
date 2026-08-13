"""main.pyの薄いRESTラッパー(ロジック自体は別のunitテストで検証済み)を
HTTP経由で叩き、配線(ルーティング・リクエスト/レスポンスの受け渡し)を
確認するスモークテスト。
"""

import json

import pytest


def test_root_endpoint(api_client):
    response = api_client.get("/")

    assert response.status_code == 200
    assert response.json() == {"message": "FastAPI OK"}


def test_health_endpoint(api_client):
    response = api_client.get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_analyze_endpoint(api_client, sample_elements):
    response = api_client.post("/analyze", json={"model_id": "test-model"})

    assert response.status_code == 200
    assert response.json()["model_id"] == "test-model"


def test_import_test_endpoint(api_client, test_db):
    response = api_client.post("/bim/import_test")

    assert response.status_code == 200
    assert response.json() == {"status": "BIM data imported"}
    assert test_db.get_element("wall001") is not None
    assert test_db.get_element("room002") is not None


def test_bim_elements_endpoint(api_client, sample_elements):
    response = api_client.get("/bim/elements")

    assert response.status_code == 200
    guids = {el["guid"] for el in response.json()}
    assert "wall001" in guids


def test_site_boundary_endpoint(api_client, test_db):
    test_db.insert_element(
        "zone_site", "Zone", "敷地",
        json.dumps({}),
        json.dumps({"type": "polygon", "points": [[0, 0], [1000, 0], [1000, 1000], [0, 1000]]}),
    )

    response = api_client.get("/site/boundary")

    assert response.status_code == 200
    assert [z["guid"] for z in response.json()["zones"]] == ["zone_site"]


def test_site_roads_endpoint(api_client, test_db):
    response = api_client.get("/site/roads")

    assert response.status_code == 200
    assert response.json() == {"zones": []}


def test_rebuild_relations_endpoint(api_client, sample_elements):
    response = api_client.post("/bim/rebuild_relations")

    assert response.status_code == 200
    assert response.json() == {"status": "relations rebuilt", "count": 4}


def test_engine_rooms_endpoint(api_client, sample_elements):
    response = api_client.get("/engine/rooms")

    assert response.status_code == 200
    guids = {r["guid"] for r in response.json()["rooms"]}
    assert guids == {"room001", "room002"}


def test_engine_evacuation_endpoint(api_client, sample_elements):
    response = api_client.get("/engine/evacuation")

    assert response.status_code == 200
    assert "exterior_doors" in response.json()
    assert "routes" in response.json()


def test_engine_code_daylighting_endpoint(api_client, sample_elements):
    response = api_client.get("/engine/code/daylighting")

    assert response.status_code == 200
    assert "disclaimer" in response.json()


def test_engine_code_accessible_doors_endpoint(api_client, sample_elements):
    response = api_client.get("/engine/code/accessible_doors")

    assert response.status_code == 200
    assert "disclaimer" in response.json()


def test_engine_rules_daylighting_endpoint(api_client, sample_elements, monkeypatch):
    monkeypatch.delenv("LEGAL_API_URL", raising=False)

    response = api_client.get("/engine/rules/daylighting")

    assert response.status_code == 200
    body = response.json()
    assert body["concept_id"] == "daylighting"
    assert body["legal_sources"] == []
    assert "items" in body


def test_engine_rules_accessible_doors_endpoint(api_client, sample_elements, monkeypatch):
    monkeypatch.delenv("LEGAL_API_URL", raising=False)

    response = api_client.get("/engine/rules/accessible_doors")

    assert response.status_code == 200
    body = response.json()
    assert body["concept_id"] == "barrier_free"
    assert "items" in body


def test_engine_legal_rules_endpoint(api_client):
    response = api_client.get("/engine/legal_rules")

    assert response.status_code == 200
    rule_ids = {r["rule_id"] for r in response.json()}
    assert rule_ids == {
        "daylighting_ratio", "accessible_door_width", "effective_daylighting_ratio",
        "ventilation_ratio", "floor_area_ratio", "site_road_frontage",
        "evacuation_walking_distance", "building_coverage_ratio",
    }


def test_engine_legal_rules_evaluate_endpoint(api_client, sample_elements, monkeypatch):
    monkeypatch.delenv("LEGAL_API_URL", raising=False)

    response = api_client.get("/engine/legal_rules/accessible_door_width/evaluate")

    assert response.status_code == 200
    body = response.json()
    assert body["rule_id"] == "accessible_door_width"
    assert body["concept_id"] == "barrier_free"


def test_engine_legal_rules_evaluate_endpoint_404_for_unknown_rule(api_client):
    response = api_client.get("/engine/legal_rules/does_not_exist/evaluate")

    assert response.status_code == 404


def test_engine_legal_inputs_endpoint(api_client, monkeypatch):
    monkeypatch.setenv("LAND_USE_CATEGORY", "residential")

    response = api_client.get("/engine/legal_inputs")

    assert response.status_code == 200
    body = response.json()
    by_key = {entry["key"]: entry for entry in body}
    assert by_key["land_use_category"]["value"] == "residential"
    assert by_key["land_use_category"]["used_by_rule_ids"] == ["effective_daylighting_ratio"]
    assert by_key["kenpei_ritsu"]["used_by_rule_ids"] == []


def test_engine_legal_rules_evaluate_endpoint_reports_missing_inputs(api_client, sample_elements, monkeypatch):
    monkeypatch.delenv("LAND_USE_CATEGORY", raising=False)

    response = api_client.get("/engine/legal_rules/effective_daylighting_ratio/evaluate")

    assert response.status_code == 200
    body = response.json()
    assert body["items"] == []
    assert [m["key"] for m in body["missing_inputs"]] == ["land_use_category"]


def test_engine_accessibility_endpoint(api_client, sample_elements):
    response = api_client.get("/engine/accessibility")

    assert response.status_code == 200
    body = response.json()
    assert "dead_ends" in body
    assert "hubs" in body


def test_engine_equipment_endpoint(api_client, sample_elements):
    response = api_client.get("/engine/equipment")

    assert response.status_code == 200
    body = response.json()
    assert "rooms" in body
    assert "unplaced_equipment" in body


def test_engine_road_slant_envelope_endpoint(api_client, test_db, monkeypatch):
    monkeypatch.setenv("LAND_USE_CATEGORY", "residential")
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

    response = api_client.get("/engine/road_slant_envelope")

    assert response.status_code == 200
    body = response.json()
    assert body[0]["resolved"] is True
    assert body[0]["gradient"] == 1.25


def test_engine_road_slant_envelope_endpoint_400_for_unknown_land_use(api_client, test_db):
    response = api_client.get("/engine/road_slant_envelope?land_use_category=nope")

    assert response.status_code == 400


def test_engine_road_slant_envelope_propose_endpoint_creates_audit_log(api_client, test_db, monkeypatch):
    monkeypatch.setenv("LAND_USE_CATEGORY", "residential")
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

    response = api_client.post("/engine/road_slant_envelope/propose")

    assert response.status_code == 200
    proposals = response.json()["proposals"]
    assert len(proposals) == 1

    audit_response = api_client.get("/engine/write_audit_log")
    assert audit_response.status_code == 200
    entries = audit_response.json()
    assert len(entries) == 1
    assert entries[0]["status"] == "proposed"


def test_engine_height_restrictions_approve_endpoint_calls_write_flow(api_client, monkeypatch):
    from backend import main as main_module

    async def fake_approve(proposal_id):
        assert proposal_id == 42
        return {"proposal_id": 42, "result_guid": "guid-xyz", "raw_result": {}}

    monkeypatch.setattr(main_module, "approve_envelope_mesh", fake_approve)

    response = api_client.post("/engine/height_restrictions/approve", json={"proposal_id": 42})

    assert response.status_code == 200
    assert response.json()["result_guid"] == "guid-xyz"


def test_engine_height_restrictions_approve_endpoint_400_for_invalid_proposal(api_client, monkeypatch):
    from backend import main as main_module

    async def fake_approve(proposal_id):
        raise ValueError(f"存在しない提案IDです: {proposal_id}")

    monkeypatch.setattr(main_module, "approve_envelope_mesh", fake_approve)

    response = api_client.post("/engine/height_restrictions/approve", json={"proposal_id": 99999})

    assert response.status_code == 400


def test_engine_adjacent_boundary_slant_envelope_endpoint(api_client, test_db, monkeypatch):
    monkeypatch.setenv("LAND_USE_CATEGORY", "residential")
    test_db.insert_element(
        "site1", "Zone", "敷地",
        json.dumps({}),
        json.dumps({"type": "polygon", "points": [[0, 0], [10000, 0], [10000, 10000], [0, 10000]]}),
    )

    response = api_client.get("/engine/adjacent_boundary_slant_envelope")

    assert response.status_code == 200
    body = response.json()
    assert body[0]["resolved"] is True
    assert body[0]["gradient"] == 1.25
    assert body[0]["rise_height_m"] == 20.0


def test_engine_adjacent_boundary_slant_envelope_propose_endpoint(api_client, test_db, monkeypatch):
    monkeypatch.setenv("LAND_USE_CATEGORY", "residential")
    test_db.insert_element(
        "site1", "Zone", "敷地",
        json.dumps({}),
        json.dumps({"type": "polygon", "points": [[0, 0], [10000, 0], [10000, 10000], [0, 10000]]}),
    )

    response = api_client.post("/engine/adjacent_boundary_slant_envelope/propose")

    assert response.status_code == 200
    assert len(response.json()["proposals"]) == 1


def test_engine_north_slant_envelope_endpoint_with_explicit_north_degrees(api_client, test_db, monkeypatch):
    monkeypatch.setenv("KITAGAWA_SHASEN_KUBUN", "low_rise")
    test_db.insert_element(
        "site1", "Zone", "敷地",
        json.dumps({}),
        json.dumps({"type": "polygon", "points": [[0, 0], [10000, 0], [10000, 10000], [0, 10000]]}),
    )

    response = api_client.get("/engine/north_slant_envelope?north_degrees=0")

    assert response.status_code == 200
    body = response.json()
    assert body[0]["resolved"] is True
    assert body[0]["rise_height_m"] == 5.0


def test_engine_north_slant_envelope_propose_endpoint_with_explicit_north_degrees(api_client, test_db, monkeypatch):
    monkeypatch.setenv("KITAGAWA_SHASEN_KUBUN", "mid_rise")
    test_db.insert_element(
        "site1", "Zone", "敷地",
        json.dumps({}),
        json.dumps({"type": "polygon", "points": [[0, 0], [10000, 0], [10000, 10000], [0, 10000]]}),
    )

    response = api_client.post("/engine/north_slant_envelope/propose?north_degrees=0")

    assert response.status_code == 200
    assert len(response.json()["proposals"]) == 1


def test_engine_height_district_envelope_endpoint_flat_kubun(api_client, test_db):
    # kubun="flat"はnorth_degreesを必要としないため、Archicad接続無しでも
    # 動作する(モックせずそのまま呼べることを確認する)。
    test_db.insert_element(
        "site1", "Zone", "敷地",
        json.dumps({}),
        json.dumps({"type": "polygon", "points": [[0, 0], [10000, 0], [10000, 10000], [0, 10000]]}),
    )

    response = api_client.get(
        "/engine/height_district_envelope?kubun=flat&max_height_m=10&kanwa_m=2"
    )

    assert response.status_code == 200
    body = response.json()
    assert body[0]["resolved"] is True
    assert body[0]["vertices"][0]["z_m"] == pytest.approx(12.0)


def test_engine_height_district_envelope_propose_endpoint_flat_kubun(api_client, test_db):
    test_db.insert_element(
        "site1", "Zone", "敷地",
        json.dumps({}),
        json.dumps({"type": "polygon", "points": [[0, 0], [10000, 0], [10000, 10000], [0, 10000]]}),
    )

    response = api_client.post(
        "/engine/height_district_envelope/propose?kubun=flat&max_height_m=10"
    )

    assert response.status_code == 200
    assert len(response.json()["proposals"]) == 1


def test_engine_height_district_envelope_endpoint_north_slant_kubun_with_explicit_north(
    api_client, test_db
):
    test_db.insert_element(
        "site1", "Zone", "敷地",
        json.dumps({}),
        json.dumps({"type": "polygon", "points": [[0, 0], [10000, 0], [10000, 10000], [0, 10000]]}),
    )

    response = api_client.get(
        "/engine/height_district_envelope"
        "?kubun=north_slant&rise_m=3&gradient=0.6&north_degrees=0"
    )

    assert response.status_code == 200
    assert response.json()[0]["resolved"] is True


def test_engine_analysis_snapshot_endpoint(api_client, test_db):
    response = api_client.get("/engine/analysis_snapshot")

    assert response.status_code == 200
    assert response.json() is None


def test_graph_relation_snapshot_endpoint(api_client, sample_elements):
    api_client.post("/bim/rebuild_relations")

    response = api_client.get("/graph/relation_snapshot")

    assert response.status_code == 200
    assert len(response.json()) == 4


def test_bim_index_endpoint(api_client, test_db, monkeypatch):
    # ChromaDB/埋め込みモデルは別途test_vector_store.pyで検証済み。
    # ここではREST配線のみ確認する。
    from backend import main as main_module

    monkeypatch.setattr(main_module, "index_elements", lambda: 3)

    response = api_client.post("/bim/index")

    assert response.status_code == 200
    assert response.json() == {"status": "indexed", "count": 3}


def test_bim_search_endpoint(api_client, test_db, monkeypatch):
    from backend import main as main_module

    monkeypatch.setattr(
        main_module,
        "search_elements",
        lambda query, n_results: [{"guid": "wall001"}],
    )

    response = api_client.post("/bim/search", json={"query": "壁", "n_results": 2})

    assert response.status_code == 200
    assert response.json() == {
        "query": "壁",
        "results": [{"guid": "wall001"}],
    }


def test_archicad_status_endpoint(api_client, monkeypatch):
    monkeypatch.delenv("ARCHICAD_MCP_URL", raising=False)

    response = api_client.get("/archicad/status")

    assert response.status_code == 200
    assert response.json()["configured"] is False


def test_agent_usage_daily_endpoint_empty(api_client, test_db):
    response = api_client.get("/agent/usage/daily")

    assert response.status_code == 200
    assert response.json() == {"days": []}


def test_agent_usage_jobs_endpoint(api_client, test_db):
    from backend.database import db as db_module

    db_module.insert_token_usage("chat", "session-1", "claude-opus-5", 100, 50, 0.00175)
    db_module.insert_token_usage("legal_report", None, "claude-opus-5", 200, 100, 0.0035)

    daily = api_client.get("/agent/usage/daily")
    assert daily.status_code == 200
    days = daily.json()["days"]
    assert len(days) == 1
    assert days[0]["call_count"] == 2
    assert days[0]["input_tokens"] == 300
    assert days[0]["output_tokens"] == 150

    jobs = api_client.get("/agent/usage/jobs")
    assert jobs.status_code == 200
    jobs_data = jobs.json()["jobs"]
    assert {j["kind"] for j in jobs_data} == {"chat", "legal_report"}
    chat_job = next(j for j in jobs_data if j["kind"] == "chat")
    assert chat_job["job_id"] == "session-1"
    assert chat_job["input_tokens"] == 100
