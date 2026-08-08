import json

from shapely.strtree import STRtree

from .envelope import find_envelope_zone_guids
from .geometry import parse_geometry, z_gap
from .relation_rules import RELATION_RULES, RELATION_TARGET_TYPES
from ..database.db import get_elements


# 階(高さ)が離れている要素同士を隣接/接続と誤判定しないための許容値(mm)。
# スラブの厚み程度の隙間(実データで確認した値は約100mm)は同一階の要素の
# ノイズとして許容し、それを大きく超える隙間(階をまたぐ、実データで
# 3000mm超)は平面距離に関わらず完全に除外する。RELATION_RULESの
# max_distance(平面上の許容誤差、例えばWall-Doorで600mm)とは別の軸の
# 閾値なので、合成せずAND条件として扱う。
MAX_Z_GAP_MM = 150.0

# RELATION_RULES全体の中で最大のmax_distance(mm)。calculate_relations()が
# shapely.STRtreeへ候補を問い合わせる際のバッファ半径として使う——これより
# 厳密な、ペアの型ごとの閾値は従来通りdetermine_relation()側で判定する。
_GLOBAL_MAX_DISTANCE_MM = max(rule["max_distance"] for rule in RELATION_RULES.values())


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


def _envelope_zone_guids(elements):
    """住戸/区画全体を表す大分類ゾーン(実室ではない)のguid集合を返す。

    graph/envelope.pyの幾何包含判定を使う(命名規則には依存しない、
    詳細はそちらのdocstring参照)。実データ検証で、こうした大分類Zone
    ("Cタイプ"等)が同じ住戸内の実室Zone十数件を幾何的に包含しており、
    これを実室と同列に扱うと1つのドア/壁が同時に多数のZoneへ「隣接」
    「接続」と誤判定される不具合があった。
    """

    zone_records = []

    for element in elements:

        if element["type"] not in ("Zone", "Room"):
            continue

        try:
            geom, _ = parse_geometry(element["geometry"])
        except (ValueError, KeyError, TypeError, json.JSONDecodeError):
            continue

        if geom.geom_type != "Polygon":
            continue

        properties = json.loads(element["properties"] or "{}")

        zone_records.append({
            "guid": element["guid"],
            "floor": properties.get("floorIndex"),
            "polygon": geom,
        })

    return find_envelope_zone_guids(zone_records)


def calculate_relations():
    """要素間の空間関係(隣接/接続)を計算する。

    以前は全要素の全ペア(O(n²))を毎回計算しており、実データ5699要素で
    約24秒かかっていた(CLAUDE.md「④ 空間関係エンジン」参照)。2段階で
    候補を絞り込むことで高速化する:
    (1) RELATION_RULESにどの組み合わせでも登場しない型(Column/Beam/Slab/
        Object等)、および住戸全体を表す大分類ゾーン(_envelope_zone_
        guids()参照)は絶対に(あるいは実室として)関係を持つべきではない
        ため、あらかじめ除外する(実データ5699要素のうち対象は1653要素
        のみ)。
    (2) 残った要素同士もshapely.STRtree(空間インデックス)で近傍候補だけに
        絞ってから、厳密な距離計算・determine_relation()判定を行う。
    """

    elements = [
        element for element in get_elements()
        if element["type"] in RELATION_TARGET_TYPES
    ]

    envelope_guids = _envelope_zone_guids(elements)

    elements = [e for e in elements if e["guid"] not in envelope_guids]

    # 幾何は要素ごとに一度だけパースする(内側ループのたびに同じ要素の
    # ジオメトリを再パースしていた既存の無駄を避ける)。破損したジオメトリ
    # (座標欠損等)を持つ要素はここで除外し、その1件のせいで計算全体が
    # クラッシュしないようにする。
    parsed = []
    for element in elements:
        try:
            parsed.append((element, parse_geometry(element["geometry"])))
        except (ValueError, KeyError, TypeError, json.JSONDecodeError):
            continue

    elements = [element for element, _ in parsed]
    parsed_geometries = [geometry for _, geometry in parsed]
    geometries = [geom for geom, _ in parsed_geometries]

    tree = STRtree(geometries)

    relations = []

    for i, geom in enumerate(geometries):

        # 自分自身より後ろ(j > i)の候補だけを見ることで、各ペアを一度だけ
        # 処理する(STRtreeは自分自身や既に処理済みのjも返してくるため)。
        candidate_indices = tree.query(
            geom.buffer(_GLOBAL_MAX_DISTANCE_MM), predicate="intersects"
        )

        for j in candidate_indices:

            j = int(j)

            if j <= i:
                continue

            e1, e2 = elements[i], elements[j]
            g1, z1 = parsed_geometries[i]
            g2, z2 = parsed_geometries[j]

            # 高さが離れすぎている(=別の階にある)ペアは、平面上どれだけ
            # 近くても除外する(実データ検証で発覚: 4階建てビルの各階の
            # 同じ位置にある壁が、全ドアと同時に「隣接」と誤判定されて
            # いた)。距離自体は従来通り平面(x,y)上の距離のみ。
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