from backend.legal_mcp import client as legal_client


def test_legal_search_returns_400_when_not_configured(api_client, monkeypatch):
    monkeypatch.delenv("LEGAL_API_URL", raising=False)

    response = api_client.get("/legal/search", params={"q": "採光"})

    assert response.status_code == 400


def test_legal_laws_returns_400_when_not_configured(api_client, monkeypatch):
    monkeypatch.delenv("LEGAL_API_URL", raising=False)

    response = api_client.get("/legal/laws")

    assert response.status_code == 400


def test_legal_search_proxies_result_when_configured(api_client, monkeypatch):
    monkeypatch.setenv("LEGAL_API_URL", "http://legal.example.invalid")

    async def fake_search(query, top_k=5, law_id=None):
        assert query == "採光"
        assert top_k == 3
        return {"query": query, "results": [{"node_id": "n1", "law_id": "L1", "law_title": "テスト法", "citation": "テスト法 第一条", "text": "本文", "distance": 0.1}]}

    monkeypatch.setattr(legal_client, "search", fake_search)

    response = api_client.get("/legal/search", params={"q": "採光", "top_k": 3})

    assert response.status_code == 200
    body = response.json()
    assert body["results"][0]["node_id"] == "n1"


def test_legal_article_proxies_result_when_configured(api_client, monkeypatch):
    monkeypatch.setenv("LEGAL_API_URL", "http://legal.example.invalid")

    async def fake_get_article(law_id, num):
        assert law_id == "L1"
        assert num == "43"
        return {"law_id": law_id, "law_title": "テスト法", "article": {"node_id": "n", "node_type": "Article", "num": num, "title": None, "caption": None, "text": None, "children": []}}

    monkeypatch.setattr(legal_client, "get_article", fake_get_article)

    response = api_client.get("/legal/article", params={"law_id": "L1", "num": "43"})

    assert response.status_code == 200
    assert response.json()["article"]["num"] == "43"


def test_legal_rules_by_concept_proxies_result_when_configured(api_client, monkeypatch):
    monkeypatch.setenv("LEGAL_API_URL", "http://legal.example.invalid")

    async def fake_get_rules_by_concept(concept_id):
        assert concept_id == "daylighting"
        return [{"rule_id": "rule:1", "law_id": "L1", "node_id": "n1", "raw_sentence": "s", "modality": "obligation", "conditions": [], "concept_ids": ["daylighting"], "confidence": 0.5}]

    monkeypatch.setattr(legal_client, "get_rules_by_concept", fake_get_rules_by_concept)

    response = api_client.get("/legal/rules/by_concept", params={"concept_id": "daylighting"})

    assert response.status_code == 200
    assert response.json()[0]["rule_id"] == "rule:1"


def test_legal_rules_by_concept_returns_400_when_not_configured(api_client, monkeypatch):
    monkeypatch.delenv("LEGAL_API_URL", raising=False)

    response = api_client.get("/legal/rules/by_concept", params={"concept_id": "daylighting"})

    assert response.status_code == 400


def test_legal_concepts_proxies_result_when_configured(api_client, monkeypatch):
    monkeypatch.setenv("LEGAL_API_URL", "http://legal.example.invalid")

    async def fake_get_concepts(has_bim_mapping=False):
        assert has_bim_mapping is True
        return [{"concept_id": "opening", "label": "開口部", "aliases": [], "category": None, "bim_mapping": {"bim_entity_types": ["Door", "Window"], "required_properties": [], "relation_dependent": False, "geometry_required": False, "notes": None}}]

    monkeypatch.setattr(legal_client, "get_concepts", fake_get_concepts)

    response = api_client.get("/legal/concepts", params={"has_bim_mapping": True})

    assert response.status_code == 200
    assert response.json()[0]["concept_id"] == "opening"


def test_legal_search_returns_502_when_backend_unreachable(api_client, monkeypatch):
    import httpx

    monkeypatch.setenv("LEGAL_API_URL", "http://legal.example.invalid")

    async def failing_search(query, top_k=5, law_id=None):
        raise httpx.ConnectError("connection refused")

    monkeypatch.setattr(legal_client, "search", failing_search)

    response = api_client.get("/legal/search", params={"q": "採光"})

    assert response.status_code == 502


def test_check_connection_reports_not_configured():
    import asyncio
    import os

    legal_client.set_connection_url(None)
    prev = os.environ.pop("LEGAL_API_URL", None)
    try:
        status = asyncio.run(legal_client.check_connection())
        assert status == {"configured": False, "reachable": False, "error": "LEGAL_API_URLが設定されていません"}
    finally:
        if prev is not None:
            os.environ["LEGAL_API_URL"] = prev
