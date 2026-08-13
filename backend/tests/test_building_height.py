import json

from backend.engine.building_height import building_height_points


def test_building_height_points_returns_wall_slab_roof_column(test_db):
    test_db.insert_element(
        "wall1", "Wall", "壁",
        json.dumps({}),
        json.dumps({"type": "line", "points": [[0, 0], [1000, 0]], "z_min": 0, "z_max": 3000}),
    )
    test_db.insert_element(
        "slab1", "Slab", "床",
        json.dumps({}),
        json.dumps({
            "type": "polygon",
            "points": [[0, 0], [1000, 0], [1000, 1000], [0, 1000]],
            "z_min": 2900, "z_max": 3000,
        }),
    )

    points = building_height_points()

    guids = {p["guid"] for p in points}
    assert guids == {"wall1", "slab1"}
    wall_point = next(p for p in points if p["guid"] == "wall1")
    assert wall_point["height_m"] == 3.0
    assert wall_point["x"] == 500
    assert wall_point["y"] == 0


def test_building_height_points_excludes_non_reference_types(test_db):
    test_db.insert_element(
        "room1", "Room", "居室",
        json.dumps({}),
        json.dumps({"type": "polygon", "points": [[0, 0], [1000, 0], [1000, 1000], [0, 1000]]}),
    )

    assert building_height_points() == []


def test_building_height_points_excludes_elements_without_height_range(test_db):
    # z_min/z_maxが無い(古い同期データ等)要素は除外する。
    test_db.insert_element(
        "wall1", "Wall", "壁",
        json.dumps({}),
        json.dumps({"type": "line", "points": [[0, 0], [1000, 0]]}),
    )

    assert building_height_points() == []
