import json

from backend.engine.equipment import find_room_equipment


def test_find_room_equipment_matches_object_by_containment(test_db):
    test_db.insert_element(
        "room1", "Room", "洋室",
        json.dumps({"floorIndex": 0}),
        json.dumps({"type": "polygon", "points": [[0, 0], [2000, 0], [2000, 2000], [0, 2000]]}),
    )
    test_db.insert_element(
        "aircon1", "Object", "壁掛けエアコン",
        json.dumps({"floorIndex": 0, "archicad_details": {"libPart": {"name": "壁取付エアコン"}}}),
        json.dumps({"type": "polygon", "points": [[900, 900], [1100, 900], [1100, 1100], [900, 1100]]}),
    )
    # 設備キーワードに一致しないObject(図面注釈)は対象外。
    test_db.insert_element(
        "marker1", "Object", "通り芯マーカー",
        json.dumps({"floorIndex": 0, "archicad_details": {"libPart": {"name": "標準通り芯マーカー"}}}),
        json.dumps({"type": "polygon", "points": [[900, 900], [1100, 900], [1100, 1100], [900, 1100]]}),
    )

    result = find_room_equipment()

    assert len(result["rooms"]) == 1
    room = result["rooms"][0]
    assert room["room_guid"] == "room1"
    assert [e["lib_part_name"] for e in room["equipment"]] == ["壁取付エアコン"]
    assert result["unplaced_equipment"] == []


def test_find_room_equipment_reports_unplaced_equipment(test_db):
    # どのRoom/Zoneポリゴンにも含まれない設備。
    test_db.insert_element(
        "fridge1", "Object", "冷蔵庫",
        json.dumps({"floorIndex": 0, "archicad_details": {"libPart": {"name": "冷蔵庫"}}}),
        json.dumps({"type": "polygon", "points": [[900, 900], [1100, 900], [1100, 1100], [900, 1100]]}),
    )

    result = find_room_equipment()

    assert result["rooms"] == []
    assert [e["lib_part_name"] for e in result["unplaced_equipment"]] == ["冷蔵庫"]


def test_find_room_equipment_ignores_room_on_different_floor(test_db):
    # 平面上は重なっていても階が違えば収容関係とみなさない(縦シャフト
    # 系Zoneの階またぎ重複対策と同じ考え方、graph/relation.py参照)。
    test_db.insert_element(
        "room1", "Room", "洋室(1階)",
        json.dumps({"floorIndex": 0}),
        json.dumps({"type": "polygon", "points": [[0, 0], [2000, 0], [2000, 2000], [0, 2000]]}),
    )
    test_db.insert_element(
        "sofa1", "Object", "ソファ",
        json.dumps({"floorIndex": 1, "archicad_details": {"libPart": {"name": "ソファ"}}}),
        json.dumps({"type": "polygon", "points": [[900, 900], [1100, 900], [1100, 1100], [900, 1100]]}),
    )

    result = find_room_equipment()

    assert result["rooms"] == []
    assert [e["lib_part_name"] for e in result["unplaced_equipment"]] == ["ソファ"]


def test_find_room_equipment_excludes_envelope_zone_as_room(test_db):
    # 大分類ゾーン(住戸全体)は実室として扱わない(graph/envelope.py参照)。
    test_db.insert_element(
        "envelope1", "Zone", "Aタイプ",
        json.dumps({"floorIndex": 0}),
        json.dumps({"type": "polygon", "points": [[0, 0], [10000, 0], [10000, 10000], [0, 10000]]}),
    )
    test_db.insert_element(
        "room1", "Zone", "LD",
        json.dumps({"floorIndex": 0}),
        json.dumps({"type": "polygon", "points": [[0, 0], [3000, 0], [3000, 3000], [0, 3000]]}),
    )
    test_db.insert_element(
        "room2", "Zone", "キッチン",
        json.dumps({"floorIndex": 0}),
        json.dumps({"type": "polygon", "points": [[4000, 0], [7000, 0], [7000, 3000], [4000, 3000]]}),
    )
    test_db.insert_element(
        "room3", "Zone", "トイレ",
        json.dumps({"floorIndex": 0}),
        json.dumps({"type": "polygon", "points": [[0, 4000], [3000, 4000], [3000, 7000], [0, 7000]]}),
    )
    test_db.insert_element(
        "tv1", "Object", "テレビ",
        json.dumps({"floorIndex": 0, "archicad_details": {"libPart": {"name": "フラットパネルテレビ"}}}),
        json.dumps({"type": "polygon", "points": [[900, 900], [1100, 900], [1100, 1100], [900, 1100]]}),
    )

    result = find_room_equipment()

    assert [r["room_guid"] for r in result["rooms"]] == ["room1"]


def test_find_room_equipment_returns_empty_when_no_objects(test_db):
    assert find_room_equipment() == {"rooms": [], "unplaced_equipment": []}


def test_find_room_equipment_skips_zone_with_unusable_geometry(test_db):
    test_db.insert_element(
        "zone_broken", "Zone", "壊れたゾーン",
        json.dumps({"floorIndex": 0}),
        "not valid json",
    )
    test_db.insert_element(
        "zone_point", "Zone", "点ジオメトリのゾーン",
        json.dumps({"floorIndex": 0}),
        json.dumps({"type": "point", "x": 0, "y": 0}),
    )
    test_db.insert_element(
        "room1", "Room", "洋室",
        json.dumps({"floorIndex": 0}),
        json.dumps({"type": "polygon", "points": [[0, 0], [2000, 0], [2000, 2000], [0, 2000]]}),
    )
    test_db.insert_element(
        "aircon1", "Object", "壁掛けエアコン",
        json.dumps({"floorIndex": 0, "archicad_details": {"libPart": {"name": "壁取付エアコン"}}}),
        json.dumps({"type": "polygon", "points": [[900, 900], [1100, 900], [1100, 1100], [900, 1100]]}),
    )

    result = find_room_equipment()

    assert [r["room_guid"] for r in result["rooms"]] == ["room1"]


def test_find_room_equipment_skips_object_with_unusable_geometry(test_db):
    test_db.insert_element(
        "room1", "Room", "洋室",
        json.dumps({"floorIndex": 0}),
        json.dumps({"type": "polygon", "points": [[0, 0], [2000, 0], [2000, 2000], [0, 2000]]}),
    )
    test_db.insert_element(
        "broken_object", "Object", "壊れたオブジェクト",
        json.dumps({"floorIndex": 0, "archicad_details": {"libPart": {"name": "冷蔵庫"}}}),
        "not valid json",
    )

    result = find_room_equipment()

    assert result == {"rooms": [], "unplaced_equipment": []}
