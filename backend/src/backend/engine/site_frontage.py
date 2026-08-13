"""接道長さ(建築基準法43条の接道義務)の参考値チェック。

敷地境界線Zoneと前面道路Zoneの共有境界の長さを幾何的に計算する。法定基準は
原則として敷地が幅員4m以上の道路に2m以上接することを要求する(43条1項、
角地緩和等の例外は未実装)。閾値は法令由来の固定値(2.0m)のため、
`daylighting_ratio`/`accessible_door_width`と同じ固定閾値パターンを使う
(容積率のようなプロジェクト単位の外部入力は不要)。
"""

from shapely.geometry import Polygon

from .site import get_road_boundaries, get_site_boundary

_MM_PER_M = 1_000

# 敷地Zoneと道路Zoneの境界線がモデリング上わずかに離れている場合でも
# 「接している」とみなすための許容誤差。他モジュールのz-gap許容(150mm)より
# やや広めに取っている(XY平面上の突き合わせはz方向より誤差が出やすいため)。
_FRONTAGE_GAP_TOLERANCE_MM = 300

REFERENCE_MIN_FRONTAGE_M = 2.0
FRONTAGE_DISCLAIMER = (
    "参考値です。敷地境界線Zoneと前面道路Zoneの境界線が幾何的にどれだけ"
    "重なっているかによる近似値であり、道路台帳等の正式な測量値ではありません。"
    "接道義務(建築基準法43条)の角地緩和・自治体条例による付加規定は未実装です。"
    "前面道路がZoneとしてモデル化されていない場合は判定不能です。"
    "法的な適合を保証するものではありません。"
)


def calculate_site_road_frontage() -> list[dict]:
    """敷地(敷地境界線Zone)ごとに、前面道路との接道長さ(m)を返す。

    前面道路Zoneが1件も見つからない場合、measured_valueはNone(判定不能)
    にする——「接道長さ0m」と「そもそも道路が未モデル化」は意味が異なる
    (engine/effective_daylighting.pyと同じ、未解決をFAILにしない設計)。
    複数の道路に接する場合(角地等)は合算する。
    """
    site_zones = get_site_boundary()
    road_zones = get_road_boundaries()

    results = []

    for site in site_zones:
        site_polygon = Polygon(site["points"])

        if not road_zones:
            results.append({
                "target_guid": site["guid"],
                "target_name": site["name"],
                "measured_value": None,
                "evidence": {"road_details": []},
            })
            continue

        road_details = []
        total_frontage_mm = 0.0

        for road in road_zones:
            road_polygon = Polygon(road["points"]).buffer(_FRONTAGE_GAP_TOLERANCE_MM)
            shared = site_polygon.exterior.intersection(road_polygon)
            frontage_mm = shared.length if not shared.is_empty else 0.0
            total_frontage_mm += frontage_mm
            road_details.append({
                "road_guid": road["guid"],
                "road_name": road["name"],
                "frontage_length_m": frontage_mm / _MM_PER_M,
            })

        results.append({
            "target_guid": site["guid"],
            "target_name": site["name"],
            "measured_value": total_frontage_mm / _MM_PER_M,
            "evidence": {"road_details": road_details},
        })

    return results
