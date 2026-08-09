"""HTTPクライアント: Legal Knowledge Builder(別リポジトリ、`~/Legal Knowledge Builder/`)
が公開する法令検索API(素のREST/JSON、MCPプロトコルではない)を呼ぶ。

`archicad_mcp/client.py`と同じ「別プロセスのサービスをURLで参照し、未設定/未接続でも
クラッシュせずステータスを返す」構成を踏襲している(接続先切り替えAPI、
`check_connection()`の形も同様)。Legal Knowledge Builder側は`knowledge/`
(ビルド済みのKnowledge Package)を読むだけの読み取り専用サービスで、法令の
改訂時のみ再ビルド・再起動される想定(bim-aiagent01の開発サイクルとは独立)。
"""

import os

import httpx


class LegalApiNotConfiguredError(RuntimeError):
    """LEGAL_API_URLが未設定(環境変数・実行時オーバーライドのいずれも無い)場合に送出する。"""


# Legal Knowledge Builder側を `uv run legal-knowledge-builder serve` で
# ローカル起動した場合の既定ポート。
LOCAL_PRESET_URL = "http://127.0.0.1:8100"

# POST /legal/connection でフロントエンドから切り替えられる実行時オーバーライド。
# Noneの場合はLEGAL_API_URL環境変数にフォールバックする。
_override_url = None


def get_active_url():
    return _override_url or os.environ.get("LEGAL_API_URL") or None


def set_connection_url(url):
    global _override_url
    _override_url = url or None
    return get_active_url()


def get_connection_info():
    return {
        "active_url": get_active_url(),
        "overridden": _override_url is not None,
        "env_default": os.environ.get("LEGAL_API_URL") or None,
        "local_preset_url": LOCAL_PRESET_URL,
    }


def is_configured():
    return bool(get_active_url())


def _base_url():
    url = get_active_url()
    if not url:
        raise LegalApiNotConfiguredError(
            "LEGAL_API_URLが設定されていません。POST /legal/connection か環境変数で設定してください。"
        )
    return url.rstrip("/")


async def list_laws():
    async with httpx.AsyncClient(timeout=10.0) as client:
        response = await client.get(f"{_base_url()}/laws")
        response.raise_for_status()
        return response.json()


async def search(query, top_k=5, law_id=None):
    params = {"q": query, "top_k": top_k}
    if law_id:
        params["law_id"] = law_id
    async with httpx.AsyncClient(timeout=30.0) as client:
        response = await client.get(f"{_base_url()}/search", params=params)
        response.raise_for_status()
        return response.json()


async def get_article(law_id, num):
    async with httpx.AsyncClient(timeout=10.0) as client:
        response = await client.get(f"{_base_url()}/article", params={"law_id": law_id, "num": num})
        response.raise_for_status()
        return response.json()


async def get_rules(law_id, node_id=None, concept_id=None):
    params = {"law_id": law_id}
    if node_id:
        params["node_id"] = node_id
    if concept_id:
        params["concept_id"] = concept_id
    async with httpx.AsyncClient(timeout=10.0) as client:
        response = await client.get(f"{_base_url()}/rules", params=params)
        response.raise_for_status()
        return response.json()


async def get_reference(law_id, node_id):
    async with httpx.AsyncClient(timeout=10.0) as client:
        response = await client.get(f"{_base_url()}/reference", params={"law_id": law_id, "node_id": node_id})
        response.raise_for_status()
        return response.json()


def _describe_exception(exc):
    return f"{type(exc).__name__}: {exc}" if str(exc) else type(exc).__name__


async def check_connection():
    """archicad_mcp.client.check_connection()と同じ形の疎通確認。curl直叩き可能。

    設定漏れ・未起動・ネットワーク不通のいずれでも例外を投げず、状態を表す
    JSON化可能なdictを返す。
    """

    if not is_configured():
        return {"configured": False, "reachable": False, "error": "LEGAL_API_URLが設定されていません"}

    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            response = await client.get(f"{_base_url()}/health")
            response.raise_for_status()
            detail = response.json()
        return {"configured": True, "reachable": True, "detail": detail}
    except Exception as exc:
        return {"configured": True, "reachable": False, "error": _describe_exception(exc)}
