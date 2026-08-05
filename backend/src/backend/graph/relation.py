from .geometry import parse_geometry, z_gap
from .relation_rules import RELATION_RULES
from ..database.db import get_elements


# 階(高さ)が離れている要素同士を隣接/接続と誤判定しないための許容値(mm)。
# スラブの厚み程度の隙間(実データで確認した値は約100mm)は同一階の要素の
# ノイズとして許容し、それを大きく超える隙間(階をまたぐ、実データで
# 3000mm超)は平面距離に関わらず完全に除外する。RELATION_RULESの
# max_distance(平面上の許容誤差、例えばWall-Doorで600mm)とは別の軸の
# 閾値なので、合成せずAND条件として扱う。
MAX_Z_GAP_MM = 150.0


def determine_relation(element1, element2, distance):

    type1 = element1["type"]
    type2 = element2["type"]

    rule = RELATION_RULES.get((type1, type2))

    if rule is None:
        rule = RELATION_RULES.get((type2, type1))

    if rule is None:
        return None

    if distance > rule["max_distance"]:
        return None

    return rule["relation"]


def calculate_relations():

    elements = get_elements()

    # 幾何は要素ごとに一度だけパースする(内側ループのたびに同じ要素の
    # ジオメトリを再パースしていた既存の無駄を避ける)。
    parsed_geometries = [parse_geometry(element["geometry"]) for element in elements]

    relations = []

    for i in range(len(elements)):

        for j in range(i + 1, len(elements)):

            e1 = elements[i]
            e2 = elements[j]

            g1, z1 = parsed_geometries[i]
            g2, z2 = parsed_geometries[j]

            # 高さが離れすぎている(=別の階にある)ペアは、平面上どれだけ
            # 近くても最初から候補から除外する(実データ検証で発覚: 4階建て
            # ビルの各階の同じ位置にある壁が、全ドアと同時に「隣接」と
            # 誤判定されていた)。距離自体は従来通り平面(x,y)上の距離のみ。
            if z_gap(z1, z2) > MAX_Z_GAP_MM:
                continue

            distance = g1.distance(g2)

            relation = determine_relation(e1, e2, distance)

            if relation is not None:

                relations.append({
                    "source_guid": e1["guid"],
                    "target_guid": e2["guid"],
                    "relation": relation,
                    "distance": distance,
                })

    return relations