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


def _z_range(data):

    z_min = data.get("z_min")
    z_max = data.get("z_max")

    if z_min is None or z_max is None:
        return None

    return (z_min, z_max)


def parse_geometry(geometry_json):
    """shapely図形と、あれば高さの範囲(z_min, z_max。mm単位)を返す。

    shapelyの.distance()はz座標を渡しても常に2D(x,y)だけで計算し、高さの
    違いを無視してしまう(実測で確認済み)。そのため高さは別途この範囲として
    扱い、呼び出し側でz_gap()と組み合わせて使う。

    z_min/z_maxは、Archicad同期時にGet3DBoundingBoxesの結果から付与される
    (archicad_mcp/tapir.pyのbounding_box_to_geometry()/details_to_geometry()
    参照)。無い場合(古い同期データや合成テストデータ)はNoneを返し、
    呼び出し側は「高さ情報なし=高さ方向は無視する」として扱うこと。
    """

    data = json.loads(geometry_json)

    # "type"省略時はpoint扱い(既存の{"x":..,"y":..}形式との後方互換)
    geometry_type = data.get("type", "point")

    factory = _GEOMETRY_FACTORIES.get(geometry_type)

    if factory is None:
        raise ValueError(f"Unknown geometry type: {geometry_type}")

    return factory(data), _z_range(data)


def geometry_from_json(geometry_json):

    geom, _ = parse_geometry(geometry_json)

    return geom


def check_distance(geom1, geom2):

    return geom1.distance(geom2)


def z_gap(range1, range2):
    """2つの高さ範囲(z_min, z_max)のmm単位の隙間を返す。

    範囲が重なっていれば0。どちらかがNone(高さ情報なし)なら高さ方向を
    無視して0を返す(=既存の2D距離のみの挙動を保つ)。
    """

    if range1 is None or range2 is None:
        return 0.0

    min1, max1 = range1
    min2, max2 = range2

    if max1 < min2:
        return min2 - max1

    if max2 < min1:
        return min1 - max2

    return 0.0


# フロントエンドのグラフ表示(フロアプラン配置)用に、要素の代表点(mm単位)を返す。
# 形状が壊れている/未設定の要素はNoneを返し、呼び出し側で座標なし扱いにする。
def centroid_from_geometry(geometry_json):

    if not geometry_json:
        return None

    try:
        geom = geometry_from_json(geometry_json)
    except (ValueError, KeyError, TypeError, json.JSONDecodeError):
        return None

    return geom.centroid.x, geom.centroid.y