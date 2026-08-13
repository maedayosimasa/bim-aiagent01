"""窓(Window)を外部窓/内部窓に分類する。

Archicadの窓要素には「外部/内部」を示す属性が存在しない(実データで確認
済み — ownerElementId・レイヤー名・寸法はあるが、外壁/内壁の区別を直接示す
フラグは無い)。ドアの外部/内部判定(evacuation_engine.pyのfind_exterior_doors、
歩行可能グラフ上の次数1)と同じ考え方を窓にも適用する: Room/Zone-Windowの
"adjacent"関係(窓は通行できないため"connects"ではなく"adjacent"、
relation_rules.py参照)で隣接する部屋の数を数え、

  - 1部屋にのみ隣接する窓 → 外部窓(片側が屋外、またはモデル化されていない
    空間に面していると推定)
  - 2部屋以上に隣接する窓 → 内部窓(部屋間の開口。バルコニー等が別Zoneとして
    モデル化されている場合はそのZoneとの間の窓も含む)
  - どの部屋にも隣接していない窓 → 判定不能(所属Zoneが未同期・距離閾値外等)

ドアの外部判定と同様、あくまでヒューリスティックであり確定的な判定では
ない(例: 外部に面していない行き止まり空間の窓を誤って外部窓と判定しうる、
Room-Window関係は距離ベースの隣接判定でありDoorのownerElementIdベースの
精緻化(graph/door_ownership.py)は適用されていない)。
"""

from ..graph.builder import build_graph
from ..graph.topology import build_topology
from .evidence import EvidenceConfidence, tag as tag_evidence
from .relation_builder import rebuild_connections


def _adjacent_rooms(graph, window):
    return [
        neighbor
        for neighbor in graph.neighbors(window)
        if graph.nodes[neighbor].get("type") in ("Room", "Zone")
        and graph.edges[window, neighbor].get("relation") == "adjacent"
    ]


def classify_windows():
    rebuild_connections()

    graph = build_graph()
    graph = build_topology(graph)

    windows = []

    for node, data in graph.nodes(data=True):
        if data.get("type") != "Window":
            continue

        adjacent_rooms = _adjacent_rooms(graph, node)
        room_count = len(adjacent_rooms)

        if room_count == 1:
            classification = "exterior"
        elif room_count >= 2:
            classification = "interior"
        else:
            classification = "unknown"

        windows.append({
            "window_guid": node,
            "window_name": data.get("name"),
            "classification": classification,
            "adjacent_rooms": [
                {"guid": guid, "name": graph.nodes[guid].get("name")}
                for guid in adjacent_rooms
            ],
        })

    counts = {"exterior": 0, "interior": 0, "unknown": 0}
    for window in windows:
        counts[window["classification"]] += 1

    windows = tag_evidence(windows, EvidenceConfidence.HEURISTIC)

    return {
        "disclaimer": (
            "参考値です。Archicadの窓要素には外部/内部を示す属性が無いため、"
            "隣接する部屋数(Room/Zone-Windowのadjacent関係)から推定しています。"
            "1部屋にのみ隣接する窓を外部窓、2部屋以上に隣接する窓を内部窓と"
            "みなしています。外部に面していない行き止まり空間の窓を誤って"
            "外部窓と判定する等、確定的な判定ではありません。"
        ),
        "total": len(windows),
        "exterior_count": counts["exterior"],
        "interior_count": counts["interior"],
        "unknown_count": counts["unknown"],
        "windows": windows,
    }
