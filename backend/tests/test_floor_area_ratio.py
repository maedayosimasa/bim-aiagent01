import json

from backend.engine.floor_area_ratio import calculate_floor_area_ratio


def test_calculate_floor_area_ratio_sums_rooms_across_floors(test_db):
    test_db.insert_element(
        "site1", "Zone", "敷地",
        json.dumps({}),
        json.dumps({"type": "polygon", "points": [[-1000, -1000], [11000, -1000], [11000, 11000], [-1000, 11000]]}),
    )
    # 1階: 100m^2(10m x 10m)。floorIndexは無いが単一階として扱われる。
    test_db.insert_element(
        "room1", "Room", "1階居室",
        json.dumps({}),
        json.dumps({"type": "polygon", "points": [[0, 0], [10000, 0], [10000, 10000], [0, 10000]]}),
    )
    # 2階: 50m^2(5m x 10m)。
    test_db.insert_element(
        "room2", "Room", "2階居室",
        json.dumps({"floorIndex": 1}),
        json.dumps({"type": "polygon", "points": [[0, 0], [5000, 0], [5000, 10000], [0, 10000]]}),
    )

    result = calculate_floor_area_ratio()

    assert len(result) == 1
    entry = result[0]
    assert entry["target_guid"] == "site1"
    # site面積 = 12m x 12m = 144m^2。延べ面積 = 100+50 = 150m^2。
    assert entry["evidence"]["site_area_m2"] == 144.0
    assert entry["evidence"]["total_floor_area_m2"] == 150.0
    assert entry["measured_value"] == 150.0 / 144.0


def test_calculate_floor_area_ratio_excludes_site_and_road_zones(test_db):
    # 敷地境界線・前面道路のZoneはfind_rooms()が除外しないため、延べ面積の
    # 合計に紛れ込む不具合の回帰テスト(effective_daylighting.pyと同じ理由)。
    test_db.insert_element(
        "site1", "Zone", "敷地",
        json.dumps({}),
        json.dumps({"type": "polygon", "points": [[-1000, -1000], [11000, -1000], [11000, 11000], [-1000, 11000]]}),
    )
    test_db.insert_element(
        "road1", "Zone", "前面道路",
        json.dumps({}),
        json.dumps({"type": "polygon", "points": [[-1000, -3000], [11000, -3000], [11000, -1000], [-1000, -1000]]}),
    )
    test_db.insert_element(
        "room1", "Room", "居室",
        json.dumps({}),
        json.dumps({"type": "polygon", "points": [[0, 0], [10000, 0], [10000, 10000], [0, 10000]]}),
    )

    result = calculate_floor_area_ratio()

    # 敷地Zone自身(144m^2)・道路Zoneが延べ面積(100m^2のみ)に混入していない。
    assert result[0]["evidence"]["total_floor_area_m2"] == 100.0


def test_calculate_floor_area_ratio_returns_empty_when_no_site_boundary(test_db):
    test_db.insert_element(
        "room1", "Room", "居室",
        json.dumps({}),
        json.dumps({"type": "polygon", "points": [[0, 0], [10000, 0], [10000, 10000], [0, 10000]]}),
    )

    assert calculate_floor_area_ratio() == []
