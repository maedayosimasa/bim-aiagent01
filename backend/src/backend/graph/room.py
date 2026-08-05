def find_rooms(graph):

    rooms=[]


    for node,data in graph.nodes(data=True):

        # Archicadの実データは部屋を"Room"ではなく"Zone"と呼ぶ
        # (archicad_mcp/tapir.pyのモジュールdocstring参照)。
        if data.get("type") in ("Room", "Zone"):

            rooms.append(node)


    return rooms