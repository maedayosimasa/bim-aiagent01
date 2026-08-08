import json

from backend.graph.builder import build_graph
from backend.graph.room import find_rooms
from backend.graph.search import find_nodes_by_type


def test_find_rooms(sample_elements):
    graph = build_graph()

    assert set(find_rooms(graph)) == {"room001", "room002"}


def test_find_rooms_includes_zone_type(test_db):
    # Archicadの実データは部屋を"Room"ではなく"Zone"と呼ぶため、
    # find_roomsは両方を部屋として扱わなければならない。
    test_db.insert_element(
        "zone001", "Zone", "ゾーンA",
        json.dumps({}),
        json.dumps({"type": "polygon", "points": [[0, 0], [1000, 0], [1000, 1000], [0, 1000]]}),
    )

    graph = build_graph()

    assert set(find_rooms(graph)) == {"zone001"}


def test_find_rooms_excludes_envelope_zones(test_db):
    # 住戸/区画全体を表す大分類ゾーン("Cタイプ"等、properties.zone_is_
    # envelope=True)は実室ではないため除外する(実データ検証で発覚:
    # archicad_mcp/tapir.pyのenvelope_zone_category_names()参照)。
    test_db.insert_element(
        "room001", "Zone", "LD",
        json.dumps({"zone_category": "住宅-1", "zone_is_envelope": False}),
        json.dumps({"type": "polygon", "points": [[0, 0], [1000, 0], [1000, 1000], [0, 1000]]}),
    )
    test_db.insert_element(
        "envelope001", "Zone", "Cタイプ",
        json.dumps({"zone_category": "住宅", "zone_is_envelope": True}),
        json.dumps({"type": "polygon", "points": [[0, 0], [5000, 0], [5000, 5000], [0, 5000]]}),
    )

    graph = build_graph()

    assert find_rooms(graph) == ["room001"]


def test_find_nodes_by_type(sample_elements):
    graph = build_graph()

    assert set(find_nodes_by_type(graph, "Door")) == {"door001"}
    assert find_nodes_by_type(graph, "Window") == []
