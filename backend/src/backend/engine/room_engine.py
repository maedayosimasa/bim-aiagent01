import json

from ..graph.builder import build_graph
from ..graph.topology import build_topology
from ..graph.room import find_rooms
from ..graph.geometry import geometry_from_json
from .relation_builder import rebuild_connections


_MM2_PER_M2 = 1_000_000


def _room_area_m2(node_data):
    geometry_json = node_data.get("geometry")

    if not geometry_json:
        return None

    try:
        geom = geometry_from_json(geometry_json)
    except (ValueError, KeyError, TypeError, json.JSONDecodeError):
        return None

    if geom.geom_type != "Polygon":
        return None

    return geom.area / _MM2_PER_M2


def _adjacent_rooms(graph, room):
    return [
        {"guid": neighbor, "name": graph.nodes[neighbor].get("name")}
        for neighbor in graph.neighbors(room)
        if graph.nodes[neighbor].get("type") in ("Room", "Zone")
        and graph.edges[room, neighbor].get("relation") == "adjacent"
    ]


def _rooms_connected_via_door(graph, room):
    connected = []

    for neighbor in graph.neighbors(room):

        if graph.nodes[neighbor].get("type") != "Door":
            continue
        if graph.edges[room, neighbor].get("relation") != "connects":
            continue

        for other_room in graph.neighbors(neighbor):

            if other_room == room:
                continue
            if graph.nodes[other_room].get("type") not in ("Room", "Zone"):
                continue
            if graph.edges[neighbor, other_room].get("relation") != "connects":
                continue

            connected.append({
                "guid": other_room,
                "name": graph.nodes[other_room].get("name"),
                "door_guid": neighbor,
            })

    return connected


def analyze_room_adjacency():
    """全部屋(Room/Zone)ごとに、隣接する部屋(壁共有)とドアで繋がる部屋を求める。

    calculate_relations()が既に計算するRoom/Zone-Room/Zoneの"adjacent"
    (境界共有)とRoom/Zone-Doorの"connects"をそのまま使う(CLAUDE.mdの
    推奨実装順序どおり、既存データのみで完結する)。"adjacent"は壁を
    共有しているだけで通り抜けられるとは限らない点に注意(通行可能性は
    "connects"、graph/path.py参照)。
    """

    rebuild_connections()

    graph = build_graph()
    graph = build_topology(graph)

    rooms = find_rooms(graph)

    results = []

    for room in rooms:
        data = graph.nodes[room]

        results.append({
            "guid": room,
            "name": data.get("name"),
            "area_m2": _room_area_m2(data),
            "adjacent_rooms": _adjacent_rooms(graph, room),
            "connected_rooms": _rooms_connected_via_door(graph, room),
        })

    return results
