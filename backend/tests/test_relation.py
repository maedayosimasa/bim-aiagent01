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


def test_determine_relation_unknown_type_pair():
    wall = {"guid": "wall001", "type": "Wall"}
    window = {"guid": "window001", "type": "Window"}

    assert determine_relation(wall, window, 0) is None


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
