import pytest

from backend.graph.geometry import (
    create_point,
    create_line,
    create_polygon,
    geometry_from_json,
    check_distance,
)


def test_create_point():
    point = create_point(0, 0)

    assert point.x == 0
    assert point.y == 0


def test_geometry_from_json_defaults_to_point_for_legacy_data():
    # 既存データは"type"を持たない{"x":..,"y":..}形式なので、
    # 後方互換のためpointとして解釈されなければならない。
    point = geometry_from_json('{"x": 500, "y": 100}')

    assert point.geom_type == "Point"
    assert point.x == 500
    assert point.y == 100


def test_geometry_from_json_explicit_point():
    point = geometry_from_json('{"type": "point", "x": 500, "y": 100}')

    assert point.geom_type == "Point"


def test_geometry_from_json_line():
    line = geometry_from_json(
        '{"type": "line", "points": [[0, 0], [1000, 0]]}'
    )

    assert line.geom_type == "LineString"
    assert line.length == 1000


def test_geometry_from_json_polygon():
    polygon = geometry_from_json(
        '{"type": "polygon", '
        '"points": [[0, 0], [1000, 0], [1000, 1000], [0, 1000]]}'
    )

    assert polygon.geom_type == "Polygon"
    assert polygon.area == 1_000_000


def test_geometry_from_json_unknown_type_raises():
    with pytest.raises(ValueError):
        geometry_from_json('{"type": "solid", "x": 0, "y": 0}')


def test_check_distance_between_points():
    wall = create_point(0, 0)
    door = create_point(500, 0)

    assert check_distance(wall, door) == 500


def test_check_distance_point_on_wall_line_is_zero():
    wall = create_line([[4000, 0], [4000, 3000]])
    door = create_point(4000, 1500)

    assert check_distance(wall, door) == 0


def test_check_distance_between_touching_room_polygons_is_zero():
    room_a = create_polygon([[0, 0], [4000, 0], [4000, 3000], [0, 3000]])
    room_b = create_polygon([[4000, 0], [7000, 0], [7000, 3000], [4000, 3000]])

    assert check_distance(room_a, room_b) == 0


def test_check_distance_between_separated_room_polygons_is_positive():
    room_a = create_polygon([[0, 0], [4000, 0], [4000, 3000], [0, 3000]])
    room_b = create_polygon(
        [[4500, 0], [8500, 0], [8500, 3000], [4500, 3000]]
    )

    assert check_distance(room_a, room_b) == 500
