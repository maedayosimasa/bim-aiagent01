import asyncio
import json
import time

from mcp.server.mcpserver import MCPServer

from ..database import db
from ..engine.legal_inputs import ARCHICAD_LEGAL_PROPERTY_KEYWORDS
from ..engine.relation_builder import rebuild_connections
from ..engine.site import is_site_marker_element
from ..engine.spatial import analyze_space
from ..engine.vector_store import index_elements, remove_from_index, search_elements
from ..graph.geometry import geometry_from_json
from . import client as archicad_client
from . import tapir

mcp_server = MCPServer(
    name="bim-spatial-intelligence",
    instructions=(
        "BIM空間知能エンジンのツール群。要素の一覧・検索・空間解析はローカル"
        "キャッシュ(SQLite/ChromaDB)を参照する。編集系ツールはまずローカル"
        "キャッシュを更新する。Archicad本体との実同期は list_archicad_tools "
        "/ call_archicad_tool 経由でのみ可能で、PC側ブリッジ(Tailscale)が"
        "接続されていない場合はエラーになる。"
    ),
)


def _element_to_dict(row):
    element = dict(row)
    element["properties"] = json.loads(element["properties"]) if element["properties"] else {}
    element["geometry"] = json.loads(element["geometry"]) if element["geometry"] else {}
    return element


@mcp_server.tool()
def list_elements() -> list[dict]:
    """キャッシュされている全BIM要素を返す。"""
    return [_element_to_dict(row) for row in db.get_elements()]


def _engine_analysis_result_to_dict(row):
    if row is None:
        return None
    result = dict(row)
    result["connected"] = bool(result["connected"])
    result["issues"] = json.loads(result["issues"]) if result["issues"] else []
    result["graph_data"] = json.loads(result["graph_data"]) if result["graph_data"] else {}
    return result


@mcp_server.tool()
def get_engine_analysis_snapshot() -> dict | None:
    """最後に保存されたengine側の解析結果スナップショットを返す(検証用)。

    analyze_bim_space()を呼ぶたびに全削除→1行だけ書き込みで置き換わる
    (履歴は積まない)。まだ一度も解析していなければNone。
    """
    return _engine_analysis_result_to_dict(db.get_engine_analysis_result())


@mcp_server.tool()
def get_graph_relation_snapshot() -> list[dict]:
    """最後に保存されたgraph側の関係計算結果一式を返す(検証用)。

    rebuild_relations()(analyze_bim_space()内部からも呼ばれる)のたびに
    全削除→まとめて書き込みで置き換わる(履歴は積まない)。
    """
    return [dict(row) for row in db.get_graph_relation_results()]


@mcp_server.tool()
def search_bim_elements(query: str, n_results: int = 5) -> list[dict]:
    """自然文クエリでBIM要素を意味検索する。"""
    return search_elements(query, n_results=n_results)


@mcp_server.tool()
def analyze_bim_space(model_id: str = "default") -> dict:
    """空間解析(隣接・接続グラフ)を実行して結果を返す。"""
    return analyze_space(model_id)


@mcp_server.tool()
def rebuild_relations() -> dict:
    """要素間の関係(隣接/接続)をキャッシュ上で再計算する。"""
    relations = rebuild_connections()
    return {"count": len(relations), "relations": relations}


@mcp_server.tool()
def update_element_properties(guid: str, properties: dict) -> dict:
    """要素のプロパティをローカルキャッシュ上で更新する(既存値とマージ)。"""
    if not db.update_element_properties(guid, properties):
        raise ValueError(f"Element not found: {guid}")
    return _element_to_dict(db.get_element(guid))


@mcp_server.tool()
def update_element_geometry(guid: str, geometry: dict) -> dict:
    """要素のジオメトリをローカルキャッシュ上で更新する(全置換)。"""
    geometry_from_json(json.dumps(geometry))  # 不正な形状なら例外にする
    if not db.update_element_geometry(guid, geometry):
        raise ValueError(f"Element not found: {guid}")
    return _element_to_dict(db.get_element(guid))


@mcp_server.tool()
def create_element(
    guid: str,
    element_type: str,
    name: str,
    properties: dict,
    geometry: dict,
) -> dict:
    """新規BIM要素をローカルキャッシュへ作成する(既存GUIDなら上書き)。"""
    geometry_from_json(json.dumps(geometry))  # 不正な形状なら例外にする
    db.insert_element(
        guid, element_type, name, json.dumps(properties), json.dumps(geometry)
    )
    return _element_to_dict(db.get_element(guid))


@mcp_server.tool()
def delete_element(guid: str) -> dict:
    """BIM要素をローカルキャッシュから削除する。"""
    if not db.delete_element(guid):
        raise ValueError(f"Element not found: {guid}")
    return {"guid": guid, "deleted": True}


@mcp_server.tool()
async def list_archicad_tools() -> list[str]:
    """PC側ブリッジ経由で接続中のArchicad MCPサーバーのツール名一覧を返す。

    実際のツール契約(名前・引数)はPC側ブリッジが稼働するまで不明なため、
    まずこれで発見してから call_archicad_tool を使う想定。
    """
    return await archicad_client.list_tools()


@mcp_server.tool()
async def call_archicad_tool(name: str, arguments: dict | None = None) -> str:
    """Archicad MCPサーバーの任意のツールを呼び出す汎用パススルー。"""
    result = await archicad_client.call_tool(name, arguments)
    return "\n".join(
        block.text for block in result.content if hasattr(block, "text")
    )


@mcp_server.tool()
async def sync_from_archicad(limit: int = 50) -> dict:
    """Archicadから実際の要素を取得し、ローカルキャッシュ(SQLite)を丸ごと置き換える。

    毎回、既存のelementsテーブルを全削除してから今回取得した分だけを
    保存する(差分マージではない)。Archicad側で削除された要素が
    キャッシュに残り続けて開発時の確認データが実態とずれるのを防ぐため。
    limitを0以下にすると全件取得(切り詰めなし)になる - この置き換え
    方式では中途半端な件数で打ち切ると、以前あった要素の大半が
    キャッシュから失われてしまうため、開発時の動作確認用途では
    基本的に0(全件)を指定する想定。

    Tapirの GetAllElements + GetDetailsOfElements + Get3DBoundingBoxes +
    GetAttributesByType(ZoneCategory / Layer) を使う。壁は芯線
    (begCoordinate/endCoordinate、多角形壁はpolygonOutline)、部屋(Zone)・
    Mesh(2026-08-13追加、道路等の地形をMeshでモデリングする運用に対応)は
    実境界(polygonCoordinates)を使う。それ以外の要素種別は2D形状を
    持たないため、バウンディングボックスのXY投影で近似する。要素タイプは
    Archicadの命名をそのまま使う(部屋は"Room"ではなく"Zone")。
    MeshはZoneと違って実名("name")を持たないため、engine/site.pyが
    Meshを名前検索する際はproperties.layer_name(下記)を代わりに使う。

    ZoneはcategoryAttributeIdが指すゾーンカテゴリ名(例:"住宅-1")を解決し
    properties.zone_categoryに表示用の情報として保存する。個々の部屋か、
    住戸/区画全体を表す大分類ゾーンかの判定にはこのカテゴリ名を使わない
    ——命名規則はArchicadのテンプレートによって様々でありうるため、
    graph/relation.pyのcalculate_relations()とgraph/room.pyのfind_rooms()
    は、テンプレートに依存しない幾何包含(graph/envelope.py)を根拠に
    大分類ゾーンを除外する。

    全要素のlayerIndexが指すレイヤー名も解決し、properties.layer_nameに
    保存する。3Dビュー(フロントエンド)で「敷地外_周辺建物」等の参考用
    レイヤーの要素(日影検討用の大まかなマスボリューム等、実際の建物本体
    とは別カテゴリ)を区別して表示するために使う(実データで、このレイヤー
    に厚み4〜11mという現実的でないSlabが配置されていることを確認済み)。

    (2026-08-13追加)GetDetailsOfElementsが返す"id"(型に依存しない共通
    必須フィールド。Archicad UIの「分類とプロパティ」パネルの「IDとカテゴリ」
    グループの「ID」欄そのもので、Zoneの実名と違い全要素タイプに存在する。
    実データで確認: {"type": "Mesh", "id": "塗膜防水", ...})を
    properties.archicad_idとして保存する。以前はこのフィールドを一切
    読んでいなかった。Meshには(Zoneと違い)details.nameが無いため
    (archicad_mcp/tapir.pyのモジュールdocstring参照)、engine/site.pyが
    Meshを名前検索する際はこのarchicad_idを使う。

    (2026-08-14追加)敷地境界線Zone(名前・archicad_id・layer_nameに"敷地"を
    含む要素)については、GetPropertyValuesOfElementsでカスタムProperty値
    (建蔽率・容積率・前面道路幅等、ユーザーが「分類とプロパティ」パネルの
    「法条件」グループへ入力している)も取得し、properties.legal_conditions
    として保存する(_sync_legal_condition_properties()参照)。engine/
    legal_inputs.pyのresolve_legal_input()がこれを読む。

    (2026-08-14追加、差分検知に基づく下流ストアの自動更新)以前は同期を
    実行しても`connections`(空間関係)・ChromaDB検索インデックスは古いまま
    放置され、フロントエンド「要素同期」タブの3つの独立したボタン(同期→
    関係再構築→検索インデックス化)をユーザーが手動で順番に押す必要が
    あった(CLAUDE.md「⑧ 埋め込み/インデックス層」に記載していた既知の
    ギャップ)。ユーザーから「BIMから取り込んだデータに変更があれば、差分を
    確認し、計算や表示をそれぞれのデータベースで確実に更新するように」との
    依頼を受け、以下を同期処理自体に統合した:
      1. 既存要素(guid→type/name/properties/geometry)を`db.replace_all_
         elements()`で置き換える前に退避し、今回同期する内容と突き合わせて
         追加(added)・変更(changed)・削除(removed)・不変(unchanged)を
         判定する(全要素の削除+書き込みは`db.replace_all_elements()`が
         1トランザクションとして実行する——同期処理が要素の途中で
         中断すると、以前は全要素削除済み・新データは一部のみという
         中途半端な状態がDBに残ってしまう不具合があったため、2026-08-14に
         `database.db.transaction()`を導入して解消した)。
      2. 追加・変更・削除が1件でもあれば`rebuild_connections()`
         (`connections`/`graph_relation_results`の再計算、実測0.2秒程度と
         軽量なため差分の大小に関わらず常にフル再計算する)を実行する。
      3. ChromaDB検索インデックスは追加・変更されたguidのみを`index_
         elements(guids=...)`でインクリメンタル更新し(実データ5706件の
         フル再インデックスは約46秒かかるため、変更の無い大多数の要素まで
         毎回埋め込み直すのは非現実的)、削除されたguidは`remove_from_
         index()`でインデックスからも削除する(以前は削除された要素の
         埋め込みが永久にインデックスに残り続ける"ghost"エントリになる
         不具合があった——`index_elements()`はupsertのみで削除を伴わない
         ため)。
      4. 何も変わっていない場合(全要素が既存内容と完全一致)はこれらの
         処理を一切実行しない(法規レポートの差分キャッシュ
         (`agent/report_graph.py`)と同じ考え方)。
    `rebuild_connections()`/`index_elements()`/`remove_from_index()`は
    いずれも同期処理を待たせるブロッキング処理(特にindex_elements()は
    要素数に応じて数秒〜数十秒かかりうる)なので、`asyncio.to_thread()`で
    実行しFastAPIのイベントループを塞がないようにする(`/bim/index`等の
    既存の同期(sync)エンドポイントはFastAPIの通常のスレッドプール実行に
    任せているが、`sync_from_archicad()`自体は`async def`のため明示的な
    委譲が必要)。`engine_analysis_results`(`/analyze`、model_id必須の
    診断用スナップショット)は自動更新の対象に含めていない——特定の
    model_idを推測する妥当な根拠が無く、他のengine計算はこのスナップ
    ショットに依存しない(呼び出しのたびに独立して再計算する)ため。
    """
    # (2026-09-05追加、実機で発覚した性能問題への対応)以前はここから続く
    # 5回のTapir呼び出し(GetAllElements/GetDetailsOfElements/
    # Get3DBoundingBoxes/GetAttributesByType×2)それぞれが個別に
    # archicad_client.call_tool()を呼んでおり、呼び出しのたびにMCP
    # セッションを新規に開いて(initializeハンドシェイク+SSEストリーム
    # 確立)閉じて(セッション破棄)いた。1つのセッションを使い回すことで
    # このオーバーヘッドを5回分から1回分に減らし、さらに互いに依存しない
    # 4呼び出しはasyncio.gather()で並列実行する(MCPはJSON-RPCベースで
    # リクエストIDにより応答を紐付けるため、1セッション上での複数
    # リクエストの同時実行は仕様上安全)。
    #
    # (同日、詳細な計測の結果判明した追加の事実)当初はセッション確立・
    # 破棄の往復が所要時間の大半を占めていると推測していたが、
    # time.monotonic()でsync_from_archicad()内の各区間を実測したところ
    # (EC2・Tailscale DERPリレー経由、実データ5706要素)、db.get_elements()
    # ・このモジュールレベルのPythonループ・db.replace_all_elements()は
    # 合計0.3秒程度に過ぎず、支配的なのは実データ転送そのもの(下記
    # print、Tapir取得だけで70〜90秒程度、敷地プロパティ取得も20秒前後)
    # だと判明した。GetDetailsOfElements/Get3DBoundingBoxesは全要素の
    # 詳細ジオメトリをまとめて1回で返す設計のためレスポンス自体が大きく、
    # DERPリレー(EC2からのTailscale直接P2P接続が確立していない環境)の
    # 帯域が実質的なボトルネックになっている。このセッション共有・並列化
    # 自体は既に実施済みの改善だが、残る遅さはPC側のネットワーク環境
    # (ルーターのUPnP/NAT設定等でTailscaleの直接P2P接続を確立できるか)
    # に依存するため、コード側でこれ以上大きく削れる余地は乏しい。
    # 以下の2箇所の所要時間ログ(print)は、今後この処理が遅く感じられた
    # 際に「ネットワーク(Tapir取得・敷地プロパティ取得)が遅いのか、
    # それ以外(下のrebuild_connections/index_elements等)が遅いのか」を
    # ログだけで切り分けられるように恒常的に残す
    # (CLAUDE.md「可観測性の整備」が未着手だったための最小限の対応)。
    _t_sync_start = time.monotonic()
    async with archicad_client.open_session() as session:
        all_guids = await tapir.get_all_element_guids(session=session)
        guids = all_guids if limit <= 0 else all_guids[:limit]

        if not guids:
            return {
                "synced": 0,
                "requested": 0,
                "legal_conditions_synced": {},
                "diff": {"added": 0, "changed": 0, "removed": 0, "unchanged": 0},
                "relations_rebuilt": False,
                "relations_count": None,
                "index_updated_count": 0,
                "index_removed_count": 0,
            }

        details_list, bounding_boxes, zone_categories, layers = await asyncio.gather(
            tapir.get_details_of_elements(guids, session=session),
            tapir.get_bounding_boxes(guids, session=session),
            tapir.get_zone_categories(session=session),
            tapir.get_layer_names(session=session),
        )
    print(f"[archicad_sync] Tapir取得(セッション確立+並列取得): {time.monotonic() - _t_sync_start:.2f}s", flush=True)

    category_name_by_guid = {
        category["attributeId"]["guid"]: category["name"] for category in zone_categories
    }
    layer_name_by_index = {layer["index"]: layer["name"] for layer in layers}

    # (2026-08-14追加)replace_all_elements()で置き換えられる前に、
    # 差分判定用に既存の内容(guid→type/name/properties/geometryの
    # 4つ組)を退避しておく。
    previous_elements = {
        element["guid"]: (
            element["type"], element["name"], element["properties"], element["geometry"]
        )
        for element in db.get_elements()
    }

    synced = 0
    site_guids = []
    new_guids: set[str] = set()
    added_guids: list[str] = []
    changed_guids: list[str] = []
    # (2026-08-14追加、トランザクション化)以前はここでdb.clear_elements()
    # (1コミット)を呼んだ上で、下のループ内でdb.insert_element()を要素
    # ごとに個別コミットしていた。同期処理が要素の途中でクラッシュすると、
    # 全要素削除済み・新データは一部のみという中途半端な状態がそのまま
    # DBに残ってしまう不具合があった(database.db.transaction()参照)。
    # 要素はここでは書き込まず、まとめてpending_elementsへ蓄積し、
    # ループの後にdb.replace_all_elements()で削除+書き込みを1つの
    # トランザクションとして実行する。
    pending_elements: list[tuple[str, str, str, str, str]] = []

    for guid, details_item, bbox_item in zip(guids, details_list, bounding_boxes):
        element_type = details_item.get("type", "Unknown")
        type_details = details_item.get("details") or {}

        geometry = tapir.details_to_geometry(element_type, type_details, bbox_item)
        name = tapir.details_to_name(element_type, guid, type_details)

        zone_category = None

        if element_type == "Zone":
            category_guid = (type_details.get("categoryAttributeId") or {}).get("guid")
            zone_category = category_name_by_guid.get(category_guid)

        archicad_id = details_item.get("id") or None
        layer_name = layer_name_by_index.get(details_item.get("layerIndex"))

        properties = {
            "floorIndex": details_item.get("floorIndex"),
            "layerIndex": details_item.get("layerIndex"),
            "layer_name": layer_name,
            "archicad_id": archicad_id,
            "archicad_details": type_details,
            "zone_category": zone_category,
        }

        properties_json = json.dumps(properties)
        geometry_json = json.dumps(geometry)

        pending_elements.append((guid, element_type, name, properties_json, geometry_json))
        synced += 1
        new_guids.add(guid)

        previous = previous_elements.get(guid)
        if previous is None:
            added_guids.append(guid)
        elif previous != (element_type, name, properties_json, geometry_json):
            changed_guids.append(guid)

        # (2026-08-14実データ調査で修正)matches_zone_keyword()のarchicad_id/
        # layer_name照合はMesh限定(敷地境界線の幾何取得という別の用途向けの
        # 制約)だが、実データでは"Object"型要素にもarchicad_id="敷地"が
        # 付けられているケースを確認した。法条件プロパティの取得は幾何を
        # 使わないため型を問わず広く拾う(見逃しよりも過検出を許容する)。
        # (2026-08-14同日追加修正)ただし型を問わない分、"敷地外_地盤"
        # (layer_name)・"周辺敷地"(archicad_id)のような地盤モデリング用
        # Meshまで誤って拾ってしまう不具合が実データで発覚したため、
        # is_site_marker_element()で"敷地外"・"周辺敷地"を除外する
        # (engine/site.pyのget_site_boundary()と同じ除外パターン)。
        if is_site_marker_element(element_type, name, archicad_id, layer_name):
            site_guids.append(guid)

    # 全要素の削除+書き込みを1つのトランザクションとして実行する
    # (db.replace_all_elements()のdocstring参照)。ブロッキング処理を
    # asyncio.to_thread()で別スレッドに逃がしFastAPIのイベントループを
    # 塞がないのは他の下流更新(rebuild_connections/index_elements)と
    # 同じ理由。
    await asyncio.to_thread(db.replace_all_elements, pending_elements)

    _t_legal = time.monotonic()
    legal_conditions_synced = await _sync_legal_condition_properties(site_guids)
    print(f"[archicad_sync] 敷地プロパティ取得: {time.monotonic() - _t_legal:.2f}s", flush=True)

    removed_guids = [guid for guid in previous_elements if guid not in new_guids]
    changed_or_added_guids = added_guids + changed_guids
    has_changes = bool(changed_or_added_guids or removed_guids)

    relations_count = None
    indexed_count = 0
    removed_from_index_count = 0

    if has_changes:
        # rebuild_connections()/index_elements()/remove_from_index()は
        # いずれもブロッキング処理(index_elements()は要素数によっては
        # 数十秒かかる)なので、async defであるこの関数から直接呼ぶと
        # FastAPIのイベントループを塞いでしまう。to_thread()で別スレッドに
        # 逃がす(モジュールdocstring参照)。
        relations = await asyncio.to_thread(rebuild_connections)
        relations_count = len(relations)

        if changed_or_added_guids:
            indexed_count = await asyncio.to_thread(
                index_elements, guids=changed_or_added_guids
            )
        if removed_guids:
            await asyncio.to_thread(remove_from_index, removed_guids)
            removed_from_index_count = len(removed_guids)

    return {
        "synced": synced,
        "requested": len(guids),
        "legal_conditions_synced": legal_conditions_synced,
        # (2026-08-14追加)差分検知の結果と、それに基づき自動更新した
        # 下流ストア(connections/ChromaDB検索インデックス)の内容。
        "diff": {
            "added": len(added_guids),
            "changed": len(changed_guids),
            "removed": len(removed_guids),
            "unchanged": synced - len(added_guids) - len(changed_guids),
        },
        "relations_rebuilt": has_changes,
        "relations_count": relations_count,
        "index_updated_count": indexed_count,
        "index_removed_count": removed_from_index_count,
    }


async def _sync_legal_condition_properties(site_guids: list[str]) -> dict:
    """敷地ZoneのArchicadカスタムProperty(建蔽率・容積率・前面道路幅等)を
    取得し、properties.legal_conditionsとして保存する。

    戻り値は{guid: {プロパティ名: 値}}(実際に見つかった内容をsync_from_
    archicad()の戻り値経由でそのまま確認できるようにする診断用の情報、
    2026-08-14追加——プロパティ名の照合キーワードが実際のArchicad側の
    表記と一致しているかを、SQLiteキャッシュを直接見なくても確認できる)。

    (2026-08-14追加)engine/legal_inputs.pyが以前「今後の課題」としていた
    カスタムProperty未対応のギャップに対応する——ユーザーが実際に敷地Zoneの
    「分類とプロパティ」パネルの「法条件」グループへ建蔽率・容積率・前面
    道路幅を入力する運用を開始したことを受けたもの。プロパティ名の完全一致は
    要求せず、engine.legal_inputs.ARCHICAD_LEGAL_PROPERTY_KEYWORDSに列挙した
    キーワードの部分一致で対象プロパティを探す(自治体・テンプレートによる
    名称の揺れに強くするため)。該当するプロパティが1つも見つからない場合、
    または敷地Zoneが1つも無い場合は何もしない(GetPropertyValuesOfElements
    自体を呼ばない、無駄なArchicad呼び出しを避けるため)。
    """
    if not site_guids:
        return {}

    all_properties = await tapir.list_properties()
    keywords = [kw for kws in ARCHICAD_LEGAL_PROPERTY_KEYWORDS.values() for kw in kws]

    matched_properties = [
        prop for prop in all_properties
        if any(kw in (prop.get("propertyName") or "") for kw in keywords)
    ]

    if not matched_properties:
        return {}

    property_guids = [prop["propertyId"]["guid"] for prop in matched_properties]
    property_names = [prop.get("propertyName") for prop in matched_properties]

    values_by_element = await tapir.get_property_values(site_guids, property_guids)

    legal_conditions_by_guid: dict = {}

    for guid, element_result in zip(site_guids, values_by_element):
        # (2026-08-14実データで発覚・修正)propertyValuesForElementsの各要素は
        # 素の配列ではなく{"propertyValues": [...]}というオブジェクト
        # (PropertyValuesOrError、common_schema_definitions.js確認済み)。
        # 要素自体が見つからない等の場合は{"error": {...}}になる。この
        # ラップを見落とし、直接zip()していたため要素の辞書キー("propertyValues"
        # という文字列自体)がvalue_itemに入り込み、AttributeErrorで
        # クラッシュしていた(実データでの実行で発覚)。
        if "error" in element_result:
            continue

        property_value_items = element_result.get("propertyValues", [])

        legal_conditions = {}
        for property_name, value_item in zip(property_names, property_value_items):
            # 個々のプロパティも同様にPropertyValueOrError(oneOf)で、
            # 未設定・取得不可の場合は{"error": {...}}になりうる。
            if "error" in value_item:
                continue
            value = (value_item.get("propertyValue") or {}).get("value")
            if value is not None:
                legal_conditions[property_name] = value

        if legal_conditions:
            db.update_element_properties(guid, {"legal_conditions": legal_conditions})
            legal_conditions_by_guid[guid] = legal_conditions

    return legal_conditions_by_guid


@mcp_server.tool()
async def get_archicad_geo_location() -> dict:
    """Archicadプロジェクトの位置情報(緯度経度・標高・北方向)を取得する。"""
    return await tapir.get_geo_location()


@mcp_server.tool()
async def list_archicad_properties() -> list[dict]:
    """Archicadの全プロパティ定義(GUID・名前・グループ等)を返す。

    プロパティ値のGET/SETにはこのpropertyId.guidが必要。
    """
    return await tapir.list_properties()


@mcp_server.tool()
async def get_archicad_property_values(
    guids: list[str], property_guids: list[str]
) -> list:
    """指定要素の指定プロパティ値をArchicadから取得する(生の構造体を返す)。"""
    return await tapir.get_property_values(guids, property_guids)


@mcp_server.tool()
async def set_archicad_property_value(
    guid: str, property_guid: str, value: str
) -> dict:
    """Archicad本体の要素プロパティ値を実際に書き換える(破壊的操作)。"""
    return await tapir.set_property_value(guid, property_guid, value)


@mcp_server.tool()
async def focus_archicad_elements(guids: list[str]) -> dict:
    """Archicad本体で指定要素を選択+ハイライトする(guidsが空なら解除)。

    Tapirにはカメラを要素まで移動させるコマンドがないため、実際に画面を
    スクロールするのはユーザー操作に委ねる(選択+ハイライトまでを行う)。
    """
    return await tapir.focus_elements(guids)


@mcp_server.tool()
async def move_archicad_element(
    guid: str, dx: float, dy: float, dz: float = 0, copy: bool = False
) -> dict:
    """Archicad本体の要素を相対ベクトルで移動する(破壊的操作)。

    Tapirの仕様上、絶対座標の指定はできず相対移動ベクトルのみ。
    """
    return await tapir.move_element(guid, dx, dy, dz, copy)


@mcp_server.tool()
async def delete_archicad_elements(guids: list[str]) -> dict:
    """Archicad本体から要素を削除する(破壊的操作、元に戻せない)。"""
    return await tapir.delete_elements(guids)


@mcp_server.tool()
async def create_archicad_mesh(
    vertices: list[dict], level: float = 0.0, skirt_level: float = 0.0
) -> dict:
    """Archicad本体に新規Mesh要素を作成する(破壊的操作)。

    (2026-08-13追加)このツール自体は他の書き込み系ツール同様、承認確認を
    内蔵しない薄いラッパー(既存のmove_archicad_element/delete_archicad_
    elementsと同じ設計方針)。承認フロー・監査ログは呼び出し側の
    engine/height_restriction_write.pyが担う——このツールは意図的に
    AIエージェント(agent/tools.py)には公開していない。人間が明示的に
    承認した後、frontend経由のREST APIからのみ呼ばれる想定。
    vertices各要素は{"x":mm,"y":mm,"z":mm}(このプロジェクトの座標系)。
    """
    return await tapir.create_mesh(vertices, level, skirt_level)
