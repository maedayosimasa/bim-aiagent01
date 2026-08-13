"""隣地斜線制限(建築基準法56条1項2号)のenvelopeを幾何的に近似する。

法令の規定: 建築物の各部分の高さは、立ち上がり高さ(住居系20m、それ以外
31m)に、隣地境界線までの水平距離に用途地域ごとの勾配(住居系1.25、
それ以外2.5)を乗じた値を加えた値以下としなければならない。

道路斜線制限(engine/road_slant_envelope.py)と異なり、隣地斜線制限には
「反対側の境界線」という緩和が無く、隣地境界線そのものまでの距離を測る。
「隣地境界線」は、敷地境界線Zoneの辺のうち前面道路Zoneに接していない辺
として近似する(前面道路が無い場合は敷地の全周を隣地境界線とみなす——
接道が無いこと自体は接道義務(43条)違反の可能性があるが、この計算は
隣地斜線制限のみを対象とし接道義務は判定しない)。

**既知の限界(結果のdisclaimerにも記載)**:
  - 適用距離の上限は無い(法令通り)が、敷地が極端に大きい場合は
    非現実的に高い値になりうる。
  - 天空率による代替緩和、2以上の道路がある場合の緩和は考慮しない。
  - 「前面道路に接する辺」の判定は幾何的な近接(300mm許容誤差)によるため、
    実際の接道状況と異なる場合がある。
  - 道路斜線制限・北側斜線制限・高度地区による別途の制限は考慮しない
    (このenvelopeだけを単独で示す、他のengine/*_slant_envelope.pyと同じ方針)。

**(2026-08-14追加)`calculate_adjacent_boundary_slant_compliance()`**:
engine/road_slant_envelope.pyの`calculate_road_slant_compliance()`と同じ
考え方で、実際の建物高さ(engine/building_height.py)とenvelopeを比較した
PASS/FAIL用実測値を返す。
"""

from shapely.geometry import LineString, Point, Polygon

from .building_height import building_height_points
from .effective_daylighting import get_land_use_category
from .site import get_road_boundaries, get_site_boundary

_MM_PER_M = 1_000

# 建築基準法56条1項2号: 住居系は勾配1.25・立ち上がり20m、それ以外
# (近隣商業・商業・工業系等)は勾配2.5・立ち上がり31m。
_GRADIENTS = {"residential": 1.25, "industrial": 2.5, "commercial": 2.5}
_RISE_HEIGHTS_M = {"residential": 20.0, "industrial": 31.0, "commercial": 31.0}

# 敷地境界線の辺が前面道路Zoneに接しているとみなす許容誤差(mm)。
# engine/site_frontage.pyの_FRONTAGE_GAP_TOLERANCE_MMと同じ考え方。
_ROAD_EDGE_TOLERANCE_MM = 300

ADJACENT_BOUNDARY_SLANT_ENVELOPE_DISCLAIMER = (
    "参考値です。建築基準法56条1項2号の隣地斜線制限を、敷地境界線の各頂点から"
    "隣地境界線(前面道路に接しない敷地の辺として近似)までの幾何的な最短水平"
    "距離に用途地域ごとの勾配・立ち上がり高さを乗じて近似したものです。"
    "適用距離の上限は設けていません(法令通り無制限ですが、敷地形状によっては"
    "非現実的な高さになりうることに注意してください)。天空率による代替緩和・"
    "2以上の道路がある場合の緩和は未実装です。道路斜線制限・北側斜線制限・"
    "高度地区による別途の制限は考慮していません。法的な適合を保証するもの"
    "ではありません。"
)


def _site_edges(site_polygon: Polygon) -> list[LineString]:
    coords = list(site_polygon.exterior.coords)
    return [LineString([coords[i], coords[i + 1]]) for i in range(len(coords) - 1)]


def _is_road_facing(edge: LineString, road_polygons: list[Polygon]) -> bool:
    return any(edge.distance(road) <= _ROAD_EDGE_TOLERANCE_MM for road in road_polygons)


def _height_limit_m(point: Point, adjacent_edges: list[LineString], gradient: float, rise_height_m: float) -> float:
    """隣地斜線制限による、pointの直上に許容される高さ上限(m)を返す。

    calculate_adjacent_boundary_slant_envelope()の頂点ループと
    calculate_adjacent_boundary_slant_compliance()の両方から使う共通ロジック。
    """
    distance_m = min(edge.distance(point) for edge in adjacent_edges) / _MM_PER_M
    return rise_height_m + gradient * distance_m


def calculate_adjacent_boundary_slant_envelope(land_use_category: str | None = None) -> list[dict]:
    """敷地(敷地境界線Zone)ごとに、隣地斜線制限のenvelope頂点群を返す。

    敷地の全周が前面道路に接している(隣地境界線が1つも無い)場合、
    resolved=False(判定不能)を返す。
    """
    land_use_category = land_use_category or get_land_use_category()

    if land_use_category not in _GRADIENTS:
        raise ValueError(
            f"未知の用途地域カテゴリです: {land_use_category}"
            f"(residential/industrial/commercialのいずれかを指定してください)"
        )

    gradient = _GRADIENTS[land_use_category]
    rise_height_m = _RISE_HEIGHTS_M[land_use_category]

    site_zones = get_site_boundary()
    road_polygons = [Polygon(r["points"]) for r in get_road_boundaries()]

    results = []

    for site in site_zones:
        site_polygon = Polygon(site["points"])
        edges = _site_edges(site_polygon)
        adjacent_edges = [e for e in edges if not _is_road_facing(e, road_polygons)]

        if not adjacent_edges:
            results.append({
                "site_guid": site["guid"],
                "site_name": site["name"],
                "resolved": False,
                "land_use_category": land_use_category,
                "gradient": gradient,
                "rise_height_m": rise_height_m,
                "vertices": [],
            })
            continue

        vertices = []
        for x, y in site_polygon.exterior.coords[:-1]:
            height_m = _height_limit_m(Point(x, y), adjacent_edges, gradient, rise_height_m)
            vertices.append({"x": x, "y": y, "z_m": height_m})

        results.append({
            "site_guid": site["guid"],
            "site_name": site["name"],
            "resolved": True,
            "land_use_category": land_use_category,
            "gradient": gradient,
            "rise_height_m": rise_height_m,
            "vertices": vertices,
        })

    return results


def calculate_adjacent_boundary_slant_compliance(land_use_category: str | None = None) -> list[dict]:
    """敷地(敷地境界線Zone)ごとに、隣地斜線制限envelopeと実際の建物高さ
    (Wall/Slab/Roof/Column、engine/building_height.py)を比較したPASS/FAIL用
    実測値(超過高さ、m)を返す。engine/road_slant_envelope.pyの
    `calculate_road_slant_compliance()`と同じ考え方(site単位1項目、
    excess_height_m = 実測高さ - その位置での高さ上限)。

    隣地境界線が1つも無い(敷地の全周が前面道路に接している)、または敷地内
    に高さ情報を持つ建物要素が無い場合はmeasured_value=None(UNKNOWN)を返す。
    """
    land_use_category = land_use_category or get_land_use_category()

    if land_use_category not in _GRADIENTS:
        raise ValueError(
            f"未知の用途地域カテゴリです: {land_use_category}"
            f"(residential/industrial/commercialのいずれかを指定してください)"
        )

    gradient = _GRADIENTS[land_use_category]
    rise_height_m = _RISE_HEIGHTS_M[land_use_category]

    site_zones = get_site_boundary()
    road_polygons = [Polygon(r["points"]) for r in get_road_boundaries()]
    all_points = building_height_points()

    results = []
    for site in site_zones:
        site_polygon = Polygon(site["points"])
        edges = _site_edges(site_polygon)
        adjacent_edges = [e for e in edges if not _is_road_facing(e, road_polygons)]

        points_in_site = [
            p for p in all_points if site_polygon.contains(Point(p["x"], p["y"]))
        ]

        if not adjacent_edges or not points_in_site:
            results.append({
                "target_guid": site["guid"],
                "target_name": site["name"],
                "measured_value": None,
                "evidence": {
                    "reason": (
                        "隣地境界線が無い(敷地の全周が前面道路に接している)、"
                        "または敷地内に高さ情報を持つ建物要素(Wall/Slab/Roof/"
                        "Column)がありません。"
                    ),
                },
            })
            continue

        worst_point = max(
            points_in_site,
            key=lambda p: p["height_m"]
            - _height_limit_m(Point(p["x"], p["y"]), adjacent_edges, gradient, rise_height_m),
        )
        limit_m = _height_limit_m(
            Point(worst_point["x"], worst_point["y"]), adjacent_edges, gradient, rise_height_m
        )
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
