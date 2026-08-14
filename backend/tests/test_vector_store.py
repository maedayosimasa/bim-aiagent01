import json

import pytest

from backend.engine import vector_store
from backend.engine.relation_builder import rebuild_connections
from backend.engine.vector_store import (
    KeywordEmbeddingFunction,
    index_elements,
    remove_from_index,
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


def test_index_elements_with_guids_only_indexes_matching_elements(
    sample_elements, chroma_client, fake_embedding_function
):
    # (2026-08-14追加)archicad_mcp/server.pyのsync_from_archicad()が
    # 差分検知で判明した追加/変更guidだけをインクリメンタルに再インデックス
    # するために使う(実データ5706件のフル再インデックスは約46秒かかり、
    # 変更の無い大多数の要素まで毎回埋め込み直すのは非現実的なため)。
    rebuild_connections()

    count = index_elements(
        client=chroma_client, embedding_function=fake_embedding_function,
        guids=["room001"],
    )

    assert count == 1

    collection = chroma_client.get_or_create_collection(
        "bim_elements", embedding_function=fake_embedding_function
    )
    assert collection.count() == 1
    assert collection.get(ids=["room001"])["ids"] == ["room001"]


def test_index_elements_with_empty_guids_indexes_nothing(
    sample_elements, chroma_client, fake_embedding_function
):
    count = index_elements(
        client=chroma_client, embedding_function=fake_embedding_function, guids=[],
    )

    assert count == 0


def test_remove_from_index_deletes_specified_ids(
    sample_elements, chroma_client, fake_embedding_function
):
    # (2026-08-14追加)sync_from_archicad()の全削除→差し替え方式では、
    # Archicad側で削除された要素がelementsテーブルからは消えても、
    # index_elements()はupsert(追加/更新のみ)しか行わないため、以前は
    # 削除された要素の埋め込みが検索インデックスに永久に残り続ける
    # "ghost"エントリになっていた。remove_from_index()で明示的に削除
    # できることを確認する。
    rebuild_connections()
    index_elements(client=chroma_client, embedding_function=fake_embedding_function)

    collection = chroma_client.get_or_create_collection(
        "bim_elements", embedding_function=fake_embedding_function
    )
    assert collection.count() == 4

    remove_from_index(
        ["room001"], client=chroma_client, embedding_function=fake_embedding_function
    )

    assert collection.count() == 3
    assert collection.get(ids=["room001"])["ids"] == []


def test_remove_from_index_with_empty_guids_is_noop(monkeypatch):
    # ChromaDBへの無駄な呼び出しを避けるため、空リストなら早期リターンする
    # (get_collection()すら呼ばれない)ことを確認する。
    called = []
    monkeypatch.setattr(vector_store, "get_collection", lambda *a, **kw: called.append(1))

    remove_from_index([])

    assert called == []


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
