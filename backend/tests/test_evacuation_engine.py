import json

from backend.engine.evacuation_engine import compute_evacuation_walking_distances, find_evacuation_routes


def test_find_evacuation_routes_finds_exterior_door_and_route(test_db):
    # room1 -door1- room2 -door2- (外部)。door2はroom2にしか繋がらないため
    # 外部ドアとみなされる。room3はどのドアとも繋がらない孤立した部屋。
    test_db.insert_element(
        "room1", "Room", "居室A",
        json.dumps({}),
        json.dumps({"type": "polygon", "points": [[0, 0], [4000, 0], [4000, 3000], [0, 3000]]}),
    )
    test_db.insert_element(
        "door1", "Door", "内部ドア",
        json.dumps({}),
        json.dumps({"type": "point", "x": 4150, "y": 1500}),
    )
    test_db.insert_element(
        "room2", "Room", "居室B(廊下)",
        json.dumps({}),
        json.dumps({"type": "polygon", "points": [[4300, 0], [8300, 0], [8300, 3000], [4300, 3000]]}),
    )
    test_db.insert_element(
        "door2", "Door", "外部ドア",
        json.dumps({}),
        json.dumps({"type": "point", "x": 8450, "y": 1500}),
    )
    test_db.insert_element(
        "room3", "Room", "孤立した部屋",
        json.dumps({}),
        json.dumps({"type": "polygon", "points": [[20000, 0], [24000, 0], [24000, 3000], [20000, 3000]]}),
    )

    result = find_evacuation_routes()

    assert result["exterior_doors"] == ["door2"]

    routes = {r["room_guid"]: r for r in result["routes"]}

    assert routes["room2"]["reachable"] is True
    assert routes["room2"]["nearest_exit_door_guid"] == "door2"
    assert routes["room2"]["total_distance_mm"] == 150

    assert routes["room1"]["reachable"] is True
    assert routes["room1"]["total_distance_mm"] == 450
    assert routes["room1"]["route"] == ["door2", "room2", "door1", "room1"]

    assert routes["room3"]["reachable"] is False
    assert routes["room3"]["total_distance_mm"] is None
    assert routes["room3"]["nearest_exit_door_guid"] is None


def test_find_evacuation_routes_no_exterior_doors_marks_all_unreachable(test_db):
    # door1がroom1・room2の両方に繋がる(degree=2)ため外部ドアとみなされず、
    # 建物内のどこからも脱出できないという結果になる。
    test_db.insert_element(
        "room1", "Room", "居室A",
        json.dumps({}),
        json.dumps({"type": "polygon", "points": [[0, 0], [4000, 0], [4000, 3000], [0, 3000]]}),
    )
    test_db.insert_element(
        "room2", "Room", "居室B",
        json.dumps({}),
        json.dumps({"type": "polygon", "points": [[4300, 0], [8300, 0], [8300, 3000], [4300, 3000]]}),
    )
    test_db.insert_element(
        "door1", "Door", "内部ドア",
        json.dumps({}),
        json.dumps({"type": "point", "x": 4150, "y": 1500}),
    )

    result = find_evacuation_routes()

    assert result["exterior_doors"] == []
    assert all(not route["reachable"] for route in result["routes"])


def test_compute_evacuation_walking_distances_converts_mm_to_m(test_db):
    # test_find_evacuation_routes_finds_exterior_door_and_route()と同じ配置。
    test_db.insert_element(
        "room1", "Room", "居室A",
        json.dumps({}),
        json.dumps({"type": "polygon", "points": [[0, 0], [4000, 0], [4000, 3000], [0, 3000]]}),
    )
    test_db.insert_element(
        "door1", "Door", "内部ドア",
        json.dumps({}),
        json.dumps({"type": "point", "x": 4150, "y": 1500}),
    )
    test_db.insert_element(
        "room2", "Room", "居室B(廊下)",
        json.dumps({}),
        json.dumps({"type": "polygon", "points": [[4300, 0], [8300, 0], [8300, 3000], [4300, 3000]]}),
    )
    test_db.insert_element(
        "door2", "Door", "外部ドア",
        json.dumps({}),
        json.dumps({"type": "point", "x": 8450, "y": 1500}),
    )
    test_db.insert_element(
        "room3", "Room", "孤立した部屋",
        json.dumps({}),
        json.dumps({"type": "polygon", "points": [[20000, 0], [24000, 0], [24000, 3000], [20000, 3000]]}),
    )

    items = compute_evacuation_walking_distances()
    by_guid = {i["target_guid"]: i for i in items}

    assert by_guid["room2"]["measured_value"] == 0.15  # 150mm → 0.15m
    assert by_guid["room1"]["measured_value"] == 0.45  # 450mm → 0.45m
    assert by_guid["room1"]["evidence"]["reachable"] is True

    # 到達不能な部屋は「歩行距離が長すぎる」ではなく判定不能(None)にする。
    assert by_guid["room3"]["measured_value"] is None
    assert by_guid["room3"]["evidence"]["reachable"] is False
