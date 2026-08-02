import json
from contextlib import AsyncExitStack, asynccontextmanager

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from .engine.spatial import analyze_space
from .engine.relation_builder import rebuild_connections
from .engine.vector_store import index_elements, search_elements
from .database.db import create_tables
from .database.db import insert_element
from mcp.server.transport_security import TransportSecuritySettings
from .archicad_mcp.server import (
    mcp_server,
    sync_from_archicad,
    list_archicad_properties,
    get_archicad_property_values,
    set_archicad_property_value,
    move_archicad_element,
    delete_archicad_elements,
)
from .archicad_mcp import client as archicad_client

# ==============================
# MCPサーバーをFastAPIへマウント
# ローカルLLM等がツール(list_elements, update_element_properties等)を
# 呼び出すためのエンドポイント。/mcp/ 配下でStreamable HTTPとして待受。
#
# DNSリバインディング防止の既定値はHostヘッダーを127.0.0.1/localhostに
# 限定するため、docker-compose内でサービス名(例: http://backend:8000)
# 経由でアクセスすると弾かれてしまう。このエンドポイントは外部非公開の
# 内部サービス間通信専用という前提で無効化する。
# ==============================

mcp_asgi_app = mcp_server.streamable_http_app(
    streamable_http_path="/",
    transport_security=TransportSecuritySettings(
        enable_dns_rebinding_protection=False
    ),
)


@asynccontextmanager
async def lifespan(app: FastAPI):
    # session_manager はstreamable_http_app()呼び出し後でないと参照できない
    async with AsyncExitStack() as stack:
        await stack.enter_async_context(mcp_server.session_manager.run())
        yield


# FastAPIアプリ作成
app = FastAPI(lifespan=lifespan)
create_tables()

app.mount("/mcp", mcp_asgi_app)

# ==============================
# CORS設定
# React(Vite)からの通信を許可
# ==============================

app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:5173"
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ==============================
# Requestモデル
# Reactから送信されるJSON形式
# ==============================

class AnalyzeRequest(BaseModel):
    model_id: str


class SearchRequest(BaseModel):
    query: str
    n_results: int = 5


class ConnectionRequest(BaseModel):
    # None/未指定 = ARCHICAD_MCP_URL環境変数(リモート/Tailscale経由)へ戻す。
    # "local" = LOCAL_PRESET_URL(127.0.0.1:8765)を使うショートカット。
    # それ以外の文字列 = 任意のURLをそのまま使う。
    url: str | None = None


class SyncFromArchicadRequest(BaseModel):
    limit: int = 50


class PropertyValuesRequest(BaseModel):
    guids: list[str]
    property_guids: list[str]


class SetPropertyValueRequest(BaseModel):
    guid: str
    property_guid: str
    value: str


class MoveElementRequest(BaseModel):
    guid: str
    dx: float
    dy: float
    dz: float = 0
    duplicate: bool = False


class DeleteArchicadElementsRequest(BaseModel):
    guids: list[str]



# ==============================
# Root API
# ==============================

@app.get("/")
def root():
    return {
        "message": "FastAPI OK"
    }



# ==============================
# Health Check API
# ==============================

@app.get("/health")
def health():
    return {
        "status": "ok"
    }



@app.post("/analyze")
def analyze(data: AnalyzeRequest):

    result = analyze_space(
        data.model_id
    )

    return result

@app.post("/bim/import_test")
def import_test():

    # 壁は芯線(line)、部屋は境界(polygon)、扉は代表点(point)として登録する。
    # 居室Aと居室Bはx=4000の辺を共有し、壁・扉もその境界上に配置している。

    insert_element(
        "wall001",
        "Wall",
        "間仕切壁",
        json.dumps({"thickness": 150, "height": 3000}),
        json.dumps({"type": "line", "points": [[4000, 0], [4000, 3000]]}),
    )

    insert_element(
        "door001",
        "Door",
        "居室ドア",
        json.dumps({"width": 900, "height": 2100}),
        json.dumps({"type": "point", "x": 4000, "y": 1500}),
    )

    insert_element(
        "room001",
        "Room",
        "居室A",
        json.dumps({}),
        json.dumps({
            "type": "polygon",
            "points": [[0, 0], [4000, 0], [4000, 3000], [0, 3000]],
        }),
    )

    insert_element(
        "room002",
        "Room",
        "居室B",
        json.dumps({}),
        json.dumps({
            "type": "polygon",
            "points": [[4000, 0], [7000, 0], [7000, 3000], [4000, 3000]],
        }),
    )

    return {

        "status":"BIM data imported"

    }


# ==============================
# Rebuild Relations API
# 要素間の関係(connections)を再計算
# ==============================

@app.post("/bim/rebuild_relations")
def rebuild_relations():

    relations = rebuild_connections()

    return {
        "status": "relations rebuilt",
        "count": len(relations)
    }


# ==============================
# Vector Index API
# 要素+関係の説明文をChromaDBへ埋め込み登録
# ==============================

@app.post("/bim/index")
def index():

    count = index_elements()

    return {
        "status": "indexed",
        "count": count
    }


# ==============================
# Semantic Search API
# 自然文クエリでBIM要素をベクトル検索
# ==============================

@app.post("/bim/search")
def search(data: SearchRequest):

    hits = search_elements(
        data.query,
        n_results=data.n_results
    )

    return {
        "query": data.query,
        "results": hits
    }


# ==============================
# Archicad Bridge Status API
# PC側ブリッジ(Tailscale越しのArchicad MCPサーバー)への到達性を確認する。
# ARCHICAD_MCP_URL未設定でも例外にせず、状態をそのまま返す。
# ==============================

@app.get("/archicad/status")
async def archicad_status():

    return await archicad_client.check_connection()


# ==============================
# Archicad Connection Switch API
# フロントエンドから接続先(ローカル直結 / リモートTailscale経由)を
# バックエンド再起動無しで切り替えるためのエンドポイント。
# ==============================

@app.get("/archicad/connection")
def get_archicad_connection():

    return archicad_client.get_connection_info()


@app.post("/archicad/connection")
async def set_archicad_connection(data: ConnectionRequest):

    url = data.url

    if url == "local":
        url = archicad_client.LOCAL_PRESET_URL

    archicad_client.set_connection_url(url)

    return {
        "connection": archicad_client.get_connection_info(),
        "status": await archicad_client.check_connection(),
    }


# ==============================
# Archicad Action Triggers API
# archicad_mcp/server.pyのMCPツールを、MCPプロトコルを介さず
# backendから直接呼び出すためのREST API。
# (@mcp_server.tool()は元の関数をそのまま返すだけなので、ここでは
#  ローカルLLM等が使うのと同じ関数を直接呼んでいる。)
# ==============================

async def _run_archicad_action(coro):
    try:
        return await coro
    except archicad_client.ArchicadNotConfiguredError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except RuntimeError as exc:
        raise HTTPException(status_code=502, detail=str(exc))


@app.post("/archicad/sync")
async def archicad_sync(data: SyncFromArchicadRequest):

    return await _run_archicad_action(sync_from_archicad(data.limit))


@app.get("/archicad/properties")
async def archicad_properties():

    return await _run_archicad_action(list_archicad_properties())


@app.post("/archicad/properties/values")
async def archicad_property_values(data: PropertyValuesRequest):

    return await _run_archicad_action(
        get_archicad_property_values(data.guids, data.property_guids)
    )


@app.post("/archicad/properties/set")
async def archicad_set_property(data: SetPropertyValueRequest):

    return await _run_archicad_action(
        set_archicad_property_value(data.guid, data.property_guid, data.value)
    )


@app.post("/archicad/elements/move")
async def archicad_move_element(data: MoveElementRequest):

    return await _run_archicad_action(
        move_archicad_element(data.guid, data.dx, data.dy, data.dz, data.duplicate)
    )


@app.post("/archicad/elements/delete")
async def archicad_delete_elements(data: DeleteArchicadElementsRequest):

    return await _run_archicad_action(delete_archicad_elements(data.guids))