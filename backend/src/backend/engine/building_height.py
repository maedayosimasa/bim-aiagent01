"""建物の実測高さ(BIM空間知能エンジンが取得した実データ)を、高さ制限
envelope(道路斜線制限/隣地斜線制限/北側斜線制限/高度地区)との合否判定に
使うための共通ヘルパー。

「建物の高さ」は法令上「地盤面からの高さ」だが、このプロジェクトのBIM
データには地盤面(グレードレベル)を明示的に記録する仕組みが無い。他の
envelope計算(道路斜線等のz_m)・effective_daylighting.pyのH計算と同じく、
「敷地Zoneが乗るプロジェクト座標系のz=0を地盤面とみなす」という既存の
簡略化をそのまま踏襲する(新たな前提を持ち込まない)。

対象要素はWall/Slab/Roof/Column(building_coverage.py・effective_daylighting.py
の`_OVERHANG_ELEMENT_TYPES`と同じ、実データでポリゴン/ラインの形状+高さ範囲
(Get3DBoundingBoxes由来のz_min/z_max)を取得できる型にWall・Columnを加えた
もの)。各要素の代表点(重心のXY)と最高点(z_max、mm→m)を返す。
"""

import json

from ..database.db import get_elements
from ..graph.geometry import parse_geometry

_MM_PER_M = 1_000

_HEIGHT_REFERENCE_ELEMENT_TYPES = ("Wall", "Slab", "Roof", "Column")


def building_height_points() -> list[dict]:
    """Wall/Slab/Roof/Columnの代表点(x, y, height_m, guid, name)一覧を返す。

    高さ範囲(z_max)を持たない要素(古い同期データ・合成テストデータ等)は
    除外する(判定対象にしない、過検出よりも見逃しを許容する設計)。
    """
    points = []
    for element in get_elements():
        if element["type"] not in _HEIGHT_REFERENCE_ELEMENT_TYPES:
            continue
        try:
            geom, z_range = parse_geometry(element["geometry"])
        except (ValueError, KeyError, TypeError, json.JSONDecodeError):
            continue
        if z_range is None:
            continue
        _z_min, z_max = z_range
        if z_max is None:
            continue

        centroid = geom.centroid
        points.append({
            "guid": element["guid"],
            "name": element["name"],
            "x": centroid.x,
            "y": centroid.y,
            "height_m": z_max / _MM_PER_M,
        })
    return points
