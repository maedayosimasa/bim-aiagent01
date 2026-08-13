import json
import os
from contextlib import AsyncExitStack, asynccontextmanager
from pathlib import Path

import anthropic
import httpx
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
from pydantic import BaseModel
from .agent import service as agent_service
from .database import db as db_module
from .engine.spatial import analyze_space
from .engine.relation_builder import rebuild_connections
from .engine.vector_store import index_elements, search_elements
from .engine.site import get_site_boundary, get_road_boundaries
from .engine.room_engine import analyze_room_adjacency
from .engine.evacuation_engine import find_evacuation_routes
from .engine.code_engine import check_daylighting, check_accessible_door_width
from .engine.effective_daylighting import calculate_effective_daylighting
from .engine.legal_inputs import list_legal_inputs, resolve_legal_input
from .engine.rule_engine import (
    run_daylighting_check,
    run_accessible_door_width_check,
    load_legal_rules,
    evaluate_legal_rule_by_id,
)
from .engine.accessibility import analyze_accessibility
from .engine.equipment import find_room_equipment
from .engine.window_classifier import classify_windows
from .engine.road_slant_envelope import calculate_road_slant_envelope
from .engine.adjacent_boundary_slant_envelope import calculate_adjacent_boundary_slant_envelope
from .engine.north_slant_envelope import calculate_north_slant_envelope
from .engine.height_district_envelope import calculate_height_district_envelope
from .engine.height_restriction_write import (
    propose_road_slant_envelope_mesh,
    propose_adjacent_boundary_slant_envelope_mesh,
    propose_north_slant_envelope_mesh,
    propose_height_district_envelope_mesh,
    approve_envelope_mesh,
)
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
from .legal_mcp import local_process as legal_local_process


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


AGENT_CHECKPOINT_DB_PATH = db_module.BASE_DIR / "agent_checkpoints.db"


@asynccontextmanager
async def lifespan(app: FastAPI):
    # session_manager はstreamable_http_app()呼び出し後でないと参照できない
    async with AsyncExitStack() as stack:
        await stack.enter_async_context(mcp_server.session_manager.run())

        # LangGraphエージェントの会話履歴(session_id単位)をSQLiteへ永続化する
        # checkpointer。bim_cache.dbとは別ファイルに分離する(スキーマが
        # LangGraph管理下でこのプロジェクトの生sqlite3スキーマと無関係なため)。
        checkpointer = await stack.enter_async_context(
            AsyncSqliteSaver.from_conn_string(str(AGENT_CHECKPOINT_DB_PATH))
        )
        await checkpointer.setup()
        agent_service.set_checkpointer(checkpointer)

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


class ApproveHeightRestrictionEnvelopeRequest(BaseModel):
    proposal_id: int


class FocusArchicadElementsRequest(BaseModel):
    guids: list[str]


class AgentChatRequest(BaseModel):
    session_id: str
    message: str


class AgentResumeRequest(BaseModel):
    session_id: str



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


@app.get("/engine/code/daylighting_effective")
def engine_code_daylighting_effective():
    # 建築基準法施行令20条の採光補正係数を用いた計算(engine/effective_daylighting.py)。
    # check_daylighting()(窓面積/床面積の単純比)とは別物で、より法令に近いが
    # 敷地境界線・前面道路のZoneモデリングに依存する等の制約がある(参照)。

    return calculate_effective_daylighting()


@app.get("/engine/rules/daylighting")
async def engine_rules_daylighting():
    # check_daylighting()(engine/code_engine.py)をPASS/FAIL/UNKNOWN判定として
    # 構造化し、Legal Knowledge Builderから該当法令Rule(法的根拠の参考情報。
    # 判定式が条文から自動導出されたわけではない)を添付する(engine/rule_engine.py)。

    return await run_daylighting_check()


@app.get("/engine/rules/accessible_doors")
async def engine_rules_accessible_doors():

    return await run_accessible_door_width_check()


@app.get("/engine/legal_rules")
def engine_legal_rules():
    # 法規チェックの定義一覧(engine/legal_rules.json)。判定式はコードではなく
    # このJSON(rule_id/concept_id/check/verification/disclaimer)で宣言する
    # (engine/rule_engine.py参照)。

    return [rule.model_dump() for rule in load_legal_rules()]


@app.get("/engine/legal_inputs")
def engine_legal_inputs():
    # BIMデータからは判定できない法規条件(用途地域等、engine/legal_inputs.py)
    # の一覧と、現在の解決状況(値の有無・どのルールが使うか)を返す。
    # ユーザーがArchicad側でテンプレート化したいと考えている「法規条件」の
    # 参照スキーマを兼ねる。

    used_by: dict[str, list[str]] = {}
    for rule in load_legal_rules():
        for key in rule.required_inputs:
            used_by.setdefault(key, []).append(rule.rule_id)

    return list_legal_inputs(used_by)


@app.get("/engine/legal_rules/{rule_id}/evaluate")
async def engine_legal_rules_evaluate(rule_id: str):

    try:
        return await evaluate_legal_rule_by_id(rule_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc))


@app.get("/engine/accessibility")
def engine_accessibility():

    return analyze_accessibility()


@app.get("/engine/equipment")
def engine_equipment():
    # 対象は名前ベースのキーワード一致(engine/equipment.pyのEQUIPMENT_
    # KEYWORDS参照)。Objectの座標はバウンディングボックス近似のみ。

    return find_room_equipment()


@app.get("/engine/windows")
def engine_windows():
    # 窓が外部窓か内部窓かを、隣接する部屋数から推定する(参考値。
    # engine/window_classifier.py参照。Archicadの窓要素自体には外部/内部を
    # 示す属性が無いため、ドアの外部判定と同じ次数ベースのヒューリスティック)。

    return classify_windows()


# ==============================
# 高さ制限 envelope の可視化(2026-08-13追加、道路斜線→隣地斜線・北側斜線→高度地区の順で実装)
# 建築基準法56条1項の道路斜線制限(1号)・隣地斜線制限(2号)・北側斜線制限
# (3号)、および都市計画法8条1項3号の高度地区(自治体条例の内容をlegal_
# inputs.pyから明示的に受け取る)を幾何的に近似し(それぞれengine/
# road_slant_envelope.py、engine/adjacent_boundary_slant_envelope.py、
# engine/north_slant_envelope.py、engine/height_district_envelope.py)、
# 承認制でArchicad本体へMeshとして書き込む(engine/height_restriction_
# write.py)。書き込みは「計算(read-only)→提案(監査ログへproposedとして
# 記録、Archicadへは未書き込み)→承認(実際にArchicadへ書き込み)」の3段階で、
# 承認は人間がfrontendから明示的に行う(AIエージェントには公開していない)。
# 承認エンドポイント(/engine/height_restrictions/approve)はenvelopeの種類を
# 判別しないため4種で共通(engine/height_restriction_write.pyのapprove_
# envelope_mesh()参照)。
# ==============================

@app.get("/engine/road_slant_envelope")
def engine_road_slant_envelope(land_use_category: str | None = None):
    # 提案・監査ログへの記録を伴わない、純粋な計算結果のプレビュー。

    try:
        return calculate_road_slant_envelope(land_use_category)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@app.post("/engine/road_slant_envelope/propose")
def engine_road_slant_envelope_propose(land_use_category: str | None = None):
    # envelopeを計算し、write_audit_logへstatus="proposed"として記録する
    # (Archicadへはまだ一切書き込まない)。返却されるproposal_idを
    # /engine/height_restrictions/approveへ渡すことで初めて実際の書き込みが
    # 行われる。

    try:
        return propose_road_slant_envelope_mesh(land_use_category)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@app.get("/engine/adjacent_boundary_slant_envelope")
def engine_adjacent_boundary_slant_envelope(land_use_category: str | None = None):

    try:
        return calculate_adjacent_boundary_slant_envelope(land_use_category)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@app.post("/engine/adjacent_boundary_slant_envelope/propose")
def engine_adjacent_boundary_slant_envelope_propose(land_use_category: str | None = None):

    try:
        return propose_adjacent_boundary_slant_envelope_mesh(land_use_category)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


async def _resolve_north_degrees(north_degrees: float | None) -> float:
    # north_degreesはSQLiteキャッシュに保存されないプロジェクト設定値
    # (archicad_mcp/tapir.pyのget_geo_location()参照)のため、省略時は
    # Archicadへ都度問い合わせる。クエリパラメータでの明示指定(オフライン
    # 検証・テスト用)も許可する。

    if north_degrees is not None:
        return north_degrees

    geo_location = await _run_archicad_action(get_archicad_geo_location())
    resolved = geo_location.get("north_degrees")
    if resolved is None:
        raise HTTPException(
            status_code=502,
            detail="Archicadから方位(north)を取得できませんでした。",
        )
    return resolved


@app.get("/engine/north_slant_envelope")
async def engine_north_slant_envelope(
    north_degrees: float | None = None, kitagawa_shasen_kubun: str | None = None
):

    resolved_north = await _resolve_north_degrees(north_degrees)
    return calculate_north_slant_envelope(resolved_north, kitagawa_shasen_kubun)


@app.post("/engine/north_slant_envelope/propose")
async def engine_north_slant_envelope_propose(
    north_degrees: float | None = None, kitagawa_shasen_kubun: str | None = None
):

    resolved_north = await _resolve_north_degrees(north_degrees)
    return propose_north_slant_envelope_mesh(resolved_north, kitagawa_shasen_kubun)


async def _resolve_north_degrees_if_needed(kubun: str | None, north_degrees: float | None) -> float | None:
    # 高度地区はkubun="north_slant"の場合のみ真北方向が必要(kubun="flat"では
    # 不要)。不要な場合にまでArchicad接続を要求しないよう、実際に使う場合
    # だけ_resolve_north_degrees()(Archicadへの都度問い合わせ)を呼ぶ。

    resolved_kubun = kubun or resolve_legal_input("kodo_chiku_kubun")
    if resolved_kubun != "north_slant":
        return north_degrees
    return await _resolve_north_degrees(north_degrees)


@app.get("/engine/height_district_envelope")
async def engine_height_district_envelope(
    north_degrees: float | None = None,
    kubun: str | None = None,
    max_height_m: float | None = None,
    rise_m: float | None = None,
    gradient: float | None = None,
    kanwa_m: float | None = None,
):

    resolved_north = await _resolve_north_degrees_if_needed(kubun, north_degrees)
    try:
        return calculate_height_district_envelope(
            resolved_north, kubun, max_height_m, rise_m, gradient, kanwa_m
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@app.post("/engine/height_district_envelope/propose")
async def engine_height_district_envelope_propose(
    north_degrees: float | None = None,
    kubun: str | None = None,
    max_height_m: float | None = None,
    rise_m: float | None = None,
    gradient: float | None = None,
    kanwa_m: float | None = None,
):

    resolved_north = await _resolve_north_degrees_if_needed(kubun, north_degrees)
    try:
        return propose_height_district_envelope_mesh(
            resolved_north, kubun, max_height_m, rise_m, gradient, kanwa_m
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@app.post("/engine/height_restrictions/approve")
async def engine_height_restrictions_approve(data: ApproveHeightRestrictionEnvelopeRequest):
    # 指定proposal_idの提案(道路斜線/隣地斜線/北側斜線いずれでも可)を承認し、
    # 実際にArchicad本体へMeshを書き込む(破壊的操作)。未承認
    # (status="proposed")の提案のみ実行できる——既に処理済みの提案を指定
    # した場合や存在しないproposal_idはValueErrorになる
    # (engine/height_restriction_write.py参照)。

    try:
        return await _run_archicad_action(
            approve_envelope_mesh(data.proposal_id)
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@app.get("/engine/write_audit_log")
def engine_write_audit_log(limit: int = 50):
    # Archicad本体への書き込み系操作の監査ログ(誰が/いつ/何を、
    # database.db.write_audit_log参照)。frontendの確認・履歴表示用。

    return [dict(row) for row in db_module.list_audit_log(limit)]


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


@app.post("/legal/start_server")
def legal_start_server():
    # Legal Knowledge Builder(~/Legal Knowledge Builder/)をbackendと同じホスト上で
    # サブプロセスとして起動する。Archicad連携のWindows側ブリッジと違い、
    # backendプロセスと同じホストで動く前提のローカル開発用の別プロセスなので
    # (legal_mcp/local_process.pyのモジュールdocstring参照)、リモートコード
    # 実行にはならない。起動コマンドは固定でユーザー入力を含まない。

    try:
        result = legal_local_process.start_server()
    except legal_local_process.LocalServerNotFoundError as exc:
        raise HTTPException(status_code=400, detail=str(exc))

    # LEGAL_API_URLが未設定の場合、ローカル起動した以上はそのプリセットURLに
    # 接続を合わせておく(セットで使う想定なので、起動後にURLを別途手動設定
    # させない)。既に何か設定/選択されている場合は上書きしない。
    if not legal_client.is_configured():
        legal_client.set_connection_url(legal_client.LOCAL_PRESET_URL)

    result["connection"] = legal_client.get_connection_info()
    return result


@app.get("/legal/start_server/status")
def legal_start_server_status():
    return legal_local_process.get_status()


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


@app.get("/legal/rules/by_concept")
async def legal_rules_by_concept(concept_id: str):
    # law_id不明のまま概念横断でRuleを引く(Rule Engineがconcept_id起点で法令根拠を
    # 探す際に使う。/legal/rulesはlaw_id必須なのでこちらを使う)。

    return await _run_legal_action(legal_client.get_rules_by_concept(concept_id))


@app.get("/legal/concepts")
async def legal_concepts(has_bim_mapping: bool = False):

    return await _run_legal_action(legal_client.get_concepts(has_bim_mapping=has_bim_mapping))


@app.get("/legal/graph/neighbors")
async def legal_graph_neighbors(node_id: str, depth: int = 1, edge_types: str | None = None):
    # containment(HAS_CHILD)+引用関係+オントロジーを統合したグラフを起点
    # ノードからdepth段階まで辿る(GraphRAGの多段階検索)。
    types = edge_types.split(",") if edge_types else None

    return await _run_legal_action(legal_client.get_graph_neighbors(node_id, depth=depth, edge_types=types))


# ==============================
# AIエージェント API(LangGraph)
# CLAUDE.md「⑨ RAG / AIエージェント層」の発話→ツール選択→実行→観察→次の発話
# のループを初めて実装する。ツールは engine/*・database/db.py・legal_mcp
# クライアントを直接呼ぶ読み取り専用・解析系のみ(agent/tools.py参照)。
# 書き込み系のArchicad操作は監査ログ未整備のため意図的に含めていない。
# 会話履歴はsession_id(LangGraphのthread_id)単位でSQLiteに永続化される。
# ==============================

@app.get("/agent/status")
def agent_status():

    return agent_service.get_connection_info()


@app.post("/agent/chat")
async def agent_chat(data: AgentChatRequest):

    try:
        return await agent_service.run_chat(data.session_id, data.message)
    except agent_service.AgentNotConfiguredError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except agent_service.ConversationTooLongError as exc:
        # このsession_idの会話履歴が肥大化しており(過去にコンテキスト上限を
        # 超える巨大なツール結果が書き込まれた等)、以後このセッションでは
        # 二度と成功しない。413(Payload Too Large)でフロントエンドに区別
        # させ、新しいセッションを開始するよう案内する。
        raise HTTPException(status_code=413, detail=str(exc))
    except anthropic.APIError as exc:
        raise HTTPException(status_code=502, detail=f"Claude APIの呼び出しに失敗しました: {exc}")


@app.post("/agent/chat/resume")
async def agent_chat_resume(data: AgentResumeRequest):
    # (2026-08-13追加、Missing Input Interrupt/Resumeパターン)engine_legal_
    # rules_evaluate_tool(agent/tools.py)が法規条件不足でLangGraphの
    # interrupt()により一時停止した会話を再開する。レスポンスの"interrupted"
    # がtrueの間は/agent/chatではなくこちらを呼ぶ(値がまだ不足していれば
    # 再びinterrupted=trueが返る)。

    try:
        return await agent_service.resume_chat(data.session_id)
    except agent_service.AgentNotConfiguredError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except agent_service.ConversationTooLongError as exc:
        raise HTTPException(status_code=413, detail=str(exc))
    except anthropic.APIError as exc:
        raise HTTPException(status_code=502, detail=f"Claude APIの呼び出しに失敗しました: {exc}")


@app.get("/agent/history/{session_id}")
async def agent_history(session_id: str):

    try:
        messages = await agent_service.get_history(session_id)
    except agent_service.AgentNotConfiguredError as exc:
        raise HTTPException(status_code=400, detail=str(exc))

    return {"session_id": session_id, "messages": messages}


@app.get("/agent/usage/daily")
def agent_usage_daily():
    # AIエージェント(Claude API)のトークン使用量を日付(UTC)単位で集計する。
    # agent/service.pyのrun_chat/run_legal_reportが呼び出しのたびに
    # database.db.insert_token_usage()へ記録した実測値の集計であり、
    # 見積もりではない。cost_usdは料金表(agent/pricing.py)に無いモデルの場合null。
    rows = db_module.get_token_usage_daily()
    return {"days": [dict(row) for row in rows]}


@app.get("/agent/usage/jobs")
def agent_usage_jobs():
    # 「作業」単位(chatはsession_id、legal_reportは実行ごと)でのトークン使用量。
    rows = db_module.get_token_usage_by_job()
    return {"jobs": [dict(row) for row in rows]}


@app.post("/agent/legal_report")
async def agent_legal_report():
    # 法規チェック→引用条文添付→レポート生成、の複数ステップグラフ
    # (agent/report_graph.py)。会話(session_id)とは無関係の単発実行で、
    # 毎回、登録済み全ルール(engine/legal_rules.json)を対象に最初から実行する。

    try:
        return await agent_service.run_legal_report()
    except agent_service.AgentNotConfiguredError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except agent_service.ConversationTooLongError as exc:
        raise HTTPException(status_code=413, detail=str(exc))
    except anthropic.APIError as exc:
        raise HTTPException(status_code=502, detail=f"Claude APIの呼び出しに失敗しました: {exc}")