from shapely.geometry import Point, LineString, Polygon
import json


def create_point(x, y):

    return Point(x, y)


def create_line(points):

    return LineString(points)


def create_polygon(points):

    return Polygon(points)


# BIM要素の形状種別ごとのファクトリ。
# 壁は芯線(line)、部屋は境界(polygon)、それ以外は代表点(point)として表現する。
_GEOMETRY_FACTORIES = {
    "point": lambda data: create_point(data["x"], data["y"]),
    "line": lambda data: create_line(data["points"]),
    "polygon": lambda data: create_polygon(data["points"]),
}


def geometry_from_json(geometry_json):

    data = json.loads(geometry_json)

    # "type"省略時はpoint扱い(既存の{"x":..,"y":..}形式との後方互換)
    geometry_type = data.get("type", "point")

    factory = _GEOMETRY_FACTORIES.get(geometry_type)

    if factory is None:
        raise ValueError(f"Unknown geometry type: {geometry_type}")

    return factory(data)


def check_distance(geom1, geom2):

    return geom1.distance(geom2)