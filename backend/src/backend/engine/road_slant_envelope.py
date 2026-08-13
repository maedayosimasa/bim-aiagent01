"""道路斜線制限(建築基準法56条1項1号)の envelope(建築可能な高さの上限)を
幾何的に近似する。

法令の規定: 建築物の各部分の高さは、前面道路の反対側の境界線からの水平
距離に、用途地域ごとの勾配(住居系1.25、それ以外1.5)を乗じた値以下と
しなければならない(適用距離の範囲内)。

この実装は敷地境界線Zoneの各頂点について、前面道路Zoneの「敷地から見て
反対側の境界線」までの水平距離を求め、そこに勾配を乗じてその頂点の直上に
許容される高さを算出する。敷地境界線の頂点群に高さを割り当てることで、
Meshとして書き出せる「envelope曲面」を構成する(engine/
height_restriction_write.py参照)。

**既知の限界(結果のdisclaimerにも記載)**:
  - 適用距離(法別表第三、用途地域・容積率により20m〜35m)は一律の固定値
    (_APPLICABLE_DISTANCE_M)を使う。実際の適用距離はプロジェクトごとに
    異なる。
  - 道路の後退緩和(セットバック)・2以上の前面道路がある場合の緩和・
    公園等に面する場合の緩和(56条6項、施行令134条以降)は未実装。
  - 隣地斜線制限・北側斜線制限・天空率による代替緩和は考慮しない(道路
    斜線制限のみを単独で示す)。
  - 前面道路の「反対側の境界線」は、道路Zoneの最小外接矩形の長辺のうち
    敷地から遠い方として幾何的に近似する(engine/site.pyの
    _estimate_width_and_centerline()と同じ近似手法)。道路が非矩形・
    屈曲している場合は不正確になりうる。
  - 複数の道路Zoneがある場合、各頂点についてそれぞれの道路までの距離の
    うち最小値(=最も厳しい制限)を採用する。

**(2026-08-14追加)`calculate_road_slant_compliance()`**: 上記envelope計算と
実際の建物高さ(engine/building_height.py、Wall/Slab/Roof/Columnのz_max)を
比較し、PASS/FAIL判定用の実測値(超過高さ、m)を返す。敷地境界線Zoneの
外周ポリゴン内にある建物要素のうち、その位置での高さ上限を最も超過している
1件を代表値として採用する(floor_area_ratio等と同じsite単位1項目の粒度)。
地盤面(グレードレベル)を明示的に記録する仕組みが無いため、他のenvelope
計算・effective_daylighting.pyのH計算と同じ「敷地Zoneの座標系z=0=地盤面」
という簡略化を踏襲する。
"""

from shapely.geometry import LineString, Point, Polygon

from .building_height import building_height_points
from .effective_daylighting import get_land_use_category
from .site import get_road_boundaries, get_site_boundary

_MM_PER_M = 1_000

# 建築基準法56条1項1号: 住居系用途地域は1.25、それ以外(近隣商業・商業・
# 工業系等)は1.5。LAND_USE_CATEGORY(residential/industrial/commercial)は
# effective_daylighting.pyの採光補正係数選択と共有する既存の環境変数。
_GRADIENTS = {
    "residential": 1.25,
    "industrial": 1.5,
    "commercial": 1.5,
}

# 適用距離(法別表第三)は用途地域・容積率により20m〜35mと幅があるため、
# 最も保守的な値を一律の目安として使う(evacuation_engine.pyの歩行距離
# 制限で「最も厳しい値」を採用したのと同じ考え方)。
_APPLICABLE_DISTANCE_M = 20.0

ROAD_SLANT_ENVELOPE_DISCLAIMER = (
    "参考値です。建築基準法56条1項1号の道路斜線制限を、敷地境界線の各頂点から"
    "前面道路の反対側の境界線までの幾何的な水平距離に用途地域ごとの勾配を"
    "乗じて近似したものです。適用距離は一律20mの固定値を使用しており、"
    "実際の適用距離(法別表第三、用途地域・容積率により20m〜35m)とは異なる"
    "場合があります。セットバック緩和・2以上の道路がある場合の緩和・"
    "公園等に面する場合の緩和(施行令134条以降)、隣地斜線・北側斜線・"
    "天空率による代替緩和は未実装です。前面道路の反対側境界線は道路Zoneの"
    "最小外接矩形による近似です。法的な適合を保証するものではありません。"
)


def _far_edge_line(road_polygon: Polygon, reference_point: Point) -> LineString:
    """道路ポリゴンの最小外接矩形の長辺2本のうち、reference_pointから遠い方を返す。

    engine/site.pyの_estimate_width_and_centerline()と同じ近似手法
    (道路がおおむね矩形の帯として描かれている前提)。
    """
    rect = road_polygon.minimum_rotated_rectangle
    corners = list(rect.exterior.coords)[:-1]
    edges = [LineString([corners[i], corners[(i + 1) % 4]]) for i in range(4)]
    edge_lengths = [edges[i].length for i in range(4)]

    if edge_lengths[0] >= edge_lengths[1]:
        long_edges = (edges[0], edges[2])
    else:
        long_edges = (edges[1], edges[3])

    return max(long_edges, key=lambda edge: edge.distance(reference_point))


def _height_limit_m(point: Point, far_edges: list[LineString], gradient: float) -> float:
    """道路斜線制限による、pointの直上に許容される高さ上限(m)を返す。

    calculate_road_slant_envelope()の頂点ループと
    calculate_road_slant_compliance()の両方から使う共通ロジック。
    """
    distance_m = min(edge.distance(point) for edge in far_edges) / _MM_PER_M
    return min(gradient * distance_m, gradient * _APPLICABLE_DISTANCE_M)


def calculate_road_slant_envelope(land_use_category: str | None = None) -> list[dict]:
    """敷地(敷地境界線Zone)ごとに、道路斜線制限のenvelope頂点群を返す。

    前面道路Zoneが1件も無い場合、resolved=False(判定不能)を返す
    (敷地境界線の頂点はあるがenvelope自体は構成できない)。
    """
    land_use_category = land_use_category or get_land_use_category()

    if land_use_category not in _GRADIENTS:
        raise ValueError(
            f"未知の用途地域カテゴリです: {land_use_category}"
            f"(residential/industrial/commercialのいずれかを指定してください)"
        )

    gradient = _GRADIENTS[land_use_category]

    site_zones = get_site_boundary()
    road_zones = get_road_boundaries()

    results = []

    for site in site_zones:
        if not road_zones:
            results.append({
                "site_guid": site["guid"],
                "site_name": site["name"],
                "resolved": False,
                "land_use_category": land_use_category,
                "gradient": gradient,
                "applicable_distance_m": _APPLICABLE_DISTANCE_M,
                "vertices": [],
            })
            continue

        site_polygon = Polygon(site["points"])
        far_edges = [
            _far_edge_line(Polygon(road["points"]), site_polygon.centroid)
            for road in road_zones
        ]

        vertices = []
        for x, y in site_polygon.exterior.coords[:-1]:
            height_m = _height_limit_m(Point(x, y), far_edges, gradient)
            vertices.append({"x": x, "y": y, "z_m": height_m})

        results.append({
            "site_guid": site["guid"],
            "site_name": site["name"],
            "resolved": True,
            "land_use_category": land_use_category,
            "gradient": gradient,
            "applicable_distance_m": _APPLICABLE_DISTANCE_M,
            "vertices": vertices,
        })

    return results


def calculate_road_slant_compliance(land_use_category: str | None = None) -> list[dict]:
    """敷地(敷地境界線Zone)ごとに、道路斜線制限envelopeと実際の建物高さ
    (Wall/Slab/Roof/Column、engine/building_height.py)を比較したPASS/FAIL用
    実測値を返す。

    敷地の外周ポリゴン内にある建物要素のうち、その位置での高さ上限を最も
    超過している要素(excess_height_m = 実測高さ - その位置での高さ上限)を
    site単位の1件として返す(floor_area_ratio/building_coverage_ratioと
    同じ「site単位1項目」の粒度)。excess_height_mが0以下ならその敷地内の
    全建物要素が道路斜線制限の範囲内(PASS)、0を超えていれば少なくとも
    1箇所で超過(FAIL)。rule_engine.pyのVerification(comparator="lte",
    threshold=0)と組み合わせて使う。

    前面道路Zoneが無い、または敷地内に高さ情報を持つ建物要素が無い場合は
    measured_value=None(UNKNOWN)を返す。
    """
    land_use_category = land_use_category or get_land_use_category()

    if land_use_category not in _GRADIENTS:
        raise ValueError(
            f"未知の用途地域カテゴリです: {land_use_category}"
            f"(residential/industrial/commercialのいずれかを指定してください)"
        )

    gradient = _GRADIENTS[land_use_category]

    site_zones = get_site_boundary()
    road_zones = get_road_boundaries()
    all_points = building_height_points()

    results = []
    for site in site_zones:
        site_polygon = Polygon(site["points"])
        points_in_site = [
            p for p in all_points if site_polygon.contains(Point(p["x"], p["y"]))
        ]

        if not road_zones or not points_in_site:
            results.append({
                "target_guid": site["guid"],
                "target_name": site["name"],
                "measured_value": None,
                "evidence": {
                    "reason": (
                        "前面道路Zoneが無い、または敷地内に高さ情報を持つ"
                        "建物要素(Wall/Slab/Roof/Column)がありません。"
                    ),
                },
            })
            continue

        far_edges = [
            _far_edge_line(Polygon(road["points"]), site_polygon.centroid)
            for road in road_zones
        ]

        worst_point = max(
            points_in_site,
            key=lambda p: p["height_m"] - _height_limit_m(Point(p["x"], p["y"]), far_edges, gradient),
        )
        limit_m = _height_limit_m(Point(worst_point["x"], worst_point["y"]), far_edges, gradient)
        excess_m = worst_point["height_m"] - limit_m

        results.append({
            "target_guid": site["guid"],
            "target_name": site["name"],
            "measured_value": excess_m,
            "evidence": {
                "land_use_category": land_use_category,
                "worst_element_guid": worst_point["guid"],
                "worst_element_name": worst_point["name"],
                "worst_element_height_m": worst_point["height_m"],
                "height_limit_m_at_worst_element": limit_m,
            },
        })

    return results
