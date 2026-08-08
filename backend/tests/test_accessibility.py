import json

from backend.engine.accessibility import analyze_accessibility


def test_analyze_accessibility_finds_dead_ends_and_hub_room(test_db):
    # room2を中心に、4方向それぞれに小部屋を1つずつドア経由で接続する
    # (room2の次数=4でハブ判定、各小部屋の次数=1で行き止まり判定)。
    # room1は完全に離れた場所にある別の行き止まり部屋。
    test_db.insert_element(
        "room2", "Zone", "共用ホール",
        json.dumps({}),
        json.dumps({"type": "polygon", "points": [[4000, 4000], [8000, 4000], [8000, 8000], [4000, 8000]]}),
    )

    test_db.insert_element(
        "room_n", "Zone", "北側の部屋",
        json.dumps({}),
        json.dumps({"type": "polygon", "points": [[4000, 8300], [8000, 8300], [8000, 9300], [4000, 9300]]}),
    )
    test_db.insert_element(
        "door_n", "Door", "北のドア",
        json.dumps({}),
        json.dumps({"type": "point", "x": 6000, "y": 8150}),
    )

    test_db.insert_element(
        "room_s", "Zone", "南側の部屋",
        json.dumps({}),
        json.dumps({"type": "polygon", "points": [[4000, 3400], [8000, 3400], [8000, 3700], [4000, 3700]]}),
    )
    test_db.insert_element(
        "door_s", "Door", "南のドア",
        json.dumps({}),
        json.dumps({"type": "point", "x": 6000, "y": 3850}),
    )

    test_db.insert_element(
        "room_e", "Zone", "東側の部屋",
        json.dumps({}),
        json.dumps({"type": "polygon", "points": [[8300, 4000], [9300, 4000], [9300, 8000], [8300, 8000]]}),
    )
    test_db.insert_element(
        "door_e", "Door", "東のドア",
        json.dumps({}),
        json.dumps({"type": "point", "x": 8150, "y": 6000}),
    )

    test_db.insert_element(
        "room_w", "Zone", "西側の部屋",
        json.dumps({}),
        json.dumps({"type": "polygon", "points": [[3400, 4000], [3700, 4000], [3700, 8000], [3400, 8000]]}),
    )
    test_db.insert_element(
        "door_w", "Door", "西のドア",
        json.dumps({}),
        json.dumps({"type": "point", "x": 3850, "y": 6000}),
    )

    # 完全に離れた場所にある、別の行き止まり部屋。
    test_db.insert_element(
        "room1", "Zone", "孤立気味の部屋",
        json.dumps({}),
        json.dumps({"type": "polygon", "points": [[20000, 0], [24000, 0], [24000, 3000], [20000, 3000]]}),
    )
    test_db.insert_element(
        "door1", "Door", "部屋1のドア",
        json.dumps({}),
        json.dumps({"type": "point", "x": 24150, "y": 1500}),
    )

    result = analyze_accessibility()

    hub_guids = {h["room_guid"] for h in result["hubs"]}
    assert hub_guids == {"room2"}
    assert result["hubs"][0]["degree"] == 4

    dead_end_guids = {d["room_guid"] for d in result["dead_ends"]}
    assert dead_end_guids == {"room1", "room_n", "room_s", "room_e", "room_w"}


def test_analyze_accessibility_empty_when_no_rooms(test_db):
    result = analyze_accessibility()

    assert result == {
        "hub_degree_threshold": 4,
        "dead_ends": [],
        "hubs": [],
    }
