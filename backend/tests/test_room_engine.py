import json

import networkx as nx

from backend.engine.room_engine import (
    analyze_room_adjacency,
    _room_area_m2,
    _rooms_connected_via_door,
)


def test_analyze_room_adjacency_distinguishes_wall_shared_and_door_connected(test_db):
    # room1-room2はx=4000の辺を共有(壁共有=adjacent)。
    # room2-room3は300mm離れているため直接adjacentにはならず(閾値200mm)、
    # 間にあるdoor1経由でのみ"connects"される。
    test_db.insert_element(
        "room1", "Room", "居室A",
        json.dumps({}),
        json.dumps({"type": "polygon", "points": [[0, 0], [4000, 0], [4000, 3000], [0, 3000]]}),
    )
    test_db.insert_element(
        "room2", "Room", "居室B",
        json.dumps({}),
        json.dumps({"type": "polygon", "points": [[4000, 0], [8000, 0], [8000, 3000], [4000, 3000]]}),
    )
    test_db.insert_element(
        "room3", "Room", "居室C",
        json.dumps({}),
        json.dumps({"type": "polygon", "points": [[8300, 0], [12300, 0], [12300, 3000], [8300, 3000]]}),
    )
    test_db.insert_element(
        "door1", "Door", "ドア",
        json.dumps({}),
        json.dumps({"type": "point", "x": 8150, "y": 1500}),
    )

    results = {r["guid"]: r for r in analyze_room_adjacency()}

    assert {a["guid"] for a in results["room1"]["adjacent_rooms"]} == {"room2"}
    assert results["room1"]["connected_rooms"] == []

    assert {a["guid"] for a in results["room2"]["adjacent_rooms"]} == {"room1"}
    assert {c["guid"] for c in results["room2"]["connected_rooms"]} == {"room3"}

    assert results["room3"]["adjacent_rooms"] == []
    assert {c["guid"] for c in results["room3"]["connected_rooms"]} == {"room2"}

    assert results["room1"]["area_m2"] == 12.0


def test_analyze_room_adjacency_empty_when_no_rooms(test_db):
    assert analyze_room_adjacency() == []


def test_room_area_m2_missing_geometry_returns_none():
    assert _room_area_m2({"geometry": None}) is None
    assert _room_area_m2({}) is None


def test_room_area_m2_invalid_geometry_json_returns_none():
    # 破損したジオメトリ文字列(実データでの座標欠損等を想定)。
    assert _room_area_m2({"geometry": "not valid json"}) is None


def test_room_area_m2_non_polygon_geometry_returns_none():
    # 部屋(Room/Zone)のジオメトリは通常polygonだが、防御的に他の形状も扱う。
    geometry = json.dumps({"type": "point", "x": 0, "y": 0})
    assert _room_area_m2({"geometry": geometry}) is None


# 以下は RELATION_RULES 上 Room/Zone-Door は常に"connects"にしかならず、
# 通常のDB→calculate_relations()経由のパイプラインでは到達しない防御的
# 分岐(不正な形でグラフが組み立てられた場合の保険)。手組みのグラフで
# 直接検証する。
def _door_graph(door_room_relation="connects", door_other_type="Room",
                 door_other_relation="connects"):
    graph = nx.Graph()
    graph.add_node("room1", type="Room", name="居室A")
    graph.add_node("door1", type="Door", name="ドア")
    graph.add_node("other1", type=door_other_type, name="その他")
    graph.add_edge("room1", "door1", relation=door_room_relation)
    graph.add_edge("door1", "other1", relation=door_other_relation)
    return graph


def test_rooms_connected_via_door_ignores_non_connects_room_door_edge():
    graph = _door_graph(door_room_relation="adjacent")

    assert _rooms_connected_via_door(graph, "room1") == []


def test_rooms_connected_via_door_ignores_non_room_neighbor_of_door():
    graph = _door_graph(door_other_type="Wall")

    assert _rooms_connected_via_door(graph, "room1") == []


def test_rooms_connected_via_door_ignores_non_connects_door_other_edge():
    graph = _door_graph(door_other_relation="adjacent")

    assert _rooms_connected_via_door(graph, "room1") == []
