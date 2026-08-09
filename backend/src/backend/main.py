import json
import os
from contextlib import AsyncExitStack, asynccontextmanager
from pathlib import Path

import httpx
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from .engine.spatial import analyze_space
from .engine.relation_builder import rebuild_connections
from .engine.vector_store import index_elements, search_elements
from .engine.site import get_site_boundary, get_road_boundaries
from .engine.room_engine import analyze_room_adjacency
from .engine.evacuation_engine import find_evacuation_routes
from .engine.code_engine import check_daylighting, check_accessible_door_width
from .engine.accessibility import analyze_accessibility
from .engine.equipment import find_room_equipment
from .database.db import create_tables
from .database.db import insert_element
from mcp.server.transport_security import TransportSecuritySettings
from .archicad_mcp.server import (
    mcp_server,
    list_elements,
    sync_from_archicad,
    get_archicad_geo_location,
    list_archicad_properties,
    get_archicad_property_values,
    set_archicad_property_value,
    move_archicad_element,
    delete_archicad_elements,
    focus_archicad_elements,
    get_engine_analysis_snapshot,
    get_graph_relation_snapshot,
)
from .archicad_mcp import client as archicad_client
from .legal_mcp import client as legal_client


def _load_dotenv_if_present() -> None:
    # docker-compose経由では docker-compose.yml が .env を読んで環境変数として
    # 渡してくれるが、`uv run uvicorn ...` で直接起動する場合は誰も読んでくれない
    # (ARCHICAD_MCP_URL/LEGAL_API_URL が「exportし忘れて未設定のまま」になる
    # 事故が続いたため追加)。既にexport済みの値は上書きしない(setdefault)。
    env_path = Path(__file__).resolve().parents[3] / ".env"
    if not env_path.exists():
        return
    for line in env_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        os.environ.setdefault(key.strip(), value.strip())


_load_dotenv_if_present()

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


class FocusArchicadElementsRequest(BaseModel):
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
# Elements List API
# キャッシュされている全BIM要素を返す(プロパティ/ジオメトリはパース済み)
# ==============================

@app.get("/bim/elements")
def list_bim_elements():

    return list_elements()


# ==============================
# Site Boundary / Road API
# 敷地境界線・道路は専用の要素タイプが無く、Zone(部屋と同じ要素タイプ)に
# 用途を表す名前("敷地"/"道路")を付けて登録する運用を前提に、SQLite
# キャッシュ済みのZoneから名前で検索する(engine/site.py参照)。
# 道路幅員・道路中心線はZoneポリゴンの最小外接矩形から幾何的に推定した
# 近似値であり、実測値・設計値そのものではない。
# ==============================

@app.get("/site/boundary")
def site_boundary():

    return {"zones": get_site_boundary()}


@app.get("/site/roads")
def site_roads():

    return {"zones": get_road_boundaries()}


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
# Spatial Intelligence Modules API
# CLAUDE.md「⑥ 空間知能エンジン」の推奨実装順序に沿った追加モジュール。
# いずれも既存のcalculate_relations()の結果(SQLiteキャッシュ済み要素から
# 都度再計算)のみで完結し、新規のArchicad連携は不要。
# ==============================

@app.get("/engine/rooms")
def engine_room_adjacency():

    return {"rooms": analyze_room_adjacency()}


@app.get("/engine/evacuation")
def engine_evacuation_routes():
    # 単一階のみ対応(階段の接続情報が実データに無いため複数階は未対応、
    # engine/evacuation_engine.py参照)。

    return find_evacuation_routes()


@app.get("/engine/code/daylighting")
def engine_code_daylighting():
    # 参考値であり法的な適合を保証するものではない(engine/code_engine.py参照)。

    return check_daylighting()


@app.get("/engine/code/accessible_doors")
def engine_code_accessible_doors():
    # 参考値であり法的な適合を保証するものではない(engine/code_engine.py参照)。

    return check_accessible_door_width()


@app.get("/engine/accessibility")
def engine_accessibility():

    return analyze_accessibility()


@app.get("/engine/equipment")
def engine_equipment():
    # 対象は名前ベースのキーワード一致(engine/equipment.pyのEQUIPMENT_
    # KEYWORDS参照)。Objectの座標はバウンディングボックス近似のみ。

    return find_room_equipment()


# ==============================
# Analysis Verification API
# engine/graphが計算した結果をSQLiteに保存したもの(再計算のたびに全削除
# →書き込み直す方式)をそのまま返す、開発時の検証用エンドポイント。
# ==============================

@app.get("/engine/analysis_snapshot")
def engine_analysis_snapshot():

    return get_engine_analysis_snapshot()


@app.get("/graph/relation_snapshot")
def graph_relation_snapshot():

    return get_graph_relation_snapshot()


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


@app.get("/archicad/geo_location")
async def archicad_geo_location():
    # プロジェクト単位の設定値(要素ではない)なのでSQLiteキャッシュには
    # 保存せず、list_archicad_properties()と同じく都度Archicadへ問い合わせる。

    return await _run_archicad_action(get_archicad_geo_location())


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


@app.post("/archicad/elements/focus")
async def archicad_focus_elements(data: FocusArchicadElementsRequest):
    # フロントエンドで要素を選択した際、Archicad本体側でも同じ要素を
    # 選択+ハイライトする(guidsが空なら解除)。カメラ移動は未対応。

    return await _run_archicad_action(focus_archicad_elements(data.guids))


# ==============================
# Legal Knowledge Builder API連携
# 建築関連法のKnowledge Package(knowledge/)を検索する別リポジトリ・別プロセス
# (~/Legal Knowledge Builder/、`uv run legal-knowledge-builder serve`)への
# 薄いプロキシ。法令改訂時のみ再ビルド・再起動される想定でbim-aiagent01の
# 開発サイクルとは独立させており、bim-aiagent01自体には埋め込みモデル等の
# 重い依存を持ち込まない(archicad_mcpの「別サービス+HTTPクライアント」
# 構成をそのまま踏襲)。
# ==============================

async def _run_legal_action(coro):
    try:
        return await coro
    except legal_client.LegalApiNotConfiguredError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except httpx.HTTPStatusError as exc:
        raise HTTPException(status_code=502, detail=f"Legal APIがエラーを返しました: {exc}")
    except httpx.HTTPError as exc:
        raise HTTPException(status_code=502, detail=f"Legal APIへの接続に失敗しました: {exc}")


@app.get("/legal/status")
async def legal_status():

    return await legal_client.check_connection()


@app.get("/legal/connection")
def get_legal_connection():

    return legal_client.get_connection_info()


@app.post("/legal/connection")
async def set_legal_connection(data: ConnectionRequest):

    url = data.url
    if url == "local":
        url = legal_client.LOCAL_PRESET_URL
    legal_client.set_connection_url(url)

    return {
        "connection": legal_client.get_connection_info(),
        "status": await legal_client.check_connection(),
    }


@app.get("/legal/laws")
async def legal_laws():

    return await _run_legal_action(legal_client.list_laws())


@app.get("/legal/search")
async def legal_search(q: str, top_k: int = 5, law_id: str | None = None):

    return await _run_legal_action(legal_client.search(q, top_k=top_k, law_id=law_id))


@app.get("/legal/article")
async def legal_article(law_id: str, num: str):

    return await _run_legal_action(legal_client.get_article(law_id, num))


@app.get("/legal/rules")
async def legal_rules(law_id: str, node_id: str | None = None, concept_id: str | None = None):

    return await _run_legal_action(legal_client.get_rules(law_id, node_id=node_id, concept_id=concept_id))


@app.get("/legal/reference")
async def legal_reference(law_id: str, node_id: str):

    return await _run_legal_action(legal_client.get_reference(law_id, node_id))


@app.get("/legal/graph/neighbors")
async def legal_graph_neighbors(node_id: str, depth: int = 1, edge_types: str | None = None):
    # containment(HAS_CHILD)+引用関係+オントロジーを統合したグラフを起点
    # ノードからdepth段階まで辿る(GraphRAGの多段階検索)。
    types = edge_types.split(",") if edge_types else None

    return await _run_legal_action(legal_client.get_graph_neighbors(node_id, depth=depth, edge_types=types))