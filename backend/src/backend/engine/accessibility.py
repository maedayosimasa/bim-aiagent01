"""建物内の動線(部屋間の通行可能性)をトポロジー解析するモジュール。

Room/Zone-Doorの"connects"エッジ(graph/path.pyのwalkable_subgraph())を
使い、既存データのみで完結する。避難経路の最短距離を求める
evacuation_engine.pyとは異なり、こちらは「行き止まりの部屋」「動線上の
要所となるハブ部屋(次数が高く、多くの部屋の通行経路になっている)」を
トポロジーの観点から特定する。
"""

from ..graph.builder import build_graph
from ..graph.topology import build_topology
from ..graph.path import walkable_subgraph
from ..graph.room import find_rooms
from .relation_builder import rebuild_connections


# ハブ部屋とみなす次数のしきい値。この件数以上のドアに接続している
# Room/Zoneを、動線上の要所(共用廊下・ホール等)として報告する。
HUB_DEGREE_THRESHOLD = 4


def analyze_accessibility():
    """各部屋(Room/Zone)の通行次数から、行き止まりとハブ部屋を求める。

    次数0(どのドアにも繋がらない、完全に孤立)はfind_isolated_elements()
    (graph/analyzer.py)側で別途検出されるため、ここでは対象にしない。
    次数1(出入り口が実質1つしかない、行き止まり)を報告する。
    """

    rebuild_connections()

    graph = build_graph()
    graph = build_topology(graph)

    walkable = walkable_subgraph(graph)

    dead_ends = []
    hubs = []

    for room in find_rooms(graph):

        degree = walkable.degree(room) if room in walkable else 0

        if degree == 1:
            dead_ends.append({
                "room_guid": room,
                "room_name": graph.nodes[room].get("name"),
                "degree": degree,
            })

        if degree >= HUB_DEGREE_THRESHOLD:
            hubs.append({
                "room_guid": room,
                "room_name": graph.nodes[room].get("name"),
                "degree": degree,
            })

    hubs.sort(key=lambda hub: -hub["degree"])

    return {
        "hub_degree_threshold": HUB_DEGREE_THRESHOLD,
        "dead_ends": dead_ends,
        "hubs": hubs,
    }
