"""容積率(建築基準法52条)の参考値チェック。

延べ面積(全floorIndexの実室Zone面積の合計)を敷地面積で除した比率を、
`legal_inputs.py`の`yoseki_ritsu`(都市計画で指定された容積率上限、%表記)と
比較する。閾値自体がプロジェクト単位の外部情報のため、`engine/rule_engine.py`
の`Verification.threshold_from_input`機構(2026-08-13追加)でlegal_inputsから
動的に解決する——`daylighting_ratio`のような固定閾値とは異なる。

前面道路の幅員による容積率の別途制限(道路幅員×法定乗数、建築基準法52条2項)
は未実装(engine/site.pyの道路幅員が幾何近似値のため、この乗数計算は別途
検証が必要)。
"""

from ..graph.builder import build_graph
from ..graph.room import find_rooms
from ..graph.topology import build_topology
from .relation_builder import rebuild_connections
from .room_engine import _room_area_m2
from .site import get_site_boundary, non_room_zone_guids

FLOOR_AREA_RATIO_DISCLAIMER = (
    "参考値です。延べ面積はBIMモデル上の実室Zone面積の合計による近似値であり、"
    "共用廊下・階段等の扱いや壁芯・内法の違いにより、確認申請上の延べ面積とは"
    "一致しない場合があります。前面道路幅員による容積率の別途制限(建築基準法"
    "52条2項)は未実装です。敷地境界線がZoneとしてモデル化されていない場合は"
    "計算できません。法的な適合を保証するものではありません。"
)


def calculate_floor_area_ratio() -> list[dict]:
    """敷地(敷地境界線Zone)ごとに、延べ面積/敷地面積の比率を返す。

    敷地境界線Zoneが見つからない場合は空リストを返す(判定不能)。
    """
    rebuild_connections()

    graph = build_graph()
    graph = build_topology(graph)

    excluded_guids = non_room_zone_guids()
    rooms = find_rooms(graph)

    total_floor_area_m2 = 0.0
    for room in rooms:
        if room in excluded_guids:
            continue
        area = _room_area_m2(graph.nodes[room])
        if area is not None:
            total_floor_area_m2 += area

    site_zones = get_site_boundary()

    return [
        {
            "target_guid": site["guid"],
            "target_name": site["name"],
            "measured_value": (
                total_floor_area_m2 / site["area_m2"] if site["area_m2"] else None
            ),
            "evidence": {
                "total_floor_area_m2": total_floor_area_m2,
                "site_area_m2": site["area_m2"],
            },
        }
        for site in site_zones
    ]
