import json

from backend.engine.window_classifier import classify_windows


def test_classify_windows_distinguishes_exterior_interior_unknown(test_db):
    # room1とroom2はx=4000〜4300に300mmの隙間を挟んで並ぶ(Room-Windowの
    # 隣接閾値700mmはtest_evacuation_engine.pyのdoorギャップと同じ考え方)。
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

    # room1の外側の壁(x=0の外側)にある窓 → room1にのみ隣接 → 外部窓。
    test_db.insert_element(
        "window_ext", "Window", "外部窓",
        json.dumps({}),
        json.dumps({"type": "point", "x": -100, "y": 1500}),
    )
    # room1・room2の隙間にある窓 → 両方に隣接 → 内部窓。
    test_db.insert_element(
        "window_int", "Window", "内部窓",
        json.dumps({}),
        json.dumps({"type": "point", "x": 4150, "y": 1500}),
    )
    # どの部屋からも遠く離れた窓 → 判定不能。
    test_db.insert_element(
        "window_isolated", "Window", "孤立窓",
        json.dumps({}),
        json.dumps({"type": "point", "x": 50000, "y": 50000}),
    )

    result = classify_windows()

    assert result["total"] == 3
    assert result["exterior_count"] == 1
    assert result["interior_count"] == 1
    assert result["unknown_count"] == 1

    by_guid = {w["window_guid"]: w for w in result["windows"]}

    assert by_guid["window_ext"]["classification"] == "exterior"
    assert [r["guid"] for r in by_guid["window_ext"]["adjacent_rooms"]] == ["room1"]
    # (2026-08-13追加)Evidence Layer: 命名/トポロジーからのヒューリスティック
    # 判定にはheuristicタグが付く(engine/evidence.py)。
    assert by_guid["window_ext"]["evidence_confidence"] == "heuristic"

    assert by_guid["window_int"]["classification"] == "interior"
    assert {r["guid"] for r in by_guid["window_int"]["adjacent_rooms"]} == {"room1", "room2"}

    assert by_guid["window_isolated"]["classification"] == "unknown"
    assert by_guid["window_isolated"]["adjacent_rooms"] == []


def test_classify_windows_empty_when_no_windows(test_db):
    result = classify_windows()

    assert result == {
        "disclaimer": result["disclaimer"],
        "total": 0,
        "exterior_count": 0,
        "interior_count": 0,
        "unknown_count": 0,
        "windows": [],
    }
