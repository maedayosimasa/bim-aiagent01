import json

from mcp.server.mcpserver import MCPServer

from ..database import db
from ..engine.relation_builder import rebuild_connections
from ..engine.spatial import analyze_space
from ..engine.vector_store import search_elements
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
    """Archicadから実際の要素を取得し、ローカルキャッシュ(SQLite)へ同期する。

    Tapirの GetAllElements + GetDetailsOfElements + Get3DBoundingBoxes を
    使う。壁は芯線(begCoordinate/endCoordinate、多角形壁はpolygonOutline)、
    部屋(Zone)は実境界(polygonCoordinates)を使う。それ以外の要素種別は
    2D形状を持たないため、バウンディングボックスのXY投影で近似する。
    要素タイプはArchicadの命名をそのまま使う(部屋は"Room"ではなく"Zone")。
    """
    guids = (await tapir.get_all_element_guids())[:limit]

    if not guids:
        return {"synced": 0, "requested": 0}

    details_list = await tapir.get_details_of_elements(guids)
    bounding_boxes = await tapir.get_bounding_boxes(guids)

    synced = 0

    for guid, details_item, bbox_item in zip(guids, details_list, bounding_boxes):
        element_type = details_item.get("type", "Unknown")
        type_details = details_item.get("details") or {}

        geometry = tapir.details_to_geometry(element_type, type_details, bbox_item)
        name = tapir.details_to_name(element_type, guid, type_details)

        properties = {
            "floorIndex": details_item.get("floorIndex"),
            "layerIndex": details_item.get("layerIndex"),
            "archicad_details": type_details,
        }

        db.insert_element(
            guid, element_type, name, json.dumps(properties), json.dumps(geometry)
        )
        synced += 1

    return {"synced": synced, "requested": len(guids)}


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
