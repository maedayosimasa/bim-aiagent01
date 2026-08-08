import json

from backend.graph.relation import determine_relation, calculate_relations


def test_determine_relation_within_max_distance():
    wall = {"guid": "wall001", "type": "Wall"}
    door = {"guid": "door001", "type": "Door"}

    assert determine_relation(wall, door, 500) == "adjacent"


def test_determine_relation_reversed_type_order():
    door = {"guid": "door001", "type": "Door"}
    wall = {"guid": "wall001", "type": "Wall"}

    assert determine_relation(door, wall, 500) == "adjacent"


def test_determine_relation_beyond_max_distance():
    wall = {"guid": "wall001", "type": "Wall"}
    door = {"guid": "door001", "type": "Door"}

    assert determine_relation(wall, door, 700) is None


def test_determine_relation_within_max_distance_for_window():
    wall = {"guid": "wall001", "type": "Wall"}
    window = {"guid": "window001", "type": "Window"}

    assert determine_relation(wall, window, 500) == "adjacent"


def test_determine_relation_unknown_type_pair():
    # Wall-Roomはルールに無い型の組み合わせ(意図的に除外。
    # relation_rules.pyのコメント参照)。
    wall = {"guid": "wall001", "type": "Wall"}
    room = {"guid": "room001", "type": "Room"}

    assert determine_relation(wall, room, 0) is None


def test_calculate_relations_uses_rules_per_type_pair(sample_elements):
    relations = calculate_relations()

    by_pair = {
        (r["source_guid"], r["target_guid"]): r["relation"]
        for r in relations
    }

    assert by_pair[("wall001", "door001")] == "adjacent"
    assert by_pair[("door001", "room001")] == "connects"
    assert by_pair[("door001", "room002")] == "connects"

    # room001とroom002はポリゴンの辺(x=4000)を共有しており、
    # 代表点間の距離ではなく実際の境界共有として隣接判定される。
    assert by_pair[("room001", "room002")] == "adjacent"

    # Wall-Room has no rule, so it must not appear regardless of distance.
    assert ("wall001", "room001") not in by_pair


def test_calculate_relations_ignores_elements_on_different_floors(test_db):
    # 実データ検証で見つかった不具合の再現: 4階建てビルの1階と4階に、平面上
    # (x,y)は全く同じ位置の壁がある場合、高さの範囲(z_min/z_max)が重なって
    # いない限り、平面上どれだけ近くても隣接/接続と判定してはならない。
    test_db.insert_element(
        "wall_floor1", "Wall", "1階の壁",
        json.dumps({}),
        json.dumps({
            "type": "line", "points": [[0, 0], [19200, 0]],
            "z_min": 0, "z_max": 3000,
        }),
    )
    test_db.insert_element(
        "wall_floor4", "Wall", "4階の壁",
        json.dumps({}),
        json.dumps({
            "type": "line", "points": [[0, 0], [19200, 0]],
            "z_min": 9600, "z_max": 12600,
        }),
    )
    test_db.insert_element(
        "door_floor1", "Door", "1階のドア",
        json.dumps({}),
        json.dumps({
            "type": "point", "x": 2000, "y": 0,
            "z_min": 900, "z_max": 3000,
        }),
    )

    relations = calculate_relations()
    by_pair = {
        frozenset((r["source_guid"], r["target_guid"])): r["relation"]
        for r in relations
    }

    assert by_pair[frozenset(("door_floor1", "wall_floor1"))] == "adjacent"
    assert frozenset(("door_floor1", "wall_floor4")) not in by_pair


def test_calculate_relations_connects_when_z_ranges_overlap(test_db):
    # z_min/z_maxが設定されていても、実際に高さが重なっていれば従来通り
    # 平面距離だけで隣接判定される(3D化で既存の判定を壊していないことの確認)。
    test_db.insert_element(
        "wall_a", "Wall", "壁A",
        json.dumps({}),
        json.dumps({
            "type": "line", "points": [[0, 0], [19200, 0]],
            "z_min": 0, "z_max": 3000,
        }),
    )
    test_db.insert_element(
        "door_a", "Door", "ドアA",
        json.dumps({}),
        json.dumps({
            "type": "point", "x": 2000, "y": 0,
            "z_min": 900, "z_max": 3000,
        }),
    )

    relations = calculate_relations()
    by_pair = {
        (r["source_guid"], r["target_guid"]): r["relation"]
        for r in relations
    }

    assert by_pair[("wall_a", "door_a")] == "adjacent"


def test_calculate_relations_excludes_non_target_types_even_when_touching(test_db):
    # calculate_relations()はSTRtreeへ問い合わせる前に、RELATION_RULESに
    # 一切登場しない型(Column等)を候補から除外する(実データ5699要素の
    # うち対象は1653要素のみに絞る性能最適化)。幾何的に完全に重なって
    # いても関係を持たないことを確認する。
    test_db.insert_element(
        "wall001", "Wall", "壁",
        json.dumps({}),
        json.dumps({"type": "line", "points": [[0, 0], [1000, 0]]}),
    )
    test_db.insert_element(
        "column001", "Column", "柱",
        json.dumps({}),
        json.dumps({"type": "point", "x": 0, "y": 0}),
    )

    assert calculate_relations() == []
