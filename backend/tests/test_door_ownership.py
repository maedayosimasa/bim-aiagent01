import json

from shapely.geometry import Polygon

from backend.graph.door_ownership import find_door_room_guids


def _wall(guid, points, thickness_m=0.2):
    return {
        "guid": guid,
        "type": "Wall",
        "properties": json.dumps({
            "archicad_details": {"begThickness": thickness_m, "endThickness": thickness_m},
        }),
        "geometry": json.dumps({"type": "line", "points": points}),
    }


def _door(guid, owner_wall_guid=None, owner_type="Wall"):
    details = {}
    if owner_wall_guid is not None:
        details = {
            "ownerElementType": owner_type,
            "ownerElementId": {"guid": owner_wall_guid},
        }
    return {
        "guid": guid,
        "type": "Door",
        "properties": json.dumps({"archicad_details": details}),
    }


def _room(guid, points):
    return guid, Polygon(points)


def test_interior_door_finds_rooms_on_both_sides():
    # 壁はx=1000の縦線(y:0-2000)。厚み200mm+余裕50mmでプローブ点は
    # (850,1000)と(1150,1000)に置かれる。
    wall = _wall("wall1", [[1000, 0], [1000, 2000]])
    door = _door("door1", owner_wall_guid="wall1")
    walls_by_guid = {"wall1": wall}
    room_records = [
        _room("roomA", [[0, 0], [1000, 0], [1000, 2000], [0, 2000]]),
        _room("roomB", [[1000, 0], [2000, 0], [2000, 2000], [1000, 2000]]),
    ]

    result = find_door_room_guids(door, walls_by_guid, room_records)

    assert result == {"roomA", "roomB"}


def test_exterior_door_finds_only_one_side():
    wall = _wall("wall1", [[1000, 0], [1000, 2000]])
    door = _door("door1", owner_wall_guid="wall1")
    walls_by_guid = {"wall1": wall}
    room_records = [
        _room("roomA", [[0, 0], [1000, 0], [1000, 2000], [0, 2000]]),
        # roomBは無い(片側は屋外)。
    ]

    result = find_door_room_guids(door, walls_by_guid, room_records)

    assert result == {"roomA"}


def test_returns_none_when_owner_type_is_not_wall():
    door = _door("door1", owner_wall_guid="obj1", owner_type="Object")

    result = find_door_room_guids(door, {}, [])

    assert result is None


def test_returns_none_when_owner_details_missing():
    door = _door("door1")  # ownerElementIdが無い

    result = find_door_room_guids(door, {}, [])

    assert result is None


def test_returns_none_when_owner_wall_not_found_in_elements():
    door = _door("door1", owner_wall_guid="wall-missing")

    result = find_door_room_guids(door, {}, [])

    assert result is None


def test_uses_default_thickness_when_missing():
    wall = _wall("wall1", [[1000, 0], [1000, 2000]])
    wall["properties"] = json.dumps({"archicad_details": {}})  # 厚み情報なし
    door = _door("door1", owner_wall_guid="wall1")
    walls_by_guid = {"wall1": wall}
    room_records = [
        _room("roomA", [[0, 0], [1000, 0], [1000, 2000], [0, 2000]]),
        _room("roomB", [[1000, 0], [2000, 0], [2000, 2000], [1000, 2000]]),
    ]

    result = find_door_room_guids(door, walls_by_guid, room_records)

    assert result == {"roomA", "roomB"}


def test_returns_empty_set_when_wall_geometry_invalid():
    wall = _wall("wall1", [[1000, 0], [1000, 2000]])
    wall["geometry"] = "not valid json"
    door = _door("door1", owner_wall_guid="wall1")

    result = find_door_room_guids(door, {"wall1": wall}, [])

    assert result == set()


def test_returns_empty_set_when_wall_geometry_is_not_a_line():
    # 多角形壁(geometryType="Polygonal")等、線分ではない壁ジオメトリ。
    wall = _wall("wall1", [[1000, 0], [1000, 2000]])
    wall["geometry"] = json.dumps({
        "type": "polygon",
        "points": [[900, 0], [1100, 0], [1100, 2000], [900, 2000]],
    })
    door = _door("door1", owner_wall_guid="wall1")

    result = find_door_room_guids(door, {"wall1": wall}, [])

    assert result == set()


def test_returns_empty_set_when_wall_is_degenerate_point():
    wall = _wall("wall1", [[1000, 0], [1000, 0]])
    door = _door("door1", owner_wall_guid="wall1")

    result = find_door_room_guids(door, {"wall1": wall}, [])

    assert result == set()
