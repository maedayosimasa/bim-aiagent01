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


def _matches_keyword(element, keyword):
    if keyword in (element["name"] or ""):
        return True

    if element["type"] != "Mesh":
        return False

    try:
        properties = json.loads(element["properties"] or "{}")
    except (TypeError, json.JSONDecodeError):
        return False

    for value in (properties.get("archicad_id"), properties.get("layer_name")):
        if value and keyword in value:
            return True

    return False


def find_zones_by_name(keyword):
    """keywordに一致するZone(Room)/Meshを実データ(SQLiteキャッシュ)から検索する。

    Zone/Roomはelement["name"](Archicadの実名)で、Meshは
    properties.archicad_id(Archicad UIの「IDとカテゴリ」→「ID」欄)または
    properties.layer_nameで照合する(Meshに"name"が無いため)。
    複数件ヒットする場合(角地で道路が2本あるなど)も想定し、常にリストで返す。
    """

    zones = [e for e in get_elements() if e["type"] in ("Zone", "Room", "Mesh")]

    matches = []
    for zone in zones:
        if not _matches_keyword(zone, keyword):
            continue
        summary = _zone_summary(zone)
        if summary is not None:
            matches.append(summary)

    return matches


def get_site_boundary():
    """名前に"敷地"を含むZoneを敷地境界線として返す。"""

    return find_zones_by_name("敷地")


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
