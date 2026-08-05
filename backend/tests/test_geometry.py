import pytest

from backend.graph.geometry import (
    create_point,
    create_line,
    create_polygon,
    geometry_from_json,
    parse_geometry,
    check_distance,
    centroid_from_geometry,
    z_gap,
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


def test_centroid_from_geometry_point():
    x, y = centroid_from_geometry('{"type": "point", "x": 500, "y": 100}')

    assert (x, y) == (500, 100)


def test_centroid_from_geometry_line():
    x, y = centroid_from_geometry(
        '{"type": "line", "points": [[4000, 0], [4000, 3000]]}'
    )

    assert (x, y) == (4000, 1500)


def test_centroid_from_geometry_polygon():
    x, y = centroid_from_geometry(
        '{"type": "polygon", '
        '"points": [[0, 0], [1000, 0], [1000, 1000], [0, 1000]]}'
    )

    assert (x, y) == (500, 500)


def test_centroid_from_geometry_none_when_missing():
    assert centroid_from_geometry(None) is None
    assert centroid_from_geometry("") is None


def test_centroid_from_geometry_none_when_invalid():
    assert centroid_from_geometry('{"type": "solid", "x": 0, "y": 0}') is None


def test_parse_geometry_returns_none_z_range_when_absent():
    geom, z_range = parse_geometry('{"type": "point", "x": 500, "y": 100}')

    assert geom.x == 500
    assert z_range is None


def test_parse_geometry_returns_z_range_when_present():
    geom, z_range = parse_geometry(
        '{"type": "point", "x": 500, "y": 100, "z_min": 3000, "z_max": 6000}'
    )

    assert geom.x == 500
    assert z_range == (3000, 6000)


def test_z_gap_is_zero_when_ranges_overlap():
    assert z_gap((0, 3000), (2000, 5000)) == 0.0


def test_z_gap_is_zero_when_ranges_touch():
    assert z_gap((0, 3000), (3000, 6000)) == 0.0


def test_z_gap_between_separated_ranges():
    # 4階建てビルの1階(0-3000mm)と2階(3600-6600mm)のように、平面上では
    # 同じ位置でも高さが離れている要素同士のギャップを検出できる必要がある。
    assert z_gap((0, 3000), (3600, 6600)) == 600.0


def test_z_gap_is_symmetric():
    assert z_gap((3600, 6600), (0, 3000)) == 600.0


def test_z_gap_ignores_missing_range():
    # 高さ情報が無い要素(古い同期データ/合成テストデータ)は、高さ方向を
    # 無視して従来通り平面距離だけで判定できるようにする。
    assert z_gap(None, (0, 3000)) == 0.0
    assert z_gap((0, 3000), None) == 0.0
    assert z_gap(None, None) == 0.0
