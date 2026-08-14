import json
from collections import defaultdict

from shapely.strtree import STRtree

from .door_ownership import find_door_room_guids, find_owner_wall_guid
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
#
# ただしこのz-gapだけでは切り分けられないケースが実データで見つかった:
# EV/PS等の縦シャフト系Zoneは、階をまたいでz範囲が「隙間」ではなく
# 「重複」するように登録されている(例: 1階分z:400〜3400・2階分
# z:3300〜6300、100mm重複)。隙間が負(=重なっている)場合、z-gapの
# 閾値をどう調整しても原理的に切り分けられない。そのため、両要素に
# floorIndex(Archicad同期時に必ず記録される、階を表す整数)が揃っている
# 場合は、z-gapとは別にfloorIndexそのものの一致も必須条件にする
# (_floor_index()参照)。z-gapは同一floorIndex内の微小なノイズ(スラブ
# 厚み等)を許容する役割として引き続き機能する。
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


def _floor_index(element):
    properties = json.loads(element["properties"] or "{}")
    return properties.get("floorIndex")


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


def _refine_door_room_connections(elements, relations):
    """Room/Zone-Doorの"connects"を、ドアのowner壁ベースの精密な判定で置き換える。

    距離ベースの一般的な"connects"判定(RELATION_RULES、閾値700mm)は、
    密集した実データでは1つのドアが同時に3〜6件以上の部屋へ「接続」と
    誤判定される問題があった(閾値をどれだけ絞っても解消しない、
    graph/door_ownership.py参照)。ドアのowner壁が特定できる場合は、
    その壁の両側の点-in-ポリゴン判定による正確な結果で置き換える。
    owner壁が特定できないドア(実データでは稀)は、従来の距離ベース
    判定のまま変更しない(フォールバック)。

    候補となるRoom/Zoneは、ドアと同じfloorIndexのものだけに絞る。これを
    怠ると、階をまたいで同じXY座標に重なるZone(EV/PS等の縦シャフト、
    または単に平面上たまたま同じ位置にある別階の部屋)まで点-in-ポリゴン
    判定でヒットしてしまう(実データで発覚: あるドアのプローブ点が、
    "MB"や"共用廊下"など複数階にまたがる同名Zoneすべてに"contains"=True
    となっていた)。

    (2026-08-14修正)置き換え後の"connects"のdistanceは、以前は一律
    0.0mm(点-in-ポリゴン判定は真偽のみで距離を持たないための単なる
    プレースホルダー値)だった。これがそのままgraph/path.py(避難経路の
    最短路探索、重みは"distance")・evacuation_engine.pyの避難歩行距離
    計算に流用されており、経路が何ホップであってもRoom-Door "connects"
    エッジが全て0.0mmのため合計距離が常に0mになる(=歩行距離チェックが
    実質何も計算していないのと同じ)不具合をユーザーからの指摘
    (「歩行距離が0mで合格は計算されていないのでは」)で実データにより
    確認した。ドアの代表点(geometryの重心)から部屋ポリゴンへの
    Hausdorff距離(`Point.hausdorff_distance(Polygon)`、点から見て
    ポリゴン内で最も遠い点までの距離)を計算し直すことで、「その部屋の
    最も奥まった場所からこのドアまでの距離」という歩行距離制限の法令上の
    定義(建築基準法施行令120条、居室の各部分から出口までの歩行距離)に
    近い近似値にした。
    """

    doors = [element for element in elements if element["type"] == "Door"]

    if not doors:
        return relations

    walls_by_guid = {
        element["guid"]: element for element in elements if element["type"] == "Wall"
    }

    envelope_guids = _envelope_zone_guids(elements)

    room_records_by_floor = defaultdict(list)

    for element in elements:

        if element["type"] not in ("Zone", "Room") or element["guid"] in envelope_guids:
            continue

        try:
            geom, _ = parse_geometry(element["geometry"])
        except (ValueError, KeyError, TypeError, json.JSONDecodeError):
            continue

        if geom.geom_type != "Polygon":
            continue

        room_records_by_floor[_floor_index(element)].append((element["guid"], geom))

    refined_room_guids = {}

    for door in doors:

        candidate_rooms = room_records_by_floor.get(_floor_index(door), [])
        result = find_door_room_guids(door, walls_by_guid, candidate_rooms)

        if result is not None:
            refined_room_guids[door["guid"]] = result

    if not refined_room_guids:
        return relations

    room_geom_by_guid = {
        room_guid: geom
        for records in room_records_by_floor.values()
        for room_guid, geom in records
    }

    door_point_by_guid = {}
    for door in doors:
        try:
            geom, _ = parse_geometry(door["geometry"])
        except (ValueError, KeyError, TypeError, json.JSONDecodeError):
            continue
        door_point_by_guid[door["guid"]] = geom.centroid

    room_types = {"Zone", "Room"}
    elements_by_guid = {element["guid"]: element["type"] for element in elements}

    def _is_refined_door_room_connects(relation):

        source, target = relation["source_guid"], relation["target_guid"]

        if relation["relation"] != "connects":
            return False

        if source in refined_room_guids and elements_by_guid.get(target) in room_types:
            return True

        if target in refined_room_guids and elements_by_guid.get(source) in room_types:
            return True

        return False

    relations = [
        relation for relation in relations
        if not _is_refined_door_room_connects(relation)
    ]

    for door_guid, room_guids in refined_room_guids.items():
        door_point = door_point_by_guid.get(door_guid)
        for room_guid in room_guids:
            room_geom = room_geom_by_guid.get(room_guid)
            distance = (
                door_point.hausdorff_distance(room_geom)
                if door_point is not None and room_geom is not None
                else 0.0
            )
            relations.append({
                "source_guid": door_guid,
                "target_guid": room_guid,
                "relation": "connects",
                "distance": distance,
            })

    return relations


def _refine_wall_opening_adjacency(elements, relations):
    """Wall-Door/Wall-Windowの"adjacent"を、owner壁ベースの精密な判定で絞り込む。

    距離ベースの一般的な"adjacent"判定(RELATION_RULES、閾値600mm)は、
    ドア/窓が実際に設置されている壁だけでなく、たまたま600mm以内にある
    無関係な壁(隣接する別の壁の端点付近等)まで拾ってしまう。実データで
    検証した結果、真の所属壁は距離0.0mmで必ず見つかる一方、無関係な壁は
    85〜600mmの範囲に散らばっていることを確認済み(壁の芯線とドア/窓の
    代表ジオメトリの位置関係による)。owner壁(properties.archicad_
    details.ownerElementId、Archicad側の確定情報)が特定できるDoor/
    Windowは、その壁との"adjacent"のみを残し、他の壁との"adjacent"は
    除外する。owner壁が特定できない場合は既存の距離ベース判定のまま
    変更しない(フォールバック)。
    """

    openings = [element for element in elements if element["type"] in ("Door", "Window")]

    if not openings:
        return relations

    walls_by_guid = {
        element["guid"]: element for element in elements if element["type"] == "Wall"
    }

    owner_wall_guid_by_opening = {}

    for opening in openings:

        owner_wall_guid = find_owner_wall_guid(opening, walls_by_guid)

        if owner_wall_guid is not None:
            owner_wall_guid_by_opening[opening["guid"]] = owner_wall_guid

    if not owner_wall_guid_by_opening:
        return relations

    def _is_non_owner_wall_adjacency(relation):

        if relation["relation"] != "adjacent":
            return False

        source, target = relation["source_guid"], relation["target_guid"]

        if source in owner_wall_guid_by_opening and target in walls_by_guid:
            return target != owner_wall_guid_by_opening[source]

        if target in owner_wall_guid_by_opening and source in walls_by_guid:
            return source != owner_wall_guid_by_opening[target]

        return False

    return [
        relation for relation in relations
        if not _is_non_owner_wall_adjacency(relation)
    ]


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
    floor_indices = [_floor_index(element) for element in elements]

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

            # floorIndexが両方とも分かっていて、かつ異なる場合は無条件で
            # 除外する(z-gapでは切り分けられない縦シャフト系Zoneのz範囲
            # 重複対策、MAX_Z_GAP_MMの説明コメント参照)。
            floor1, floor2 = floor_indices[i], floor_indices[j]

            if floor1 is not None and floor2 is not None and floor1 != floor2:
                continue

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

    relations = _refine_wall_opening_adjacency(elements, relations)

    return _refine_door_room_connections(elements, relations)