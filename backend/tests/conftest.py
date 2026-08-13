import json

import pytest
from fastapi.testclient import TestClient

from backend.database import db as db_module


@pytest.fixture(scope="session")
def api_client():
    # main.py's mcp_server.session_manager.run() (entered via its FastAPI
    # lifespan) may only be called once per process, so every test module
    # that needs to hit main.app over HTTP must share this one TestClient
    # instead of each opening its own "with TestClient(app) as client:".
    from backend.main import app

    with TestClient(app) as client:
        yield client


@pytest.fixture
def test_db(tmp_path, monkeypatch):
    db_path = tmp_path / "test_bim_cache.db"
    monkeypatch.setattr(db_module, "DB_PATH", db_path)
    db_module.create_tables()
    return db_module


@pytest.fixture
def sample_elements(test_db):
    # room001とroom002はx=4000の辺を共有しており、wall001(芯線)と
    # door001(代表点)もその境界上に置かれている。点ベースの近似では
    # なく、実際のポリゴン/ラインの境界共有として隣接が判定されることを
    # このデータで確認する。
    test_db.insert_element(
        "wall001", "Wall", "間仕切壁",
        json.dumps({"thickness": 150, "height": 3000}),
        json.dumps({"type": "line", "points": [[4000, 0], [4000, 3000]]}),
    )
    test_db.insert_element(
        "door001", "Door", "居室ドア",
        json.dumps({"width": 900, "height": 2100}),
        json.dumps({"type": "point", "x": 4000, "y": 1500}),
    )
    test_db.insert_element(
        "room001", "Room", "居室A",
        json.dumps({}),
        json.dumps({
            "type": "polygon",
            "points": [[0, 0], [4000, 0], [4000, 3000], [0, 3000]],
        }),
    )
    test_db.insert_element(
        "room002", "Room", "居室B",
        json.dumps({}),
        json.dumps({
            "type": "polygon",
            "points": [[4000, 0], [7000, 0], [7000, 3000], [4000, 3000]],
        }),
    )
    return test_db


def _delete_bim_collection(client):
    from backend.engine import vector_store

    try:
        client.delete_collection(vector_store.COLLECTION_NAME)
    except Exception:
        pass


@pytest.fixture
def chroma_client():
    from backend.engine import vector_store

    # get_client() is process-wide cached (lru_cache) so that a real
    # HttpClient/EphemeralClient is reused across requests within one
    # app run. chromadb also shares its in-memory backend across every
    # EphemeralClient() created with the same settings in one process,
    # so the "bim_elements" collection must be dropped around each test
    # or data leaks between them.
    vector_store.get_client.cache_clear()

    client = vector_store.get_client()
    _delete_bim_collection(client)

    yield client

    _delete_bim_collection(client)
    vector_store.get_client.cache_clear()


class _FakeEmbeddingFunction:
    """Deterministic bag-of-character-trigrams embedding.

    Not semantically meaningful, but textually similar strings land
    closer together than unrelated ones - enough to exercise chromadb's
    real upsert/query pipeline in tests without downloading the ~80MB
    default ONNX embedding model.
    """

    DIM = 256

    def is_legacy(self):
        return False

    def default_space(self):
        return "cosine"

    def supported_spaces(self):
        return ["cosine", "l2", "ip"]

    def get_config(self):
        return {}

    @staticmethod
    def build_from_config(config):
        return _FakeEmbeddingFunction()

    def __call__(self, input):
        return [self._embed(text) for text in input]

    def embed_query(self, input):
        return self(input)

    def _embed(self, text):
        vector = [0.0] * self.DIM

        for i in range(len(text) - 2):
            trigram = text[i:i + 3]
            vector[hash(trigram) % self.DIM] += 1.0

        # L2-normalize so a short, partially-matching document can't beat a
        # longer, more-matching one purely by having a smaller raw distance.
        norm = sum(v * v for v in vector) ** 0.5

        if norm > 0:
            vector = [v / norm for v in vector]

        return vector

    @staticmethod
    def name():
        return "fake-trigram"


@pytest.fixture
def fake_embedding_function():
    return _FakeEmbeddingFunction()


@pytest.fixture(autouse=True)
def _reset_archicad_connection_override():
    # archicad_mcp.client keeps a module-level runtime override (set via
    # POST /archicad/connection) so it survives across requests within one
    # backend process. Tests must not leak that global across each other.
    from backend.archicad_mcp import client as archicad_client

    archicad_client.set_connection_url(None)
    yield
    archicad_client.set_connection_url(None)


@pytest.fixture(autouse=True)
def _reset_legal_connection_override():
    # legal_mcp.client も同様にモジュールレベルのランタイムオーバーライドを
    # 持つ(POST /legal/connection経由)。テスト間でリークしないようにする。
    from backend.legal_mcp import client as legal_client

    legal_client.set_connection_url(None)
    yield
    legal_client.set_connection_url(None)


@pytest.fixture(autouse=True)
def _reset_legal_local_process():
    # legal_mcp.local_process も起動したサブプロセスのハンドルを
    # モジュールレベルで保持する。テスト間でリークしないようにする。
    from backend.legal_mcp import local_process as legal_local_process

    legal_local_process._process = None
    yield
    legal_local_process._process = None
