import json

from ..graph.builder import build_graph
from ..graph.topology import build_topology
from ..graph.room import find_rooms
from .relation_builder import rebuild_connections
from .room_engine import _room_area_m2


# 建築基準法の採光有効面積比(居室の床面積に対する有効採光面積の割合)は
# 用途地域・室用途によって基準が異なる(住宅の居室は原則1/7、緩和規定で
# 引き下げられる場合もある)。ここでは最も一般的に引用される1/7を参考値
# としてのみ用いる。実際の法定計算は採光補正係数等を用いるため、この値
# (単純な窓面積/床面積比)とは異なる。CLAUDE.md「④ code_engine」参照。
REFERENCE_DAYLIGHTING_RATIO = 1 / 7
DAYLIGHTING_DISCLAIMER = (
    "参考値です。建築基準法の有効採光面積は採光補正係数等を用いた法定計算が"
    "必要で、この値は窓面積/床面積の単純比率による目安に過ぎません。"
    "法的な適合を保証するものではありません。"
)

# バリアフリー関連の基準でよく引用される、車椅子通行に配慮した最小有効幅の
# 目安。実際の基準は建物用途・自治体条例により異なる。
REFERENCE_MIN_ACCESSIBLE_DOOR_WIDTH_M = 0.8
DOOR_WIDTH_DISCLAIMER = (
    "参考値です。バリアフリーに関する最小ドア幅の基準は建物用途・自治体"
    "条例により異なります。法的な適合を保証するものではありません。"
)


def _window_area_m2(archicad_details):
    width = (archicad_details or {}).get("width")
    height = (archicad_details or {}).get("height")

    if width is None or height is None:
        return None

    return width * height


def _node_archicad_details(graph, node):
    properties = json.loads(graph.nodes[node].get("properties") or "{}")

    return properties.get("archicad_details") or {}


def _room_window_stats(graph, room):
    """部屋(room)に付随する窓の合計面積(m2)と件数を求める。"""

    window_area_m2 = 0.0
    window_count = 0

    for neighbor in graph.neighbors(room):

        if graph.nodes[neighbor].get("type") != "Window":
            continue
        if graph.edges[room, neighbor].get("relation") != "adjacent":
            continue

        area = _window_area_m2(_node_archicad_details(graph, neighbor))

        if area is None:
            continue

        window_area_m2 += area
        window_count += 1

    return window_area_m2, window_count


def check_daylighting():
    """各部屋(Room/Zone)の窓面積/床面積比を、採光有効面積の参考値として算出する。

    Room/Zone-Windowの"adjacent"関係(relation_rules.py。窓は通行できない
    ため"connects"ではなく"adjacent"扱い)を使って、部屋に付随する窓を
    集計する。窓の幅・高さはproperties.archicad_details(Archicad由来、
    単位m)から取得する。
    """

    rebuild_connections()

    graph = build_graph()
    graph = build_topology(graph)

    rooms = find_rooms(graph)

    results = []

    for room in rooms:
        data = graph.nodes[room]
        floor_area_m2 = _room_area_m2(data)

        window_area_m2, window_count = _room_window_stats(graph, room)

        ratio = (
            window_area_m2 / floor_area_m2
            if floor_area_m2
            else None
        )

        results.append({
            "room_guid": room,
            "room_name": data.get("name"),
            "floor_area_m2": floor_area_m2,
            "window_area_m2": round(window_area_m2, 3),
            "window_count": window_count,
            "ratio": ratio,
            "meets_reference_ratio": (
                ratio >= REFERENCE_DAYLIGHTING_RATIO if ratio is not None else None
            ),
        })

    return {
        "reference_ratio": REFERENCE_DAYLIGHTING_RATIO,
        "disclaimer": DAYLIGHTING_DISCLAIMER,
        "rooms": results,
    }


def check_accessible_door_width():
    """各ドアの幅員が、バリアフリーの参考最小幅を満たすかを判定する。"""

    graph = build_graph()

    results = []

    for node, data in graph.nodes(data=True):

        if data.get("type") != "Door":
            continue

        width_m = _node_archicad_details(graph, node).get("width")

        results.append({
            "door_guid": node,
            "door_name": data.get("name"),
            "width_m": width_m,
            "meets_reference_width": (
                width_m >= REFERENCE_MIN_ACCESSIBLE_DOOR_WIDTH_M
                if width_m is not None
                else None
            ),
        })

    return {
        "reference_min_width_m": REFERENCE_MIN_ACCESSIBLE_DOOR_WIDTH_M,
        "disclaimer": DOOR_WIDTH_DISCLAIMER,
        "doors": results,
    }
