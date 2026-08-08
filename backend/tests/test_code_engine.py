import json

import networkx as nx

from backend.engine.code_engine import (
    check_daylighting,
    check_accessible_door_width,
    _window_area_m2,
    _room_window_stats,
)


def test_check_daylighting_computes_ratio(test_db):
    # 10m x 5m = 50m^2の部屋に、幅2m x 高さ1m(=2m^2)の窓を1つ付ける。
    test_db.insert_element(
        "room1", "Room", "居室A",
        json.dumps({}),
        json.dumps({"type": "polygon", "points": [[0, 0], [10000, 0], [10000, 5000], [0, 5000]]}),
    )
    test_db.insert_element(
        "window1", "Window", "窓",
        json.dumps({"archicad_details": {"width": 2, "height": 1}}),
        json.dumps({"type": "point", "x": 0, "y": 2500}),
    )

    result = check_daylighting()
    room = {r["room_guid"]: r for r in result["rooms"]}["room1"]

    assert room["floor_area_m2"] == 50.0
    assert room["window_area_m2"] == 2.0
    assert room["window_count"] == 1
    assert room["ratio"] == 2.0 / 50.0
    assert room["meets_reference_ratio"] is False  # 0.04 < 1/7
    assert result["reference_ratio"] == 1 / 7
    assert "disclaimer" in result and result["disclaimer"]


def test_check_daylighting_room_without_windows(test_db):
    test_db.insert_element(
        "room1", "Room", "窓の無い部屋",
        json.dumps({}),
        json.dumps({"type": "polygon", "points": [[0, 0], [4000, 0], [4000, 3000], [0, 3000]]}),
    )

    result = check_daylighting()
    room = result["rooms"][0]

    assert room["window_count"] == 0
    assert room["window_area_m2"] == 0.0
    assert room["ratio"] == 0.0
    assert room["meets_reference_ratio"] is False


def test_check_accessible_door_width_flags_narrow_door(test_db):
    test_db.insert_element(
        "door_narrow", "Door", "狭いドア",
        json.dumps({"archicad_details": {"width": 0.6}}),
        json.dumps({"type": "point", "x": 0, "y": 0}),
    )
    test_db.insert_element(
        "door_wide", "Door", "広いドア",
        json.dumps({"archicad_details": {"width": 0.9}}),
        json.dumps({"type": "point", "x": 1000, "y": 0}),
    )

    result = check_accessible_door_width()
    by_guid = {d["door_guid"]: d for d in result["doors"]}

    assert by_guid["door_narrow"]["meets_reference_width"] is False
    assert by_guid["door_wide"]["meets_reference_width"] is True
    assert result["reference_min_width_m"] == 0.8
    assert "disclaimer" in result and result["disclaimer"]


def test_check_accessible_door_width_ignores_non_door_elements(test_db):
    test_db.insert_element(
        "wall1", "Wall", "壁",
        json.dumps({}),
        json.dumps({"type": "line", "points": [[0, 0], [1000, 0]]}),
    )
    test_db.insert_element(
        "door1", "Door", "ドア",
        json.dumps({"archicad_details": {"width": 0.9}}),
        json.dumps({"type": "point", "x": 0, "y": 0}),
    )

    result = check_accessible_door_width()

    assert [d["door_guid"] for d in result["doors"]] == ["door1"]


def test_window_area_m2_returns_none_when_width_or_height_missing():
    assert _window_area_m2({}) is None
    assert _window_area_m2({"width": 1}) is None
    assert _window_area_m2({"height": 1}) is None
    assert _window_area_m2({"width": 2, "height": 1}) == 2


# RELATION_RULES上、Room/Zone-Windowは存在すれば常に"adjacent"にしかならず、
# 通常のパイプラインでは到達しない防御的分岐(不正な形でグラフが組み立て
# られた場合の保険)。手組みのグラフで直接検証する。
def _room_with_neighbor(neighbor_type, relation, archicad_details=None):
    graph = nx.Graph()
    graph.add_node("room1", type="Room")
    graph.add_node(
        "neighbor1", type=neighbor_type,
        properties=json.dumps({"archicad_details": archicad_details or {}}),
    )
    graph.add_edge("room1", "neighbor1", relation=relation)
    return graph


def test_room_window_stats_ignores_non_window_neighbor():
    graph = _room_with_neighbor("Door", relation="connects")

    assert _room_window_stats(graph, "room1") == (0.0, 0)


def test_room_window_stats_ignores_window_with_wrong_relation():
    graph = _room_with_neighbor(
        "Window", relation="connects", archicad_details={"width": 1, "height": 1}
    )

    assert _room_window_stats(graph, "room1") == (0.0, 0)


def test_room_window_stats_ignores_window_missing_area_data():
    graph = _room_with_neighbor("Window", relation="adjacent", archicad_details={})

    assert _room_window_stats(graph, "room1") == (0.0, 0)


def test_room_window_stats_sums_multiple_windows():
    graph = nx.Graph()
    graph.add_node("room1", type="Room")
    graph.add_node("w1", type="Window", properties=json.dumps(
        {"archicad_details": {"width": 2, "height": 1}}
    ))
    graph.add_node("w2", type="Window", properties=json.dumps(
        {"archicad_details": {"width": 1, "height": 1}}
    ))
    graph.add_edge("room1", "w1", relation="adjacent")
    graph.add_edge("room1", "w2", relation="adjacent")

    assert _room_window_stats(graph, "room1") == (3.0, 2)
