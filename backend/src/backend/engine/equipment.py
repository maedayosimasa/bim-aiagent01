"""設備・家具オブジェクト(Object)が、どの部屋(Room/Zone)に設置されて
いるかを判定する(点-in-ポリゴンによる「収容」関係)。

Object⇔Roomの関係はRELATION_RULES(距離閾値モデル)には無く、別方式が
必要(CLAUDE.md参照)。Objectの座標はTapirのGet3DBoundingBoxes矩形近似
のみで実アウトラインを持たないため、代表点として矩形の重心を使う。
graph/door_ownership.pyと同じ「壁のowner情報を使わず、確実な情報(ここ
では重心)から点-in-ポリゴン判定する」考え方を流用し、floorIndexで階を
揃え、大分類ゾーン(graph/envelope.py)は対象から除外する。
"""

import json
from collections import defaultdict

from ..database.db import get_elements
from ..graph.envelope import find_envelope_zone_guids
from ..graph.geometry import geometry_from_json

# 設備・家具として扱うArchicadライブラリパーツ名のキーワード。実データ
# (bim_cache.db)で確認した家具・設備オブジェクトの名前から抽出した。
# ここに無いObject(通り芯マーカー・レール・生垣等の図面注釈/外構要素)は
# 対象外とする。
EQUIPMENT_KEYWORDS = [
    "エアコン", "室外機", "換気扇",  # 空調設備
    "キッチン", "カップボード", "冷蔵庫",  # キッチン設備
    "UB", "洗面化粧台", "トイレ",  # 水回り設備
    "ソファ", "テーブル", "ベッド", "椅子", "テレビ", "物干金物",  # 家具等
]


def _is_equipment(archicad_details):
    lib_part_name = (archicad_details.get("libPart") or {}).get("name") or ""
    return any(keyword in lib_part_name for keyword in EQUIPMENT_KEYWORDS)


def _room_records_by_floor(elements):
    """全Room/Zone要素を、floorIndexごとに(guid, name, polygon)へグループ化する。

    大分類ゾーン(graph/envelope.py参照)は除外する。
    """

    zone_records = []

    for element in elements:

        if element["type"] not in ("Zone", "Room"):
            continue

        try:
            geom = geometry_from_json(element["geometry"])
        except (ValueError, KeyError, TypeError, json.JSONDecodeError):
            continue

        if geom.geom_type != "Polygon":
            continue

        properties = json.loads(element["properties"] or "{}")

        zone_records.append({
            "guid": element["guid"],
            "name": element["name"],
            "floor": properties.get("floorIndex"),
            "polygon": geom,
        })

    envelope_guids = find_envelope_zone_guids(zone_records)

    by_floor = defaultdict(list)

    for record in zone_records:

        if record["guid"] in envelope_guids:
            continue

        by_floor[record["floor"]].append(
            (record["guid"], record["name"], record["polygon"])
        )

    return by_floor


def _find_containing_room(point, candidate_rooms):
    for room_guid, room_name, polygon in candidate_rooms:
        if polygon.contains(point):
            return room_guid, room_name
    return None


def find_room_equipment():
    """各部屋(Room/Zone)に設置されている設備・家具オブジェクトの一覧を求める。

    どの部屋にも収容されなかった設備(点-in-ポリゴン判定で該当なし、
    Objectのバウンディングボックス近似の誤差や共用部の什器等)は
    unplaced_equipmentとして別途返す。
    """

    elements = get_elements()

    room_records_by_floor = _room_records_by_floor(elements)

    equipment_by_room = {}
    unplaced_equipment = []

    for element in elements:

        if element["type"] != "Object":
            continue

        properties = json.loads(element["properties"] or "{}")
        archicad_details = properties.get("archicad_details") or {}

        if not _is_equipment(archicad_details):
            continue

        lib_part_name = (archicad_details.get("libPart") or {}).get("name")

        try:
            geom = geometry_from_json(element["geometry"])
        except (ValueError, KeyError, TypeError, json.JSONDecodeError):
            continue

        candidate_rooms = room_records_by_floor.get(properties.get("floorIndex"), [])
        match = _find_containing_room(geom.centroid, candidate_rooms)

        item = {
            "guid": element["guid"],
            "name": element["name"],
            "lib_part_name": lib_part_name,
        }

        if match is None:
            unplaced_equipment.append(item)
            continue

        room_guid, room_name = match
        equipment_by_room.setdefault(
            room_guid, {"room_guid": room_guid, "room_name": room_name, "equipment": []}
        )
        equipment_by_room[room_guid]["equipment"].append(item)

    return {
        "rooms": list(equipment_by_room.values()),
        "unplaced_equipment": unplaced_equipment,
    }
