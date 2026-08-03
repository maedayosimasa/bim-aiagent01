import functools
import os
import zlib
from collections import defaultdict

import chromadb

from ..database.db import get_elements, get_connections

COLLECTION_NAME = "bim_elements"

RELATION_LABELS_JA = {
    "adjacent": "隣接",
    "connects": "接続",
    "near": "近接",
}


class KeywordEmbeddingFunction:
    """Dependency-free fallback embedding: a hashed bag-of-words vector.

    Used when CHROMA_EMBEDDING_BACKEND=keyword - e.g. when the default
    ONNX semantic model (~80MB, downloaded on first use) can't be
    fetched because of a restricted/offline network. This trades away
    semantic understanding (no synonyms/paraphrase matching) for exact
    keyword overlap, which is enough for our own short, space-tokenized
    element descriptions in _describe_element().

    zlib.crc32 (not the builtin hash()) is used for the hash-bucket so
    the mapping is stable across process restarts - important because
    indexed vectors may be persisted (via the ChromaDB server volume)
    across backend redeploys.
    """

    DIM = 512

    def __call__(self, input):
        return [self._embed(text) for text in input]

    def embed_query(self, input):
        return self(input)

    def _embed(self, text):
        vector = [0.0] * self.DIM

        for token in text.split():
            bucket = zlib.crc32(token.encode("utf-8")) % self.DIM
            vector[bucket] += 1.0

        norm = sum(v * v for v in vector) ** 0.5

        if norm > 0:
            vector = [v / norm for v in vector]

        return vector

    def is_legacy(self):
        return False

    @staticmethod
    def name():
        return "bim-keyword-fallback"

    def default_space(self):
        return "cosine"

    def supported_spaces(self):
        return ["cosine", "l2", "ip"]

    def get_config(self):
        return {}

    @staticmethod
    def build_from_config(config):
        return KeywordEmbeddingFunction()


@functools.lru_cache(maxsize=1)
def get_client():
    host = os.environ.get("CHROMA_HOST")

    if host:
        return chromadb.HttpClient(
            host=host,
            port=int(os.environ.get("CHROMA_PORT", "8000")),
        )

    return chromadb.EphemeralClient()


def _default_embedding_function():
    # "onnx" (default): let chromadb use its own built-in semantic model.
    # "keyword": dependency-free fallback for offline/restricted networks.
    backend = os.environ.get("CHROMA_EMBEDDING_BACKEND", "onnx")

    if backend == "keyword":
        return KeywordEmbeddingFunction()

    if backend != "onnx":
        raise ValueError(f"Unknown CHROMA_EMBEDDING_BACKEND: {backend!r}")

    return None


def get_collection(client=None, embedding_function=None):
    client = client or get_client()

    if embedding_function is None:
        embedding_function = _default_embedding_function()

    # get_or_create_collection() defaults to Chroma's real (downloaded)
    # embedding model when the kwarg is omitted entirely - passing an
    # explicit None would override that default with "no function", so
    # the kwarg is only forwarded when one was actually resolved above.
    kwargs = {}

    if embedding_function is not None:
        kwargs["embedding_function"] = embedding_function

    return client.get_or_create_collection(COLLECTION_NAME, **kwargs)


def _describe_element(element, relations):
    # Space-separated tokens (not a natural sentence) so the text works
    # for both the ONNX model and the whitespace-tokenized keyword
    # fallback - Japanese has no spaces between words, so a plain
    # sentence would collapse into a single unsplit token under the
    # fallback's naive tokenizer.
    parts = [element["name"], element["type"]]

    for conn in relations:
        other_guid = (
            conn["target_guid"]
            if conn["source_guid"] == element["guid"]
            else conn["source_guid"]
        )

        label = RELATION_LABELS_JA.get(conn["relation"], conn["relation"])

        parts.append(other_guid)
        parts.append(label)

    return " ".join(parts)


def _relations_by_guid():
    relations_by_guid = defaultdict(list)

    for conn in get_connections():
        relations_by_guid[conn["source_guid"]].append(conn)
        relations_by_guid[conn["target_guid"]].append(conn)

    return relations_by_guid


def index_elements(client=None, embedding_function=None):
    collection = get_collection(client, embedding_function)

    elements = get_elements()
    relations_by_guid = _relations_by_guid()

    if not elements:
        return 0

    ids = [element["guid"] for element in elements]
    documents = [
        _describe_element(element, relations_by_guid[element["guid"]])
        for element in elements
    ]
    metadatas = [
        {"type": element["type"], "name": element["name"]}
        for element in elements
    ]

    collection.upsert(ids=ids, documents=documents, metadatas=metadatas)

    return len(ids)


def search_elements(query, n_results=5, client=None, embedding_function=None):
    collection = get_collection(client, embedding_function)

    if collection.count() == 0:
        return []

    results = collection.query(
        query_texts=[query],
        n_results=min(n_results, collection.count()),
    )

    hits = []

    for guid, document, metadata, distance in zip(
        results["ids"][0],
        results["documents"][0],
        results["metadatas"][0],
        results["distances"][0],
    ):
        hits.append({
            "guid": guid,
            "document": document,
            "type": metadata.get("type"),
            "name": metadata.get("name"),
            "distance": distance,
        })

    return hits
