"""建築基準法施行令第20条に基づく、採光補正係数を用いた有効採光面積を算出する。

`code_engine.py`の`check_daylighting()`(窓面積/床面積の単純比、参考値)とは
別物。こちらは実際の法定計算式(legal_search_tool/legal_article_toolで
現行条文を確認済み、建築基準法施行令第20条第1項・第2項):

    有効採光面積 = Σ(各開口部の面積 × 採光補正係数)
    採光補正係数 = 採光関係比率(D/H) × 用途地域ごとの係数 − 定数
      住居系: ×6.0 − 1.4  /  工業系: ×8.0 − 1.0  /  商業系・無指定: ×10.0 − 1.0
      (算定値の上限は3.0、負の場合は0とする)

D(水平距離)・H(垂直距離)は以下のように幾何的に求める:

  - D: 開口部から敷地境界線までの水平距離。開口部の所属壁
    (properties.archicad_details.ownerElementId、graph/door_ownership.py)
    の法線方向のうち部屋の外側を向く方向へレイを飛ばし
    (find_opening_outward_direction())、「敷地境界線」という名前のZone
    (engine/site.py)の境界との交点までの距離を測る。開口部が「前面道路」
    という名前のZoneに面している場合は、法令の規定通り道路の反対側の
    境界線までの距離とする(道路を挟んだ先まで距離を伸ばす、緩和規定)。
  - H: 開口部の直上にある建築物の各部分(Slab/Roof)から開口部中心までの
    垂直距離。開口部のXY位置を含み、開口部より上のz座標を持つSlab/Roofの
    うち最も低いz_minを「直上の建築物部分」とみなす。直上に何も見つから
    ない場合(屋上に面する開口部等)は、障害物が無い最も有利なケースとして
    採光補正係数の上限(3.0)を採用する。

**既知の限界(結果のdisclaimerにも記載)**:
  - 敷地境界線・前面道路がZoneとしてモデル化されていない建物では、この
    計算は一切できない(D不明→当該開口部は未解決扱い、engine/site.pyと
    同じ制約)。
  - 開口部の外側方向(法線)は、所属壁の両プローブ点のうちどちらが部屋の
    外側かで判定するため、部屋の角に近い開口部等では判定できないことがある。
  - 同一敷地内の隣接建物(自分の敷地内の別棟)・隣地の実際の建物高さは
    考慮していない(単一棟のBIMモデルという前提、隣地境界線までの距離Dの
    みで判定する簡略化)。
  - 天窓(算定値×3)・縁側等を通す開口部(×0.7)の補正、および水平距離が
    7m/5m/4m以上のときの算定値1.0未満を1.0とみなす緩和規定は未実装。
  - 用途地域はBIMデータから判定できないため、環境変数LAND_USE_CATEGORY
    (residential/industrial/commercial、既定residential)で明示的に指定する
    (get_land_use_category())。
  - Room/Zone-Windowの隣接判定は距離ベースのみで、ドアのようなowner壁ベース
    の精緻化(graph/door_ownership.py)が適用されていない(engine/
    window_classifier.pyと同じ既知の限界)。実データでPS/MB/EV等の小面積
    ゾーンに大面積の窓が誤って帰属し、比率が極端な値(5〜58等)になる
    ケースが確認されている。居室系(LD/洋室等)の結果を見る際は、この
    誤帰属の影響を受けにくい(隣接候補が少ない)ため相対的に信頼できるが、
    小面積・密集ゾーンの結果は割り引いて解釈すること。

**(2026-08-11実データで発見・同日修正)当初、部屋の内外判定に大分類ゾーン
(「Aタイプ」等、住戸全体を表すZone、graph/envelope.py参照)を含めてしまい、
実データで対象143室中62室が未解決(D計算不能)になっていた。** 大分類
ゾーンは実室を丸ごと包含するため、開口部の両プローブ点が両方とも「部屋の
内側」と誤判定され、外側方向を特定できなくなっていた。find_rooms()の
結果(既に大分類ゾーン除外済み)に絞り込むことで解消し、未解決16室まで
改善した。

**(2026-08-13検討・見送り)建築基準法第28条の採光義務は「居室」(第2条4号)が
対象であり、廊下・便所・PS・EV等はそもそも対象外のため、Zone名から居室
該当性を推定して除外する`room_classifier.py`を一度実装したが、「名前ベースの
推定であり確定的な法的判断ではない」ことがそのまま誤判断を招く原因になる
というユーザー判断により削除した。居室以外(廊下・便所・PS・EV等)も
含めた全Room/Zoneがそのままチェック対象になる状態に戻している。**

この計算結果は、上記の前提が成立する場合の参考値であり、確定的な法適合
判定ではない。
"""

import json

from shapely.geometry import LineString, Point, Polygon

from ..graph.builder import build_graph
from ..graph.door_ownership import find_opening_outward_direction
from ..graph.geometry import parse_geometry
from ..graph.room import find_rooms
from ..graph.topology import build_topology
from ..database.db import get_elements
from .code_engine import _node_archicad_details, _window_area_m2
from .legal_inputs import normalize_land_use_category, resolve_legal_input
from .relation_builder import rebuild_connections
from .room_engine import _room_area_m2
from .site import get_road_boundaries, get_site_boundary

_MAX_RAY_LENGTH_MM = 100_000  # 100m。実データの敷地規模に対して十分大きい
_OVERHANG_ELEMENT_TYPES = ("Slab", "Roof")

# 建築基準法施行令第20条第2項各号。(係数, 定数)の組。
_LAND_USE_COEFFICIENTS = {
    "residential": (6.0, 1.4),
    "industrial": (8.0, 1.0),
    "commercial": (10.0, 1.0),
}


def get_land_use_category() -> str:
    """用途地域カテゴリ(residential/industrial/commercial)を取得する(既定residential)。

    実際の用途地域はBIMデータのどこにも記録されていない外部の都市計画情報
    のため、機械的に推測せずユーザーに明示的に指定してもらう。値は
    legal_inputs.resolve_legal_input("land_use_category")で解決する
    ((1)敷地ZoneのArchicadカスタムProperty「用途地域」、(2)環境変数
    LAND_USE_CATEGORYの順)。値が正式な用途地域名(例:「第一種住居地域」)
    の場合はnormalize_land_use_category()が3分類へ変換する
    (2026-08-14追加、以前は環境変数のみを直接読んでいた)。
    """
    raw = resolve_legal_input("land_use_category") or "residential"
    category, _kubun = normalize_land_use_category(raw)
    return category


def _daylighting_coefficient(ratio: float, land_use_category: str) -> float:
    multiplier, subtract = _LAND_USE_COEFFICIENTS[land_use_category]
    value = ratio * multiplier - subtract

    if value > 3.0:
        return 3.0
    if value < 0:
        return 0.0
    return value


def _room_polygons(graph):
    records = []

    for node, data in graph.nodes(data=True):
        if data.get("type") not in ("Room", "Zone"):
            continue
        try:
            geom, _ = parse_geometry(data["geometry"])
        except (ValueError, KeyError, TypeError, json.JSONDecodeError):
            continue
        if geom.geom_type == "Polygon":
            records.append((node, geom))

    return records


def _nearest_ray_intersection(origin: Point, direction, polygon: Polygon):
    dx, dy = direction
    far_point = Point(origin.x + dx * _MAX_RAY_LENGTH_MM, origin.y + dy * _MAX_RAY_LENGTH_MM)
    ray = LineString([origin, far_point])

    intersection = ray.intersection(polygon.exterior)

    if intersection.is_empty:
        return None
    if intersection.geom_type == "Point":
        points = [intersection]
    elif intersection.geom_type == "MultiPoint":
        points = list(intersection.geoms)
    else:
        # レイが境界線と平行に重なる等のレアケースは安全側で判定不能とする
        return None

    return min(point.distance(origin) for point in points)


def _farthest_ray_intersection(origin: Point, direction, polygon: Polygon):
    dx, dy = direction
    far_point = Point(origin.x + dx * _MAX_RAY_LENGTH_MM, origin.y + dy * _MAX_RAY_LENGTH_MM)
    ray = LineString([origin, far_point])

    intersection = ray.intersection(polygon.exterior)

    if intersection.is_empty:
        return None
    if intersection.geom_type == "Point":
        points = [intersection]
    elif intersection.geom_type == "MultiPoint":
        points = list(intersection.geoms)
    else:
        return None

    return max(point.distance(origin) for point in points)


def _compute_horizontal_distance(origin, direction, site_polygons, road_polygons):
    # 道路に面する開口部は、法令の規定通り道路の反対側の境界線までの
    # 距離とする(近い方の境界線ではなく、道路を挟んだ先の境界線)。
    for road_polygon in road_polygons:
        distance = _farthest_ray_intersection(origin, direction, road_polygon)
        if distance is not None:
            return distance

    for site_polygon in site_polygons:
        distance = _nearest_ray_intersection(origin, direction, site_polygon)
        if distance is not None:
            return distance

    return None


def _overhang_candidates(elements):
    candidates = []

    for element in elements:
        if element["type"] not in _OVERHANG_ELEMENT_TYPES:
            continue
        try:
            geom, z_range = parse_geometry(element["geometry"])
        except (ValueError, KeyError, TypeError, json.JSONDecodeError):
            continue
        if z_range is None or geom.geom_type != "Polygon":
            continue
        candidates.append((geom, z_range))

    return candidates


def _overhang_z(window_point: Point, window_z_max: float, candidates) -> float | None:
    """開口部の直上にある建築物部分(Slab/Roof)のうち、最も低いz_min(mm)を返す。

    直上に何も見つからなければNone(=障害物が無い最も有利なケース)。
    """
    z_values = [
        z_min
        for geom, (z_min, _z_max) in candidates
        if z_min > window_z_max and geom.contains(window_point)
    ]

    return min(z_values) if z_values else None


def _resolve_window_coefficient(
    window_point, window_z_range, outward_direction, site_polygons, road_polygons,
    overhang_candidates, land_use_category,
):
    if outward_direction is None or window_z_range is None:
        return None

    horizontal_distance = _compute_horizontal_distance(
        window_point, outward_direction, site_polygons, road_polygons
    )

    if horizontal_distance is None:
        return None

    window_center_z = (window_z_range[0] + window_z_range[1]) / 2
    overhang = _overhang_z(window_point, window_z_range[1], overhang_candidates)

    if overhang is None:
        # 直上に建築物部分が無い=障害物が無い最も有利なケース
        return 3.0

    vertical_distance = overhang - window_center_z

    if vertical_distance <= 0:
        return 3.0

    ratio = horizontal_distance / vertical_distance

    return _daylighting_coefficient(ratio, land_use_category)


def calculate_effective_daylighting(land_use_category: str | None = None) -> dict:
    land_use_category = land_use_category or get_land_use_category()

    if land_use_category not in _LAND_USE_COEFFICIENTS:
        raise ValueError(
            f"未知の用途地域カテゴリです: {land_use_category}"
            f"(residential/industrial/commercialのいずれかを指定してください)"
        )

    rebuild_connections()

    graph = build_graph()
    graph = build_topology(graph)

    elements = get_elements()
    walls_by_guid = {e["guid"]: e for e in elements if e["type"] == "Wall"}
    windows_by_guid = {e["guid"]: e for e in elements if e["type"] == "Window"}
    overhang_candidates = _overhang_candidates(elements)

    site_zones = get_site_boundary()
    road_zones = get_road_boundaries()
    site_polygons = [Polygon(zone["points"]) for zone in site_zones]
    road_polygons = [Polygon(zone["points"]) for zone in road_zones]

    rooms = find_rooms(graph)

    # 開口部の外側方向判定(find_opening_outward_direction()の内外判定)には
    # 「実室」のポリゴンのみを使う。find_rooms()が既に大分類ゾーン(住戸
    # 全体を表す「Aタイプ」等、graph/envelope.py)を除外しているため、その
    # 結果セットに絞り込む——ここを絞らずZone/Room全件を使うと、大分類
    # ゾーンが実室を丸ごと包含してしまい、壁の両プローブ点が両方とも
    # 「内側」と判定されて方向を特定できなくなる(実データ検証で発覚、
    # 143実室のうち16件の大分類ゾーンが原因で大半の窓が未解決になっていた)。
    # 敷地境界線・前面道路のZoneも部屋ではないため同様に除外する(敷地境界線
    # は大分類ゾーンとしても除外され得るが、小規模な建物では大分類ゾーンの
    # 除外条件(3件以上の内包)を満たさないことがあるため、念のため明示的に
    # 除外する)。
    room_guid_set = set(rooms)
    non_room_guids = {zone["guid"] for zone in site_zones} | {zone["guid"] for zone in road_zones}
    room_polygons = [
        (guid, geom) for guid, geom in _room_polygons(graph)
        if guid in room_guid_set and guid not in non_room_guids
    ]

    room_results = []
    excluded_site_or_road_count = 0

    for room in rooms:
        data = graph.nodes[room]

        # 敷地境界線・前面道路のZoneは部屋ではない(上記room_polygonsの
        # 絞り込みと同じ理由)。find_rooms()自体はこれらを除外しないため、
        # ここで明示的に除外しないと「部屋」として採光チェック対象に
        # 紛れ込んでしまう(テストで発覚。room_polygonsは既に除外していたが、
        # room_resultsを作るこのループには適用されていなかった)。
        if room in non_room_guids:
            excluded_site_or_road_count += 1
            continue

        floor_area_m2 = _room_area_m2(data)

        effective_area_m2 = 0.0
        window_count = 0
        unresolved_window_count = 0
        window_details = []

        for neighbor in graph.neighbors(room):
            if graph.nodes[neighbor].get("type") != "Window":
                continue
            if graph.edges[room, neighbor].get("relation") != "adjacent":
                continue

            window_count += 1
            window_element = windows_by_guid.get(neighbor)

            if window_element is None:
                unresolved_window_count += 1
                continue

            try:
                geom, z_range = parse_geometry(window_element["geometry"])
            except (ValueError, KeyError, TypeError, json.JSONDecodeError):
                unresolved_window_count += 1
                continue

            window_point = geom.centroid if geom.geom_type == "Polygon" else geom
            area_m2 = _window_area_m2(_node_archicad_details(graph, neighbor))
            outward_direction = find_opening_outward_direction(
                window_element, walls_by_guid, room_polygons
            )

            coefficient = _resolve_window_coefficient(
                window_point, z_range, outward_direction, site_polygons, road_polygons,
                overhang_candidates, land_use_category,
            )

            if coefficient is None or area_m2 is None:
                unresolved_window_count += 1
                window_details.append({
                    "window_guid": neighbor,
                    "resolved": False,
                    "coefficient": None,
                    "area_m2": area_m2,
                })
                continue

            contribution_m2 = area_m2 * coefficient
            effective_area_m2 += contribution_m2

            window_details.append({
                "window_guid": neighbor,
                "resolved": True,
                "coefficient": round(coefficient, 3),
                "area_m2": area_m2,
                "effective_area_m2": round(contribution_m2, 3),
            })

        ratio = effective_area_m2 / floor_area_m2 if floor_area_m2 else None

        # 未解決の窓がある場合、集計した値は「その窓を0として扱った」下限値
        # でしかない。閾値を下回っている(FAILに見える)場合、本来はもっと
        # 高い可能性があり誤ってFAIL扱いにしてしまう恐れがあるため、その
        # ケースだけは判定不能(None)に倒す(閾値を満たしている場合は、
        # 未解決の窓を足せば実際の値はさらに高くなるだけなのでPASSのまま
        # 確定してよい)。
        if unresolved_window_count > 0 and (ratio is None or ratio < 1 / 7):
            ratio = None

        room_results.append({
            "room_guid": room,
            "room_name": data.get("name"),
            "floor_area_m2": floor_area_m2,
            "effective_window_area_m2": round(effective_area_m2, 3),
            "window_count": window_count,
            "unresolved_window_count": unresolved_window_count,
            "ratio": ratio,
            "window_details": window_details,
        })

    return {
        "land_use_category": land_use_category,
        "disclaimer": (
            "参考値です。建築基準法施行令第20条の採光補正係数を用いた計算ですが、"
            "敷地境界線・前面道路がZoneとしてモデル化されていない場合や、開口部の"
            "所属壁・直上の建築物部分が特定できない場合は計算できません"
            "(unresolved_window_countが1件以上ある部屋は、実際の値がこの結果より"
            "高い可能性があります)。天窓・縁側等の補正係数や緩和規定は未実装です。"
            "用途地域は環境変数LAND_USE_CATEGORYの指定値を使用しており、実際の"
            "用途地域と異なる場合は結果が変わります。法的な適合を保証するもの"
            "ではありません。"
        ),
        "excluded_site_or_road_count": excluded_site_or_road_count,
        "rooms": room_results,
    }
