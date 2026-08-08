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
    # 住戸/区画全体を表す大分類ゾーン("Cタイプ"等)は実室ではないため除外
    # する。除外の根拠は命名規則ではなく幾何包含のみ(graph/envelope.py、
    # Archicadのテンプレートによってカテゴリの命名規則が異なりうるため)。
    # 実データと同じパターン(大きな1枚のZoneが、同一階の他Zoneを3件以上・
    # 面積の大部分にわたって包含する)を再現する。
    test_db.insert_element(
        "envelope001", "Zone", "Cタイプ",
        json.dumps({}),
        json.dumps({"type": "polygon", "points": [[0, 0], [10000, 0], [10000, 10000], [0, 10000]]}),
    )
    test_db.insert_element(
        "room001", "Zone", "LD",
        json.dumps({}),
        json.dumps({"type": "polygon", "points": [[0, 0], [3000, 0], [3000, 3000], [0, 3000]]}),
    )
    test_db.insert_element(
        "room002", "Zone", "キッチン",
        json.dumps({}),
        json.dumps({"type": "polygon", "points": [[4000, 0], [7000, 0], [7000, 3000], [4000, 3000]]}),
    )
    test_db.insert_element(
        "room003", "Zone", "トイレ",
        json.dumps({}),
        json.dumps({"type": "polygon", "points": [[0, 4000], [3000, 4000], [3000, 7000], [0, 7000]]}),
    )

    graph = build_graph()

    assert set(find_rooms(graph)) == {"room001", "room002", "room003"}


def test_find_rooms_ignores_zone_with_invalid_geometry_for_envelope_check(test_db):
    # 大分類ゾーン判定(graph/envelope.py)の対象探索中に壊れたジオメトリの
    # Zoneに出会ってもクラッシュせず、単に包含判定の対象から外すだけで
    # 処理を継続し、当のZone自体はfind_rooms()の結果に残る。
    test_db.insert_element(
        "zone_broken", "Zone", "壊れたゾーン",
        json.dumps({}),
        "not valid json",
    )

    graph = build_graph()

    assert find_rooms(graph) == ["zone_broken"]


def test_find_rooms_ignores_non_polygon_zone_for_envelope_check(test_db):
    test_db.insert_element(
        "zone_point", "Zone", "点ジオメトリのゾーン",
        json.dumps({}),
        json.dumps({"type": "point", "x": 0, "y": 0}),
    )

    graph = build_graph()

    assert find_rooms(graph) == ["zone_point"]


def test_find_nodes_by_type(sample_elements):
    graph = build_graph()

    assert set(find_nodes_by_type(graph, "Door")) == {"door001"}
    assert find_nodes_by_type(graph, "Window") == []
