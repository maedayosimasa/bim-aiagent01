"""ドアが実際に接する部屋(Room/Zone)を、壁の両側の点-in-ポリゴン判定で
正確に求める。

Room/Zone-Doorの一般的な"connects"関係(RELATION_RULESの距離閾値)は、
密集した実データ(共用廊下・水回り等の小部屋が密集するマンション)では
1つのドアが同時に3〜6件以上の部屋へ「接続」と誤判定される問題があった。
閾値をどれだけ絞っても解消しない(壁の所属と違い、ドアの真の所属壁は
常に距離0mmで見つかるのに対し、部屋の場合は密集した隣室同士が距離0mm
でも複数重なるため)。

ドアのowner壁(properties.archicad_details.ownerElementId、Archicad側の
確定情報であり幾何推測ではない)を起点に、その壁の両側にプローブ点を置き、
実際にどのRoom/Zoneポリゴンに含まれるかを判定することで、ドアが本当に
接する部屋(内部ドアなら最大2件、外部ドアなら1件)を正確に求める。
"""

import json
import math

from shapely.geometry import Point

from .geometry import parse_geometry

_METERS_TO_MM = 1000

# 壁厚が取得できない場合のプローブ点オフセット(mm)。実データの壁厚は
# 150〜220mm程度だったため、その半分強を想定した保守的な既定値。
_DEFAULT_HALF_THICKNESS_MM = 150.0

# プローブ点を壁面からさらに外側へずらす余裕(mm)。壁の厚み方向の
# 中心線ちょうどではなく、確実に部屋ポリゴンの内側に入るようにする。
_PROBE_MARGIN_MM = 50.0


def _wall_half_thickness_mm(wall):
    details = json.loads(wall["properties"] or "{}").get("archicad_details") or {}
    thickness_m = details.get("begThickness") or details.get("endThickness")

    if thickness_m is None:
        return _DEFAULT_HALF_THICKNESS_MM

    return thickness_m * _METERS_TO_MM / 2


def _door_owner_wall(door, walls_by_guid):
    details = json.loads(door["properties"] or "{}").get("archicad_details") or {}

    if details.get("ownerElementType") != "Wall":
        return None

    owner_guid = (details.get("ownerElementId") or {}).get("guid")

    return walls_by_guid.get(owner_guid)


def _probe_points(wall):
    """壁の芯線の中点から、壁の厚み方向(法線)両側にプローブ点を置く。"""

    try:
        geom, _ = parse_geometry(wall["geometry"])
    except (ValueError, KeyError, TypeError, json.JSONDecodeError):
        return []

    if geom.geom_type != "LineString":
        return []

    (x1, y1), (x2, y2) = geom.coords[0], geom.coords[-1]
    dx, dy = x2 - x1, y2 - y1
    length = math.hypot(dx, dy)

    if length == 0:
        return []

    mx, my = (x1 + x2) / 2, (y1 + y2) / 2

    # 壁の方向ベクトルに直交する単位法線ベクトル。
    nx, ny = -dy / length, dx / length

    offset = _wall_half_thickness_mm(wall) + _PROBE_MARGIN_MM

    return [
        Point(mx + nx * offset, my + ny * offset),
        Point(mx - nx * offset, my - ny * offset),
    ]


def find_door_room_guids(door, walls_by_guid, room_records):
    """ドアが実際に接する部屋(Room/Zone)のguid集合を返す。

    owner壁が特定できない(details.ownerElementTypeがWallでない、または
    対応する壁がelements中に無い)場合はNoneを返す——この場合は呼び出し側
    が距離ベースの一般的な"connects"判定にフォールバックすべきという
    意味で、空集合(=判定できたが該当部屋が無かった)とは区別する。

    room_recordsは(guid, shapely Polygon)のタプルのリスト。
    """

    owner_wall = _door_owner_wall(door, walls_by_guid)

    if owner_wall is None:
        return None

    room_guids = set()

    for probe in _probe_points(owner_wall):

        for room_guid, polygon in room_records:

            if polygon.contains(probe):
                room_guids.add(room_guid)
                break

    return room_guids
