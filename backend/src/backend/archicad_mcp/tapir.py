"""Typed helpers for the specific Tapir JSON API commands this project uses.

Shapes were confirmed against archicad-mcp's actual Tapir definitions
(src/tapir/command_definitions.js, common_schema_definitions.js) - not
guessed. Notably:

- GetAllElements / GetElementsByType return {"elements": [{"elementId":
  {"guid": ...}}, ...]}.
- GetDetailsOfElements returns {"detailsOfElements": [{"type": <str
  ElementType enum, e.g. "Wall"/"Zone">, "id": <guid str>, "floorIndex",
  "layerIndex", "drawIndex", "details": <huge type-specific structure>}]}.
  Archicad calls rooms "Zone", not "Room" - callers that compare against
  this project's own RELATION_RULES (which uses "Room") need to account
  for that; this module intentionally does not rename it.
- Get3DBoundingBoxes returns {"boundingBoxes3D": [{"boundingBox3D": {xMin,
  yMin, zMin, xMax, yMax, zMax}} | {"error": ...}]}, same order as input.
  zMin/zMax are attached to every synced element's geometry as "z_min"/
  "z_max" (mm, see _z_range_from_bounding_box()) regardless of which 2D
  shape branch produced the geometry - graph/relation.py uses this range
  to avoid treating elements on different floors as spatially adjacent
  just because their (x,y) footprint happens to line up (e.g. the same
  column position repeated on every floor of a multi-story building).
- "details" (from GetDetailsOfElements) is a `oneOf` union with no
  discriminator field of its own - which shape it is depends entirely on
  the sibling "type" field. Confirmed against WallDetails/ZoneDetails:
  - Wall: {"geometryType": "Straight"|"Trapezoid"|"Polygonal",
    "begCoordinate": {x,y}, "endCoordinate": {x,y}, ...,
    "polygonOutline": [{x,y}, ...] (only for "Polygonal")}. beg/end is the
    wall's centerline, not its outline - real thickness is not captured
    as a shape, only as the separate begThickness/endThickness numbers.
  - Zone: {"name": str, "polygonCoordinates": [{x,y}, ...] (>=3), ...} -
    this is the room's real boundary and a real display name, unlike
    every other type here.
  - Mesh: {"level", "skirtType", "skirtLevel", "polygonCoordinates":
    [{x,y,z}, ...] (>=3), "polygonArcs", "holes", "sublines"} - confirmed
    against MeshDetails in common_schema_definitions.js. Unlike Zone this
    has no "name" field at all, so details_to_name() still falls back to
    the synthetic "Mesh_<guid>" label for it - callers that need to find
    a specific Mesh by a user-given label (e.g. site.py identifying a
    "前面道路" Mesh) must use another per-element label Archicad actually
    exposes, such as the resolved layer name (properties.layer_name,
    populated by sync_from_archicad() via GetAttributesByType), not the
    element name.
  - Everything else (Door, Window, Object, ...) has no direct 2D outline
    in "details" - LibPartBasedElementDetails only carries the owning
    wall's id, not a position. Get3DBoundingBoxes remains the only
    available geometry source for those.
  Curved segments ("polygonArcs") are not tessellated - arcs are treated
  as straight edges between their endpoints, a deliberate simplification.
- MoveElements takes a *relative* vector (elementsWithMoveVectors), not an
  absolute position - it cannot be used to implement "set to this exact
  geometry".
- SetPropertyValuesOfElements accepts propertyValue as the simplified
  {"value": "<display string>"} form; GetPropertyValuesOfElements returns
  the fuller typed union (NormalStringPropertyValue etc.) - this module
  returns that raw structure rather than guessing a normalized shape.
"""

import json
import math

from . import client as archicad_client


async def _call(name, arguments=None, transport=None):
    # archicad-mcp wraps every dynamically-registered Tapir command behind
    # a single "input" parameter (see its register_tapir.py:
    # `def dynamic_command(input, ...)`), so the real Tapir arguments must
    # be nested one level under "input" rather than passed as the MCP
    # tool's top-level arguments - confirmed against a real "'input' is a
    # required property" error from the live server.
    result = await archicad_client.call_tool(
        name, {"input": arguments}, transport=transport
    )

    if result.is_error:
        text = "; ".join(
            block.text for block in result.content if hasattr(block, "text")
        )
        raise RuntimeError(f"Archicad tool {name!r} failed: {text}")

    if result.structured_content is not None:
        return result.structured_content

    # archicad-mcp is built on a different MCP server implementation
    # (fastmcp) than our own (mcp SDK) - structured_content population
    # isn't guaranteed, so fall back to parsing the text content as JSON.
    text = "".join(block.text for block in result.content if hasattr(block, "text"))
    return json.loads(text) if text else {}


def _element_ids(guids):
    return [{"elementId": {"guid": guid}} for guid in guids]


async def get_all_element_guids(transport=None):
    payload = await _call("GetAllElements", transport=transport)
    return [item["elementId"]["guid"] for item in payload.get("elements", [])]


async def get_element_guids_by_type(element_type, transport=None):
    payload = await _call(
        "GetElementsByType", {"elementType": element_type}, transport=transport
    )
    return [item["elementId"]["guid"] for item in payload.get("elements", [])]


async def get_details_of_elements(guids, transport=None):
    payload = await _call(
        "GetDetailsOfElements", {"elements": _element_ids(guids)}, transport=transport
    )
    return payload.get("detailsOfElements", [])


async def get_bounding_boxes(guids, transport=None):
    payload = await _call(
        "Get3DBoundingBoxes", {"elements": _element_ids(guids)}, transport=transport
    )
    return payload.get("boundingBoxes3D", [])


async def get_selected_element_guids(transport=None):
    payload = await _call("GetSelectedElements", transport=transport)
    return [item["elementId"]["guid"] for item in payload.get("elements", [])]


async def select_elements(guids, transport=None):
    """Archicad本体の選択状態を指定guid群に置き換える(既存選択は解除)。

    TapirにはChangeSelectionOfElements(追加/削除)しかなく、絶対指定の
    「これに置き換える」コマンドはない。そのため現在の選択をまず取得し、
    それを丸ごとremoveElementsFromSelectionしつつ、新しい選択をaddする。
    guidsが空なら選択解除のみになる。
    """
    current = await get_selected_element_guids(transport=transport)

    arguments = {}
    if current:
        arguments["removeElementsFromSelection"] = _element_ids(current)
    if guids:
        arguments["addElementsToSelection"] = _element_ids(guids)

    if not arguments:
        return {"executionResultsOfAddToSelection": [], "executionResultsOfRemoveFromSelection": []}

    return await _call("ChangeSelectionOfElements", arguments, transport=transport)


# フロントエンドでクリックされた要素をArchicad本体側で目立たせるための色。
# 選択中の要素は明るいオレンジ、それ以外は薄いグレーに沈める。
_FOCUS_COLOR = [255, 122, 24, 255]
_DIMMED_COLOR = [200, 200, 200, 90]


async def highlight_elements(guids, transport=None):
    """指定要素をArchicad本体でハイライトする。guidsが空なら全ハイライト解除。

    Tapirにはカメラ/ビューを要素へ移動させるコマンドが存在しない(2026-08-04
    時点でArchicad MCPブリッジのツール一覧を確認済み)。そのためこの関数は
    「選択+ハイライト」までを行い、実際に画面をその要素までスクロール/
    ズームするのはユーザー側の操作に委ねる。
    """
    if not guids:
        return await _call(
            "HighlightElements",
            {"elements": [], "highlightedColors": []},
            transport=transport,
        )

    return await _call(
        "HighlightElements",
        {
            "elements": _element_ids(guids),
            "highlightedColors": [_FOCUS_COLOR for _ in guids],
            "nonHighlightedColor": _DIMMED_COLOR,
        },
        transport=transport,
    )


async def focus_elements(guids, transport=None):
    """選択+ハイライトをまとめて行う(フロントエンドの1クリックに対応する単位)。"""
    selection_result = await select_elements(guids, transport=transport)
    highlight_result = await highlight_elements(guids, transport=transport)
    return {"selection": selection_result, "highlight": highlight_result}


async def move_element(guid, dx, dy, dz=0, copy=False, transport=None):
    return await _call(
        "MoveElements",
        {
            "elementsWithMoveVectors": [
                {
                    "elementId": {"guid": guid},
                    "moveVector": {"x": dx, "y": dy, "z": dz},
                    "copy": copy,
                }
            ]
        },
        transport=transport,
    )


async def delete_elements(guids, transport=None):
    return await _call(
        "DeleteElements", {"elements": _element_ids(guids)}, transport=transport
    )


async def get_geo_location(transport=None):
    """プロジェクトの位置情報(緯度経度・標高・北方向)を取得する。

    GetGeoLocationはTapirコマンド定義(command_definitions.js)上
    inputSchemeがnullの引数無しコマンド。他の要素単位の情報とは異なり
    プロジェクト全体の設定値なので、sync_from_archicad()のような
    SQLiteキャッシュへの保存は行わず、要求のたびにArchicadへ問い合わせる
    (list_properties()と同じ「都度取得」方式)。
    "north"はラジアンで返ってくるため、扱いやすいよう度数のnorth_degrees
    を追加して返す。
    """
    payload = await _call("GetGeoLocation", transport=transport)
    location = payload.get("projectLocation") or {}
    north_radians = location.get("north")

    return {
        **payload,
        "north_degrees": (
            math.degrees(north_radians) if north_radians is not None else None
        ),
    }


async def get_zone_categories(transport=None):
    """ゾーンカテゴリ属性(住宅/共用/バルコニー等)の一覧を取得する。

    Zoneのdetails.categoryAttributeId.guidが指す先の実名(例:"住宅-1")は
    GetDetailsOfElementsのレスポンスには含まれず、別途GetAttributesByType
    (attributeType="ZoneCategory")で解決する必要がある。実データで検証した
    結果、Archicad側の「名前と位置」タブの「カテゴリ」欄がこれに対応する。

    このカテゴリ名はproperties.zone_categoryとして保存する表示用の情報に
    とどめる。実室か、住戸/区画全体を表す大分類ゾーンかの判定には使わない
    ——命名規則(例:番号なしの大分類名+番号付きサブタイプという構成)は
    Archicadのテンプレートによって書き方が様々でありうるため、この判定は
    テンプレートに依存しない幾何包含(graph/envelope.py)を根拠にする。
    """
    payload = await _call(
        "GetAttributesByType", {"attributeType": "ZoneCategory"}, transport=transport
    )
    return payload.get("attributes", [])


async def get_layer_names(transport=None):
    """レイヤー属性(名前・番号)の一覧を取得する。

    GetDetailsOfElementsが返すproperties.layerIndexは番号のみで、レイヤー
    名を含まない。実データを検証した結果、「敷地外_周辺建物」のような
    レイヤーに、日影検討等の参考用に大まかなボリューム(例: 厚み4〜11m
    という現実的でない厚みのSlab)が配置されているケースがあり、これを
    実際の建物本体の要素と区別できずに3Dビュー(フロントエンド)へ
    そのまま表示すると、あたかも建物のスラブが暴走しているように見える
    不具合があった。sync_from_archicad()がこのレイヤー名を解決して
    properties.layer_nameとして保存し、フロントエンドが「敷地外」等の
    参考レイヤーを区別して表示できるようにする。
    """
    payload = await _call(
        "GetAttributesByType", {"attributeType": "Layer"}, transport=transport
    )
    return payload.get("attributes", [])


async def list_properties(transport=None):
    payload = await _call("GetAllProperties", transport=transport)
    return payload.get("properties", [])


async def get_property_values(guids, property_guids, transport=None):
    payload = await _call(
        "GetPropertyValuesOfElements",
        {
            "elements": _element_ids(guids),
            "properties": [
                {"propertyId": {"guid": guid}} for guid in property_guids
            ],
        },
        transport=transport,
    )
    return payload.get("propertyValuesForElements", [])


async def set_property_value(guid, property_guid, value, transport=None):
    return await _call(
        "SetPropertyValuesOfElements",
        {
            "elementPropertyValues": [
                {
                    "elementId": {"guid": guid},
                    "propertyId": {"guid": property_guid},
                    "propertyValue": {"value": str(value)},
                }
            ]
        },
        transport=transport,
    )


# Archicad's JSON/Tapir API expresses all coordinates in meters, while
# this project's geometry model (RELATION_RULES max_distance, the
# synthetic test/demo data in main.py) is in millimeters throughout.
# Confirmed empirically: real Zone polygonCoordinates came back as e.g.
# [0.15, 4.65]..[19.05, 19.35] for an actual building footprint - values
# that only make sense as meters. Every coordinate read from Archicad
# must be converted here, at the ingestion boundary, so nothing
# downstream needs to know which unit a given number originally was in.
_METERS_TO_MM = 1000


def _to_mm(value):
    return value * _METERS_TO_MM


def _z_range_from_bounding_box(bounding_box_item):
    """Get3DBoundingBoxesの1件から高さの範囲(z_min, z_max。mm単位)を取り出す。

    calculate_relations()(graph/relation.py)が平面距離と組み合わせて使う。
    これが無いと、平面上では近くても実際には別の階にある要素同士(例えば
    4階建てビルの各階の同じ位置にある壁)まで隣接/接続と誤判定してしまう。
    """

    if not isinstance(bounding_box_item, dict):
        return {}

    box = bounding_box_item.get("boundingBox3D")

    if box is None:
        return {}

    return {"z_min": _to_mm(box["zMin"]), "z_max": _to_mm(box["zMax"])}


def bounding_box_to_geometry(bounding_box_item):
    """Convert one Get3DBoundingBoxes() array item to our geometry schema.

    Uses the XY footprint of the 3D bounding box as an approximate
    polygon - this is not the element's real (possibly non-rectangular)
    outline. Used only as a fallback by details_to_geometry() for types
    that don't carry a real 2D shape in "details" (see module docstring).
    """

    if not isinstance(bounding_box_item, dict):
        return {"type": "point", "x": 0, "y": 0}

    box = bounding_box_item.get("boundingBox3D")

    if box is None:
        return {"type": "point", "x": 0, "y": 0}

    x_min, y_min = _to_mm(box["xMin"]), _to_mm(box["yMin"])
    x_max, y_max = _to_mm(box["xMax"]), _to_mm(box["yMax"])

    return {
        "type": "polygon",
        "points": [
            [x_min, y_min],
            [x_max, y_min],
            [x_max, y_max],
            [x_min, y_max],
        ],
        **_z_range_from_bounding_box(bounding_box_item),
    }


def _coords_to_points(coords):
    return [[_to_mm(c["x"]), _to_mm(c["y"])] for c in coords]


def details_to_geometry(element_type, details, bounding_box_item=None):
    """Build our geometry schema from Tapir's real per-type "details".

    Falls back to bounding_box_to_geometry() when the type has no direct
    2D outline (most types), or when the expected fields are missing/too
    sparse to form a valid shape (e.g. a degenerate <3-point polygon).
    """

    details = details or {}
    z_range = _z_range_from_bounding_box(bounding_box_item)

    if element_type == "Wall":
        if details.get("geometryType") == "Polygonal":
            outline = details.get("polygonOutline") or []
            if len(outline) >= 3:
                return {"type": "polygon", "points": _coords_to_points(outline), **z_range}
        beg = details.get("begCoordinate")
        end = details.get("endCoordinate")
        if beg is not None and end is not None:
            return {
                "type": "line",
                "points": [
                    [_to_mm(beg["x"]), _to_mm(beg["y"])],
                    [_to_mm(end["x"]), _to_mm(end["y"])],
                ],
                **z_range,
            }

    elif element_type == "Zone":
        coords = details.get("polygonCoordinates") or []
        if len(coords) >= 3:
            return {"type": "polygon", "points": _coords_to_points(coords), **z_range}

    elif element_type == "Mesh":
        # MeshDetails.polygonCoordinates is a Coordinate3D list (has "z"
        # per point, for the terrain's level changes), but _coords_to_points
        # only reads "x"/"y" - the real footprint shape (not axis-aligned
        # bounding-box approximation) is what site.py needs to estimate a
        # road's width/centerline correctly for a non-rectangular Mesh.
        coords = details.get("polygonCoordinates") or []
        if len(coords) >= 3:
            return {"type": "polygon", "points": _coords_to_points(coords), **z_range}

    return bounding_box_to_geometry(bounding_box_item)


def details_to_name(element_type, guid, details):
    """Prefer Archicad's own display name when "details" carries one.

    Only Zone details include a real user-facing name; every other type
    falls back to a synthetic "<Type>_<short guid>" label.
    """

    if element_type == "Zone":
        name = (details or {}).get("name")
        if name:
            return name

    return f"{element_type}_{guid[:8]}"
