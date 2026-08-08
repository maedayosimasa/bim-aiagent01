"""main.pyの薄いRESTラッパー(ロジック自体は別のunitテストで検証済み)を
HTTP経由で叩き、配線(ルーティング・リクエスト/レスポンスの受け渡し)を
確認するスモークテスト。
"""

import json


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


def test_engine_accessibility_endpoint(api_client, sample_elements):
    response = api_client.get("/engine/accessibility")

    assert response.status_code == 200
    body = response.json()
    assert "dead_ends" in body
    assert "hubs" in body


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
