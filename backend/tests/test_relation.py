import json

from backend.graph.relation import (
    determine_relation,
    calculate_relations,
    _refine_door_room_connections,
    _refine_wall_opening_adjacency,
)


def test_determine_relation_within_max_distance():
    wall = {"guid": "wall001", "type": "Wall"}
    door = {"guid": "door001", "type": "Door"}

    assert determine_relation(wall, door, 500) == "adjacent"


def test_determine_relation_reversed_type_order():
    door = {"guid": "door001", "type": "Door"}
    wall = {"guid": "wall001", "type": "Wall"}

    assert determine_relation(door, wall, 500) == "adjacent"


def test_determine_relation_beyond_max_distance():
    wall = {"guid": "wall001", "type": "Wall"}
    door = {"guid": "door001", "type": "Door"}

    assert determine_relation(wall, door, 700) is None


def test_determine_relation_within_max_distance_for_window():
    wall = {"guid": "wall001", "type": "Wall"}
    window = {"guid": "window001", "type": "Window"}

    assert determine_relation(wall, window, 500) == "adjacent"


def test_determine_relation_unknown_type_pair():
    # Wall-Roomはルールに無い型の組み合わせ(意図的に除外。
    # relation_rules.pyのコメント参照)。
    wall = {"guid": "wall001", "type": "Wall"}
    room = {"guid": "room001", "type": "Room"}

    assert determine_relation(wall, room, 0) is None


def test_calculate_relations_uses_rules_per_type_pair(sample_elements):
    relations = calculate_relations()

    by_pair = {
        (r["source_guid"], r["target_guid"]): r["relation"]
        for r in relations
    }

    assert by_pair[("wall001", "door001")] == "adjacent"
    assert by_pair[("door001", "room001")] == "connects"
    assert by_pair[("door001", "room002")] == "connects"

    # room001とroom002はポリゴンの辺(x=4000)を共有しており、
    # 代表点間の距離ではなく実際の境界共有として隣接判定される。
    assert by_pair[("room001", "room002")] == "adjacent"

    # Wall-Room has no rule, so it must not appear regardless of distance.
    assert ("wall001", "room001") not in by_pair


def test_calculate_relations_ignores_elements_on_different_floors(test_db):
    # 実データ検証で見つかった不具合の再現: 4階建てビルの1階と4階に、平面上
    # (x,y)は全く同じ位置の壁がある場合、高さの範囲(z_min/z_max)が重なって
    # いない限り、平面上どれだけ近くても隣接/接続と判定してはならない。
    test_db.insert_element(
        "wall_floor1", "Wall", "1階の壁",
        json.dumps({}),
        json.dumps({
            "type": "line", "points": [[0, 0], [19200, 0]],
            "z_min": 0, "z_max": 3000,
        }),
    )
    test_db.insert_element(
        "wall_floor4", "Wall", "4階の壁",
        json.dumps({}),
        json.dumps({
            "type": "line", "points": [[0, 0], [19200, 0]],
            "z_min": 9600, "z_max": 12600,
        }),
    )
    test_db.insert_element(
        "door_floor1", "Door", "1階のドア",
        json.dumps({}),
        json.dumps({
            "type": "point", "x": 2000, "y": 0,
            "z_min": 900, "z_max": 3000,
        }),
    )

    relations = calculate_relations()
    by_pair = {
        frozenset((r["source_guid"], r["target_guid"])): r["relation"]
        for r in relations
    }

    assert by_pair[frozenset(("door_floor1", "wall_floor1"))] == "adjacent"
    assert frozenset(("door_floor1", "wall_floor4")) not in by_pair


def test_calculate_relations_ignores_pairs_with_different_floor_index_even_when_z_overlaps(test_db):
    # 実データ検証で見つかった不具合の再現: EV/PS等の縦シャフト系Zoneは
    # 階をまたいでz範囲が「隙間」ではなく「重複」するように登録されている
    # (1階分z:400〜3400・2階分z:3300〜6300、100mm重複)。z-gapは隙間を
    # 前提にしているためこの重複ケースを切り分けられないが、floorIndexが
    # 異なれば無条件で除外されなければならない。
    test_db.insert_element(
        "zone_floor0", "Zone", "EV(1階)",
        json.dumps({"floorIndex": 0}),
        json.dumps({
            "type": "polygon",
            "points": [[0, 0], [2000, 0], [2000, 2000], [0, 2000]],
            "z_min": 400, "z_max": 3400,
        }),
    )
    test_db.insert_element(
        "zone_floor1", "Zone", "EV(2階)",
        json.dumps({"floorIndex": 1}),
        json.dumps({
            "type": "polygon",
            "points": [[0, 0], [2000, 0], [2000, 2000], [0, 2000]],
            "z_min": 3300, "z_max": 6300,
        }),
    )

    assert calculate_relations() == []


def test_calculate_relations_allows_pair_when_floor_index_missing(test_db):
    # floorIndexが片方(または両方)に無い場合(合成データ等)は、従来通り
    # z-gapのみで判定する後方互換を保つ。
    test_db.insert_element(
        "wall_a", "Wall", "壁A",
        json.dumps({}),
        json.dumps({"type": "line", "points": [[0, 0], [1000, 0]], "z_min": 0, "z_max": 3000}),
    )
    test_db.insert_element(
        "door_a", "Door", "ドアA",
        json.dumps({}),
        json.dumps({"type": "point", "x": 500, "y": 0, "z_min": 900, "z_max": 3000}),
    )

    relations = calculate_relations()

    assert len(relations) == 1
    assert relations[0]["relation"] == "adjacent"


def test_calculate_relations_connects_when_z_ranges_overlap(test_db):
    # z_min/z_maxが設定されていても、実際に高さが重なっていれば従来通り
    # 平面距離だけで隣接判定される(3D化で既存の判定を壊していないことの確認)。
    test_db.insert_element(
        "wall_a", "Wall", "壁A",
        json.dumps({}),
        json.dumps({
            "type": "line", "points": [[0, 0], [19200, 0]],
            "z_min": 0, "z_max": 3000,
        }),
    )
    test_db.insert_element(
        "door_a", "Door", "ドアA",
        json.dumps({}),
        json.dumps({
            "type": "point", "x": 2000, "y": 0,
            "z_min": 900, "z_max": 3000,
        }),
    )

    relations = calculate_relations()
    by_pair = {
        (r["source_guid"], r["target_guid"]): r["relation"]
        for r in relations
    }

    assert by_pair[("wall_a", "door_a")] == "adjacent"


def test_calculate_relations_excludes_non_target_types_even_when_touching(test_db):
    # calculate_relations()はSTRtreeへ問い合わせる前に、RELATION_RULESに
    # 一切登場しない型(Column等)を候補から除外する(実データ5699要素の
    # うち対象は1653要素のみに絞る性能最適化)。幾何的に完全に重なって
    # いても関係を持たないことを確認する。
    test_db.insert_element(
        "wall001", "Wall", "壁",
        json.dumps({}),
        json.dumps({"type": "line", "points": [[0, 0], [1000, 0]]}),
    )
    test_db.insert_element(
        "column001", "Column", "柱",
        json.dumps({}),
        json.dumps({"type": "point", "x": 0, "y": 0}),
    )

    assert calculate_relations() == []


def test_calculate_relations_excludes_envelope_zones(test_db):
    # 住戸/区画全体を表す大分類ゾーン("Cタイプ"等)は実室ではないため、
    # 他のZoneを幾何的に包含していても隣接/接続関係を持たない(実データ
    # 検証で発覚: 1つの大分類ゾーンが同じ住戸内の実室十数件を幾何包含し、
    # ドアが同時に多数の部屋へ「接続」と誤判定されていた)。除外の根拠は
    # 命名規則ではなく幾何包含のみ(graph/envelope.py、Archicadのテンプレ
    # ートによってカテゴリの命名規則が異なりうるため)。ここでは実データと
    # 同じパターン(大きな1枚のZoneが、同一階の他Zoneを3件以上・面積の
    # 大部分にわたって包含する)を再現する。
    test_db.insert_element(
        "envelope001", "Zone", "Cタイプ",
        json.dumps({}),
        json.dumps({"type": "polygon", "points": [[0, 0], [10000, 0], [10000, 10000], [0, 10000]]}),
    )
    test_db.insert_element(
        "room001", "Zone", "LD",
        json.dumps({}),
        json.dumps({"type": "polygon", "points": [[0, 0], [3000, 0], [3000, 3000], [0, 3000]]}),
    )
    test_db.insert_element(
        "room002", "Zone", "キッチン",
        json.dumps({}),
        json.dumps({"type": "polygon", "points": [[4000, 0], [7000, 0], [7000, 3000], [4000, 3000]]}),
    )
    test_db.insert_element(
        "room003", "Zone", "トイレ",
        json.dumps({}),
        json.dumps({"type": "polygon", "points": [[0, 4000], [3000, 4000], [3000, 7000], [0, 7000]]}),
    )

    # room001〜003は互いに離れており(200mm閾値超)直接は隣接しないため、
    # envelope001が正しく除外されていれば関係は一つも生まれない。
    assert calculate_relations() == []


def test_calculate_relations_ignores_zone_with_invalid_geometry_for_envelope_check(test_db):
    # 大分類ゾーン判定(graph/envelope.py)の対象探索中に壊れたジオメトリの
    # Zoneに出会ってもクラッシュせず、単に包含判定の対象から外すだけで
    # 処理を継続する。
    test_db.insert_element(
        "wall001", "Wall", "壁",
        json.dumps({}),
        json.dumps({"type": "line", "points": [[0, 0], [1000, 0]]}),
    )
    test_db.insert_element(
        "zone_broken", "Zone", "壊れたゾーン",
        json.dumps({}),
        "not valid json",
    )

    assert calculate_relations() == []


def test_calculate_relations_ignores_non_polygon_zone_for_envelope_check(test_db):
    test_db.insert_element(
        "wall001", "Wall", "壁",
        json.dumps({}),
        json.dumps({"type": "line", "points": [[0, 0], [1000, 0]]}),
    )
    test_db.insert_element(
        "zone_point", "Zone", "点ジオメトリのゾーン",
        json.dumps({}),
        json.dumps({"type": "point", "x": 0, "y": 0}),
    )

    assert calculate_relations() == []


def test_calculate_relations_refines_door_room_connections_using_owner_wall(test_db):
    # ドアのowner壁(archicad_details.ownerElementType/ownerElementId)が
    # 分かる場合、距離ベースの一般的な"connects"(700mm閾値)ではroom3も
    # 誤って接続と判定されてしまう(door1からroom3までの距離は500mm)が、
    # 壁の両側の点-in-ポリゴン判定により、実際に壁を挟むroom1・room2のみが
    # 正しく接続と判定されることを確認する(graph/door_ownership.py参照)。
    test_db.insert_element(
        "wall1", "Wall", "壁",
        json.dumps({"archicad_details": {"begThickness": 0.2, "endThickness": 0.2}}),
        json.dumps({"type": "line", "points": [[1000, 0], [1000, 2000]]}),
    )
    test_db.insert_element(
        "door1", "Door", "ドア",
        json.dumps({"archicad_details": {
            "ownerElementType": "Wall",
            "ownerElementId": {"guid": "wall1"},
        }}),
        json.dumps({"type": "point", "x": 1000, "y": 1000}),
    )
    test_db.insert_element(
        "room1", "Room", "居室A",
        json.dumps({}),
        json.dumps({"type": "polygon", "points": [[0, 0], [1000, 0], [1000, 2000], [0, 2000]]}),
    )
    test_db.insert_element(
        "room2", "Room", "居室B",
        json.dumps({}),
        json.dumps({"type": "polygon", "points": [[1000, 0], [2000, 0], [2000, 2000], [1000, 2000]]}),
    )
    test_db.insert_element(
        "room3", "Room", "近くにあるだけの別の部屋",
        json.dumps({}),
        json.dumps({"type": "polygon", "points": [[1000, 1500], [1500, 1500], [1500, 2000], [1000, 2000]]}),
    )

    relations = calculate_relations()

    door_connects = {
        r["target_guid"] if r["source_guid"] == "door1" else r["source_guid"]
        for r in relations
        if r["relation"] == "connects" and "door1" in (r["source_guid"], r["target_guid"])
    }

    assert door_connects == {"room1", "room2"}


def _element(guid, element_type, properties, geometry):
    return {
        "guid": guid,
        "type": element_type,
        "properties": json.dumps(properties),
        "geometry": json.dumps(geometry) if not isinstance(geometry, str) else geometry,
    }


def test_refine_door_room_connections_filters_relation_with_door_as_target():
    # calculate_relations()内部のSTRtreeループでは(source, target)の向きが
    # 要素の並び順に依存するため、Roomがsource・Doorがtargetになる
    # 組み合わせも正しく除外・置換できることを直接確認する。
    wall = _element(
        "wall1", "Wall",
        {"archicad_details": {"begThickness": 0.2, "endThickness": 0.2}},
        {"type": "line", "points": [[1000, 0], [1000, 2000]]},
    )
    door = _element(
        "door1", "Door",
        {"archicad_details": {
            "ownerElementType": "Wall",
            "ownerElementId": {"guid": "wall1"},
        }},
        {"type": "point", "x": 1000, "y": 1000},
    )
    room1 = _element(
        "room1", "Room", {},
        {"type": "polygon", "points": [[0, 0], [1000, 0], [1000, 2000], [0, 2000]]},
    )
    room2 = _element(
        "room2", "Room", {},
        {"type": "polygon", "points": [[1000, 0], [2000, 0], [2000, 2000], [1000, 2000]]},
    )

    elements = [wall, door, room1, room2]
    relations = [
        {"source_guid": "room1", "target_guid": "door1", "relation": "connects", "distance": 0.0},
        # owner壁情報を持たない(=refined_room_guidsの対象外の)ドアが
        # 絡む"connects"は素通りする(_is_refined_door_room_connects()の
        # どちらの分岐にも該当しない)ことも合わせて確認する。
        {"source_guid": "room1", "target_guid": "door_unrelated", "relation": "connects", "distance": 100.0},
    ]

    refined = _refine_door_room_connections(elements, relations)

    assert ("room1", "door1") not in {(r["source_guid"], r["target_guid"]) for r in refined}
    assert {"source_guid": "room1", "target_guid": "door_unrelated", "relation": "connects", "distance": 100.0} in refined
    door_rooms = {
        r["target_guid"] if r["source_guid"] == "door1" else r["source_guid"]
        for r in refined
        if "door1" in (r["source_guid"], r["target_guid"])
    }
    assert door_rooms == {"room1", "room2"}


def test_refine_door_room_connections_skips_rooms_with_unusable_geometry():
    # 大分類ゾーン除外(_envelope_zone_guids)とは別に、この関数自身が
    # room_recordsを構築する際にも、破損したジオメトリやpolygonでない
    # 形状のRoomをクラッシュせずスキップできることを確認する。
    wall = _element(
        "wall1", "Wall",
        {"archicad_details": {"begThickness": 0.2}},
        {"type": "line", "points": [[1000, 0], [1000, 2000]]},
    )
    door = _element(
        "door1", "Door",
        {"archicad_details": {
            "ownerElementType": "Wall",
            "ownerElementId": {"guid": "wall1"},
        }},
        {"type": "point", "x": 1000, "y": 1000},
    )
    room_broken = _element("room_broken", "Room", {}, "not valid json")
    room_point = _element("room_point", "Room", {}, {"type": "point", "x": 0, "y": 0})
    room_good = _element(
        "room_good", "Room", {},
        {"type": "polygon", "points": [[0, 0], [1000, 0], [1000, 2000], [0, 2000]]},
    )

    elements = [wall, door, room_broken, room_point, room_good]

    refined = _refine_door_room_connections(elements, [])

    door_rooms = {
        r["target_guid"] for r in refined if r["source_guid"] == "door1"
    }
    assert door_rooms == {"room_good"}


def test_refine_door_room_connections_ignores_same_xy_room_on_different_floor():
    # 実データで発覚した不具合の再現: EV/PS等の縦シャフトのように、
    # 平面(x,y)上は同じ位置に複数階のZoneが重なっていると、floorIndexを
    # 見ずに点-in-ポリゴン判定するとドアと別の階のZoneまでヒットして
    # しまう。ドアと同じfloorIndexのRoom/Zoneだけを候補にすべきことを
    # 確認する。
    wall = _element(
        "wall1", "Wall",
        {
            "floorIndex": 1,
            "archicad_details": {"begThickness": 0.2, "endThickness": 0.2},
        },
        {"type": "line", "points": [[1000, 0], [1000, 2000]]},
    )
    door = _element(
        "door1", "Door",
        {
            "floorIndex": 1,
            "archicad_details": {
                "ownerElementType": "Wall",
                "ownerElementId": {"guid": "wall1"},
            },
        },
        {"type": "point", "x": 1000, "y": 1000},
    )
    room_floor1 = _element(
        "room_floor1", "Room", {"floorIndex": 1},
        {"type": "polygon", "points": [[0, 0], [1000, 0], [1000, 2000], [0, 2000]]},
    )
    # room_floor0はドアと同じXY位置に重なる別階(floorIndex=0)の部屋。
    # 点-in-ポリゴン判定だけならヒットしてしまうが、floorIndexが違うので
    # 候補から除外されなければならない。
    room_floor0 = _element(
        "room_floor0", "Room", {"floorIndex": 0},
        {"type": "polygon", "points": [[1000, 0], [2000, 0], [2000, 2000], [1000, 2000]]},
    )

    elements = [wall, door, room_floor1, room_floor0]

    refined = _refine_door_room_connections(elements, [])

    door_rooms = {r["target_guid"] for r in refined if r["source_guid"] == "door1"}
    assert door_rooms == {"room_floor1"}


def test_calculate_relations_falls_back_to_distance_when_door_owner_unknown(sample_elements):
    # ownerElementId等が無いドア(実データでは稀だが、合成データ/古い
    # データではあり得る)は、従来通り距離ベースの"connects"判定のまま
    # 動作する(後方互換)。sample_elementsのdoor001はowner情報を持たない。
    relations = calculate_relations()
    by_pair = {
        frozenset((r["source_guid"], r["target_guid"])): r["relation"]
        for r in relations
    }

    assert by_pair[frozenset(("door001", "room001"))] == "connects"
    assert by_pair[frozenset(("door001", "room002"))] == "connects"


def test_calculate_relations_wall_adjacency_limited_to_owner_wall(test_db):
    # 実データで見つかったパターンの再現: door1はwall1の範囲内に設置されて
    # いる(距離0mm)が、wall1の端点でwall2(別の壁)と接しているため、
    # 距離ベースの一般的な"adjacent"判定(600mm閾値)だとwall2ともヒット
    # してしまう(door1-wall2間は100mm)。owner壁(wall1)以外との
    # "adjacent"は除外されなければならない。
    test_db.insert_element(
        "wall1", "Wall", "壁1",
        json.dumps({}),
        json.dumps({"type": "line", "points": [[0, 0], [2000, 0]]}),
    )
    test_db.insert_element(
        "wall2", "Wall", "角で接する別の壁",
        json.dumps({}),
        json.dumps({"type": "line", "points": [[2000, 0], [2000, 2000]]}),
    )
    test_db.insert_element(
        "door1", "Door", "ドア",
        json.dumps({"archicad_details": {
            "ownerElementType": "Wall",
            "ownerElementId": {"guid": "wall1"},
        }}),
        json.dumps({"type": "polygon", "points": [[1700, -50], [1900, -50], [1900, 50], [1700, 50]]}),
    )

    relations = calculate_relations()

    wall_adjacent = {
        r["target_guid"] if r["source_guid"] == "door1" else r["source_guid"]
        for r in relations
        if r["relation"] == "adjacent" and "door1" in (r["source_guid"], r["target_guid"])
    }

    assert wall_adjacent == {"wall1"}


def test_refine_wall_opening_adjacency_filters_when_opening_is_relation_source():
    # calculate_relations()内部のSTRtreeループでは(source, target)の向きが
    # 要素の並び順に依存するため、Door/Windowがsource・Wallがtargetになる
    # 組み合わせも正しく絞り込めることを直接確認する。
    wall1 = {"guid": "wall1", "type": "Wall", "properties": "{}"}
    wall2 = {"guid": "wall2", "type": "Wall", "properties": "{}"}
    door1 = {
        "guid": "door1", "type": "Door",
        "properties": json.dumps({"archicad_details": {
            "ownerElementType": "Wall",
            "ownerElementId": {"guid": "wall1"},
        }}),
    }
    relations = [
        {"source_guid": "door1", "target_guid": "wall1", "relation": "adjacent", "distance": 0.0},
        {"source_guid": "door1", "target_guid": "wall2", "relation": "adjacent", "distance": 100.0},
    ]

    refined = _refine_wall_opening_adjacency([wall1, wall2, door1], relations)

    assert refined == [
        {"source_guid": "door1", "target_guid": "wall1", "relation": "adjacent", "distance": 0.0},
    ]


def test_refine_wall_opening_adjacency_falls_back_when_owner_unknown():
    # owner壁情報を持たないDoor/Windowは、既存の距離ベース判定のまま
    # 変更しない(フォールバック)。
    wall1 = {"guid": "wall1", "type": "Wall", "properties": "{}"}
    door1 = {"guid": "door1", "type": "Door", "properties": "{}"}
    relations = [
        {"source_guid": "wall1", "target_guid": "door1", "relation": "adjacent", "distance": 300.0},
    ]

    refined = _refine_wall_opening_adjacency([wall1, door1], relations)

    assert refined == relations
