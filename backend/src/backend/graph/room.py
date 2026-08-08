import json


def find_rooms(graph):

    rooms = []

    for node, data in graph.nodes(data=True):

        # Archicadの実データは部屋を"Room"ではなく"Zone"と呼ぶ
        # (archicad_mcp/tapir.pyのモジュールdocstring参照)。
        if data.get("type") not in ("Room", "Zone"):
            continue

        # 住戸/区画全体を表す大分類ゾーン(実室ではない)は除外する。
        # sync_from_archicad()が事前に判定・保存したproperties.
        # zone_is_envelopeを使う(archicad_mcp/tapir.pyの
        # envelope_zone_category_names()参照)。
        properties = json.loads(data.get("properties") or "{}")
        if properties.get("zone_is_envelope"):
            continue

        rooms.append(node)

    return rooms
