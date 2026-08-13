"""建蔽率(建築基準法53条)の参考値チェック。

建築面積(建物を真上から見た水平投影面積)を敷地面積で除した比率を、
`legal_inputs.py`の`kenpei_ritsu`(都市計画で指定された建蔽率上限、%表記)と
比較する(`engine/rule_engine.py`の`threshold_from_input`機構、
`floor_area_ratio`と同じ)。

建築面積は単純な床面積(Room/Zone)の合計ではない——壁の厚み分の外周リング、
および軒・ひさし・はね出し縁等のオーバーハングを含む必要がある
(建築基準法施行令2条1項2号)。この計算は2段階で構成する:

  1. 建物本体の外郭(base envelope): 全floorIndexのRoom/Zone床面積
     (`engine/site.py`の`non_room_zone_guids()`で敷地・道路Zoneを除外)と、
     全Wallの芯線を半厚み(`graph/door_ownership.py`の
     `_wall_half_thickness_mm()`を再利用)だけ膨らませた帯状ポリゴンを
     unionする。外壁/内壁を区別する情報がArchicadデータに無いため全Wallを
     対象にするが、内壁は既にRoom/Zone面積の内側に収まるため外側境界には
     影響しない(過大評価にはならない)。上階が下階よりせり出す場合、その
     上階のRoom/Zone自体がunionに加わるため、通常の階のオーバーハング
     (バルコニー等の居室面積を伴う張り出し)は自然に反映される。
  2. 軒・ひさし等のオーバーハング: Slab/Roof(`effective_daylighting.py`の
     `_OVERHANG_ELEMENT_TYPES`と同じ要素タイプ、実データでポリゴン形状を
     取得できることを確認済み)のうち、1.の外郭から水平距離1m未満の
     張り出しは建築面積に算入しない(施行令2条1項2号の緩和規定)。実装は
     「外郭を1mバッファした領域」をSlab/Roofのunionから差し引いた残り
     (1mを超えて突き出た部分のみ)を1.に加算する。
"""

import json

from shapely.ops import unary_union

from ..database.db import get_elements
from ..graph.builder import build_graph
from ..graph.door_ownership import _wall_half_thickness_mm
from ..graph.geometry import parse_geometry
from ..graph.room import find_rooms
from ..graph.topology import build_topology
from .relation_builder import rebuild_connections
from .site import get_site_boundary, non_room_zone_guids

_MM2_PER_M2 = 1_000_000

# 施行令2条1項2号: 軒・ひさし等が中心線から水平距離1m以上突き出た場合、
# その端から1m後退した線までは建築面積に算入しない(=1m未満の張り出しは
# 一切算入せず、1m以上の場合も1m分は控除する)。
_EAVES_EXEMPTION_MM = 1_000

# effective_daylighting.pyの_OVERHANG_ELEMENT_TYPESと同じ要素タイプ
# (Slab/Roofは実データでポリゴン形状+高さ範囲を取得できることを確認済み)。
_OVERHANG_ELEMENT_TYPES = ("Slab", "Roof")

BUILDING_COVERAGE_RATIO_DISCLAIMER = (
    "参考値です。建築面積はBIMモデル上のRoom/Zone面積・壁芯+半厚み・"
    "Slab/Roofのオーバーハング(水平距離1m未満の張り出しは施行令2条1項2号"
    "により不算入)から幾何的に近似した値であり、実際の測量値・確認申請上の"
    "建築面積とは一致しない場合があります。外壁/内壁の区別が無いため全Wall"
    "を対象にしており、独立した塀等が誤って含まれる可能性があります。複数棟"
    "の敷地では全棟合計になります(棟ごとの分離は未実装)。角地・防火地域"
    "等の建蔽率緩和規定は未実装です。法的な適合を保証するものではありません。"
)


def _parse_polygon(geometry_json):
    try:
        geom, _ = parse_geometry(geometry_json)
    except (ValueError, KeyError, TypeError, json.JSONDecodeError):
        return None
    return geom if geom.geom_type == "Polygon" else None


def _parse_line(geometry_json):
    try:
        geom, _ = parse_geometry(geometry_json)
    except (ValueError, KeyError, TypeError, json.JSONDecodeError):
        return None
    return geom if geom.geom_type == "LineString" else None


def _wall_footprint_polygons(elements):
    polygons = []
    for element in elements:
        if element["type"] != "Wall":
            continue
        centerline = _parse_line(element["geometry"])
        if centerline is None:
            continue
        half_thickness_mm = _wall_half_thickness_mm(element)
        polygons.append(centerline.buffer(half_thickness_mm))
    return polygons


def _overhang_polygons(elements):
    polygons = []
    for element in elements:
        if element["type"] not in _OVERHANG_ELEMENT_TYPES:
            continue
        polygon = _parse_polygon(element["geometry"])
        if polygon is not None:
            polygons.append(polygon)
    return polygons


def calculate_building_area_m2() -> float | None:
    """建物全体(全floorIndex合算)の建築面積(m2)を返す。

    Room/Zone・Wallが1件も無い場合はNone(算出不能)を返す。
    """
    rebuild_connections()

    graph = build_graph()
    graph = build_topology(graph)

    elements = get_elements()
    excluded_guids = non_room_zone_guids()
    rooms = find_rooms(graph)

    room_polygons = []
    for room in rooms:
        if room in excluded_guids:
            continue
        polygon = _parse_polygon(graph.nodes[room].get("geometry"))
        if polygon is not None:
            room_polygons.append(polygon)

    wall_polygons = _wall_footprint_polygons(elements)

    base_parts = room_polygons + wall_polygons
    if not base_parts:
        return None

    base_envelope = unary_union(base_parts)
    if base_envelope.is_empty:
        return None

    overhang_polygons = _overhang_polygons(elements)
    counted_overhang_area_mm2 = 0.0

    if overhang_polygons:
        exemption_zone = base_envelope.buffer(_EAVES_EXEMPTION_MM)
        counted_overhang = unary_union(overhang_polygons).difference(exemption_zone)
        # exemption_zoneはbase_envelopeを内包するため、counted_overhangは
        # base_envelopeと重ならない(単純加算でよい)。
        counted_overhang_area_mm2 = counted_overhang.area

    return (base_envelope.area + counted_overhang_area_mm2) / _MM2_PER_M2


def calculate_building_coverage_ratio() -> list[dict]:
    """敷地(敷地境界線Zone)ごとに、建築面積/敷地面積の比率を返す。

    敷地境界線Zoneが見つからない場合は空リストを返す(判定不能)。
    """
    building_area_m2 = calculate_building_area_m2()
    site_zones = get_site_boundary()

    return [
        {
            "target_guid": site["guid"],
            "target_name": site["name"],
            "measured_value": (
                building_area_m2 / site["area_m2"]
                if building_area_m2 is not None and site["area_m2"]
                else None
            ),
            "evidence": {
                "building_area_m2": building_area_m2,
                "site_area_m2": site["area_m2"],
            },
        }
        for site in site_zones
    ]
