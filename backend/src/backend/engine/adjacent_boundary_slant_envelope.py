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
"""

from shapely.geometry import LineString, Point, Polygon

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
            point = Point(x, y)
            distance_m = min(edge.distance(point) for edge in adjacent_edges) / _MM_PER_M
            height_m = rise_height_m + gradient * distance_m
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
