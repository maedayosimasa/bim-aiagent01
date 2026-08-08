import json

import pytest

from backend.engine import vector_store
from backend.engine.relation_builder import rebuild_connections
from backend.engine.vector_store import (
    KeywordEmbeddingFunction,
    index_elements,
    search_elements,
    _describe_element,
)


def test_describe_element_includes_relations():
    element = {"guid": "room001", "type": "Room", "name": "居室A"}
    relations = [
        {"source_guid": "room001", "target_guid": "room002", "relation": "adjacent"},
        {"source_guid": "door001", "target_guid": "room001", "relation": "connects"},
    ]

    text = _describe_element(element, relations)

    assert text == "居室A Room room002 隣接 door001 接続"


def test_index_elements_upserts_one_document_per_element(
    sample_elements, chroma_client, fake_embedding_function
):
    rebuild_connections()

    count = index_elements(client=chroma_client, embedding_function=fake_embedding_function)

    assert count == 4

    collection = chroma_client.get_or_create_collection(
        "bim_elements", embedding_function=fake_embedding_function
    )
    assert collection.count() == 4

    stored = collection.get(ids=["room001"])
    assert "居室A" in stored["documents"][0]
    assert "隣接" in stored["documents"][0]


def test_index_elements_with_no_data_returns_zero(test_db, chroma_client, fake_embedding_function):
    count = index_elements(client=chroma_client, embedding_function=fake_embedding_function)

    assert count == 0


def test_index_elements_splits_into_batches_when_exceeding_batch_size(
    test_db, chroma_client, fake_embedding_function, monkeypatch
):
    # 実データ検証で発覚した不具合の再現: ChromaDBは1回のupsert()で送れる
    # 件数に上限があり(実データ5708要素で上限5461件を超えInternalError
    # になっていた)、超える場合は分割して送らなければならない。
    monkeypatch.setattr(vector_store, "_UPSERT_BATCH_SIZE", 2)

    for i in range(5):
        test_db.insert_element(
            f"wall{i}", "Wall", f"壁{i}",
            json.dumps({}),
            json.dumps({"type": "line", "points": [[0, 0], [1000, 0]]}),
        )

    count = index_elements(client=chroma_client, embedding_function=fake_embedding_function)

    assert count == 5

    collection = chroma_client.get_or_create_collection(
        vector_store.COLLECTION_NAME, embedding_function=fake_embedding_function
    )
    assert collection.count() == 5


def test_search_elements_finds_relevant_element_by_text(
    sample_elements, chroma_client, fake_embedding_function
):
    rebuild_connections()
    index_elements(client=chroma_client, embedding_function=fake_embedding_function)

    # "居室ドア" is door001's distinctive name and doesn't appear in any
    # other element's description, so the match is unambiguous regardless
    # of the fake embedding's hash-bucket seed.
    hits = search_elements(
        "居室ドア",
        n_results=1,
        client=chroma_client,
        embedding_function=fake_embedding_function,
    )

    assert len(hits) == 1
    assert hits[0]["guid"] == "door001"
    assert hits[0]["type"] == "Door"


def test_search_elements_on_empty_collection_returns_no_hits(
    test_db, chroma_client, fake_embedding_function
):
    hits = search_elements(
        "何もない", client=chroma_client, embedding_function=fake_embedding_function
    )

    assert hits == []


def test_default_embedding_function_defaults_to_onnx(monkeypatch):
    # None means "let chromadb use its own built-in ONNX model" - see
    # get_collection()'s handling of the None sentinel.
    monkeypatch.delenv("CHROMA_EMBEDDING_BACKEND", raising=False)

    assert vector_store._default_embedding_function() is None


def test_default_embedding_function_keyword_backend(monkeypatch):
    monkeypatch.setenv("CHROMA_EMBEDDING_BACKEND", "keyword")

    ef = vector_store._default_embedding_function()

    assert isinstance(ef, KeywordEmbeddingFunction)


def test_default_embedding_function_unknown_backend_raises(monkeypatch):
    monkeypatch.setenv("CHROMA_EMBEDDING_BACKEND", "bogus")

    with pytest.raises(ValueError):
        vector_store._default_embedding_function()


def test_get_collection_resolves_default_embedding_function_when_omitted(
    chroma_client, monkeypatch
):
    # get_collection()にembedding_functionを渡さない(=呼び出し側の既定)
    # 経路。ONNXモデルのダウンロードを避けるため、KeywordEmbeddingFunction
    # を使うバックエンドに切り替えた上で確認する。
    monkeypatch.setenv("CHROMA_EMBEDDING_BACKEND", "keyword")

    collection = vector_store.get_collection(client=chroma_client)

    assert collection.name == vector_store.COLLECTION_NAME


def test_get_client_uses_http_client_when_chroma_host_set(monkeypatch):
    # CHROMA_HOST未設定時(デフォルト)はEphemeralClientを使う経路が
    # chroma_clientフィクスチャ経由で常に踏まれるが、CHROMA_HOST設定時の
    # HttpClient経路は別途確認する必要がある。実ネットワーク接続は
    # 発生させず、コンストラクタ引数だけを検証する。
    calls = {}

    class _FakeHttpClient:
        def __init__(self, host, port):
            calls["host"] = host
            calls["port"] = port

    monkeypatch.setattr(vector_store.chromadb, "HttpClient", _FakeHttpClient)
    monkeypatch.setenv("CHROMA_HOST", "chroma.example.com")
    monkeypatch.setenv("CHROMA_PORT", "9000")

    vector_store.get_client.cache_clear()
    try:
        client = vector_store.get_client()
    finally:
        vector_store.get_client.cache_clear()

    assert isinstance(client, _FakeHttpClient)
    assert calls == {"host": "chroma.example.com", "port": 9000}


def test_keyword_embedding_function_is_deterministic_across_instances():
    # Uses zlib.crc32 (not the randomized builtin hash()) specifically so
    # this holds across separate processes/restarts, not just within one.
    ef1 = KeywordEmbeddingFunction()
    ef2 = KeywordEmbeddingFunction()

    assert ef1(["居室A Room room002 隣接"]) == ef2(["居室A Room room002 隣接"])


def test_keyword_embedding_function_end_to_end_search(sample_elements, chroma_client):
    # Exercises the real fallback (no fake embedding fixture) since it
    # has no external dependency or download to worry about.
    rebuild_connections()
    keyword_ef = KeywordEmbeddingFunction()

    count = index_elements(client=chroma_client, embedding_function=keyword_ef)
    assert count == 4

    hits = search_elements(
        "居室ドア",
        n_results=1,
        client=chroma_client,
        embedding_function=keyword_ef,
    )

    assert len(hits) == 1
    assert hits[0]["guid"] == "door001"
