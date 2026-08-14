import json

from shapely.geometry import Point, Polygon

from ..database.db import get_elements
from ..graph.geometry import geometry_from_json


# 敷地・道路は、Archicad上では専用の要素タイプが無く、部屋と同じZoneとして
# (用途を表す名前を付けて)登録される運用を前提にしている。Zoneはこの
# プロジェクトで唯一、details_to_geometry()が実境界(polygonCoordinates)を、
# details_to_name()が実名をそのまま保存する要素タイプ(archicad_mcp/
# tapir.pyのモジュールdocstring参照)。Line/PolyLine/Hatch等で敷地境界線・
# 道路中心線を直接描いている場合は、Tapirの GetDetailsOfElements がこれらの
# 型を "Not yet supported element type" として扱うため実際の経路を取得
# できず、Get3DBoundingBoxesによる矩形近似(bounding_box_to_geometry())に
# しかならない。そのため現状はZoneとしてモデリングされている前提でのみ
# 敷地境界線・道路を検出する。
#
# (2026-08-13追加)道路をMeshでモデリングする運用にも対応する。MeshDetails
# にはZoneと違って"name"が無いため(tapir.pyのモジュールdocstring参照)、
# details_to_geometry()はMeshでも実footprint(polygonCoordinates)をそのまま
# 保存できるようになったが、details_to_name()は依然として合成名
# "Mesh_<guid>"しか返せない。そのためMeshの検索キーはZone/Roomと同じ
# element["name"]では機能せず、代わりに以下2つのproperties値を照合する
# (実データ・実際のArchicad UIで確認済み):
#   - properties.archicad_id: GetDetailsOfElementsが型に依らず返す共通の
#     "id"フィールド。Archicad UIの「分類とプロパティ」パネル→「IDとカテゴリ」
#     グループ→「ID」欄そのもの(実データで確認: id="塗膜防水"等)。ユーザーが
#     Meshを選択してこの欄に「前面道路」と入力するだけで設定できる、
#     Mesh向けの主な照合キー。
#   - properties.layer_name: Archicad側の「レイヤー」属性名。ユーザーが
#     archicad_idの代わりにレイヤー名で運用したい場合のフォールバック。
_MM2_PER_M2 = 1_000_000
_MM_PER_M = 1_000


def _zone_summary(element):
    try:
        geom = geometry_from_json(element["geometry"])
    except (ValueError, KeyError, TypeError, json.JSONDecodeError):
        return None

    if geom.geom_type != "Polygon":
        return None

    return {
        "guid": element["guid"],
        "name": element["name"],
        "points": list(geom.exterior.coords),
        "area_m2": geom.area / _MM2_PER_M2,
    }


def matches_zone_keyword(
    element_type: str,
    name: str | None,
    archicad_id: str | None,
    layer_name: str | None,
    keyword: str,
    *,
    exclude_keywords: tuple[str, ...] = (),
) -> bool:
    """Zone/Room/Meshがkeywordに一致するかを判定する(名前ベースの部分一致)。

    find_zones_by_name()(SQLiteキャッシュ済みの要素向け)と
    archicad_mcp/server.pyのsync_from_archicad()(同期中、まだ未挿入の
    要素向け)の両方から呼べるよう、DB行の形に依存しない引数を取る。
    Zone/Roomはname、Meshはarchicad_id/layer_nameで照合する
    (モジュールdocstring参照)。

    exclude_keywordsを指定した場合、keywordが一致したフィールドの値に
    exclude_keywordsのいずれかが含まれていればその一致を無効にする
    (例: keyword="敷地"に対しexclude_keywords=("敷地外",)を指定すると、
    "敷地外_地盤"のような値は「敷地」を含むが実際には「敷地の外側」を
    意味する語であるため除外できる)。
    """

    def _matches(value):
        if not value or keyword not in value:
            return False
        return not any(excl in value for excl in exclude_keywords)

    if _matches(name):
        return True

    if element_type != "Mesh":
        return False

    return _matches(archicad_id) or _matches(layer_name)


def _matches_keyword(element, keyword, exclude_keywords=()):
    try:
        properties = json.loads(element["properties"] or "{}")
    except (TypeError, json.JSONDecodeError):
        properties = {}

    return matches_zone_keyword(
        element["type"], element["name"],
        properties.get("archicad_id"), properties.get("layer_name"),
        keyword, exclude_keywords=exclude_keywords,
    )


def find_zones_by_name(keyword, exclude_keywords=()):
    """keywordに一致するZone(Room)/Meshを実データ(SQLiteキャッシュ)から検索する。

    Zone/Roomはelement["name"](Archicadの実名)で、Meshは
    properties.archicad_id(Archicad UIの「IDとカテゴリ」→「ID」欄)または
    properties.layer_nameで照合する(Meshに"name"が無いため)。
    複数件ヒットする場合(角地で道路が2本あるなど)も想定し、常にリストで返す。
    exclude_keywordsはmatches_zone_keyword()参照。
    """

    zones = [e for e in get_elements() if e["type"] in ("Zone", "Room", "Mesh")]

    matches = []
    for zone in zones:
        if not _matches_keyword(zone, keyword, exclude_keywords):
            continue
        summary = _zone_summary(zone)
        if summary is not None:
            matches.append(summary)

    return matches


# (2026-08-14追加)実データで、地盤モデリング用のMeshが"敷地"キーワードに
# 誤って一致する2パターンを確認した: (1) layer_name「敷地外_地盤」の
# "敷地外"(=敷地の外側)という否定的な複合語、(2) archicad_id「周辺敷地」の
# "周辺敷地"(=隣接・周辺の別敷地であって当該敷地そのものではない)という
# 修飾語。いずれも部分一致では検出できない「意味の反転・限定」のため、
# 既知のパターンとして明示的に除外する。
_SITE_KEYWORD_EXCLUSIONS = ("敷地外", "周辺敷地")


def get_site_boundary():
    """名前に"敷地"を含むZone/Meshを敷地境界線として返す。

    "敷地外"・"周辺敷地"を含む場合は除外する(_SITE_KEYWORD_EXCLUSIONS参照)。
    """

    return find_zones_by_name("敷地", exclude_keywords=_SITE_KEYWORD_EXCLUSIONS)


def is_site_marker_element(
    element_type: str, name: str | None, archicad_id: str | None, layer_name: str | None
) -> bool:
    """要素が「敷地の目印」(敷地境界線Zone、または敷地であることを示す
    識別子を持つ任意の型の要素)かどうかを判定する。

    get_site_boundary()/find_zones_by_name()(敷地境界線の**幾何取得**用、
    archicad_id/layer_nameの照合をMesh限定にしている)とは異なり、型を
    問わずname/archicad_id/layer_nameのいずれかで判定する
    (archicad_mcp/server.pyのsync_from_archicad()が、法条件カスタム
    プロパティを取得すべき対象を決める用途で使う。実データでObject型
    要素にも敷地の目印(archicad_id="敷地")が付けられているケースを
    確認済みで、この用途では幾何形状の信頼性を問わないため型を問わず
    広く拾う)。"敷地外"・"周辺敷地"を含む場合はget_site_boundary()と
    同様に除外する(_SITE_KEYWORD_EXCLUSIONS参照。2026-08-14修正、
    以前はこの除外が無く、地盤モデリング用のMesh(archicad_id="周辺敷地"
    等)が敷地の目印と誤認識され、法条件プロパティ(建蔽率等)がその
    Meshに対して取得・保存されてしまっていた)。
    """

    def _matches(value):
        if not value or "敷地" not in value:
            return False
        return not any(excl in value for excl in _SITE_KEYWORD_EXCLUSIONS)

    return _matches(name) or _matches(archicad_id) or _matches(layer_name)


def _estimate_width_and_centerline(points):
    """帯状(おおよそ矩形)のポリゴンから、幅員と中心線を幾何的に推定する。

    最小外接矩形(shapelyのminimum_rotated_rectangle)を取り、短辺の長さを
    幅員、2本の短辺それぞれの中点を結んだ線分を中心線とする。道路が
    Archicad上でおおむね矩形の帯として描かれている前提の近似値であり、
    実測値・設計値そのものではない。
    """

    polygon = Polygon(points)
    rect = polygon.minimum_rotated_rectangle

    if rect.geom_type != "Polygon":
        # 面積が無い(退化した)ジオメトリ
        return {"estimated_width_m": None, "estimated_centerline": None}

    corners = list(rect.exterior.coords)[:-1]
    edges = [(corners[i], corners[(i + 1) % 4]) for i in range(4)]
    edge_lengths = [Point(a).distance(Point(b)) for a, b in edges]

    # 矩形なので対辺(0,2)と(1,3)がそれぞれ等しい長さを持つ。短い方が幅員方向。
    if edge_lengths[0] <= edge_lengths[1]:
        width_edges, width_mm = (edges[0], edges[2]), edge_lengths[0]
    else:
        width_edges, width_mm = (edges[1], edges[3]), edge_lengths[1]

    def midpoint(edge):
        (x1, y1), (x2, y2) = edge
        return [(x1 + x2) / 2, (y1 + y2) / 2]

    return {
        "estimated_width_m": width_mm / _MM_PER_M,
        "estimated_centerline": [midpoint(width_edges[0]), midpoint(width_edges[1])],
    }


def get_road_boundaries():
    """名前に"道路"を含むZoneごとに、境界・推定幅員・推定中心線を返す。"""

    roads = find_zones_by_name("道路")

    for road in roads:
        road.update(_estimate_width_and_centerline(road["points"]))

    return roads


def non_room_zone_guids() -> set[str]:
    """敷地境界線・前面道路として検索されるZone/Meshのguid集合を返す。

    graph.room.find_rooms()はこれらを除外しないため(命名ベースの検索は
    room.pyの幾何包含判定とは別の仕組みのため)、部屋として扱ってはいけない
    箇所(採光計算・容積率計算等)で明示的に除外するために使う
    (engine/effective_daylighting.pyで最初に発覚した問題への対応と同じ)。
    """
    return {z["guid"] for z in get_site_boundary()} | {z["guid"] for z in get_road_boundaries()}
