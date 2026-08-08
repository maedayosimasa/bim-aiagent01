import json

from .envelope import find_envelope_zone_guids
from .geometry import geometry_from_json


def _zone_records(graph):
    records = []

    for node, data in graph.nodes(data=True):

        if data.get("type") not in ("Room", "Zone"):
            continue

        try:
            geom = geometry_from_json(data.get("geometry"))
        except (ValueError, KeyError, TypeError, json.JSONDecodeError):
            continue

        if geom.geom_type != "Polygon":
            continue

        properties = json.loads(data.get("properties") or "{}")

        records.append({
            "guid": node,
            "floor": properties.get("floorIndex"),
            "polygon": geom,
        })

    return records


def find_rooms(graph):

    # 住戸/区画全体を表す大分類ゾーン(実室ではない)は除外する
    # (graph/envelope.py参照。幾何包含のみを根拠とし、命名規則には
    # 依存しない)。
    envelope_guids = find_envelope_zone_guids(_zone_records(graph))

    rooms = []

    for node, data in graph.nodes(data=True):

        # Archicadの実データは部屋を"Room"ではなく"Zone"と呼ぶ
        # (archicad_mcp/tapir.pyのモジュールdocstring参照)。
        if data.get("type") not in ("Room", "Zone"):
            continue

        if node in envelope_guids:
            continue

        rooms.append(node)

    return rooms
