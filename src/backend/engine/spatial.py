from ..database.db import get_elements
from ..graph.builder import build_graph


def analyze_space(model_id: str):

    # SQLiteからBIM要素を取得
    elements = get_elements()

    # NetworkXグラフを構築
    graph = build_graph()

    # 要素を分類
    walls = []
    doors = []
    windows = []
    rooms = []

    for element in elements:

        element_type = element["type"]

        if element_type == "Wall":
            walls.append(element)

        elif element_type == "Door":
            doors.append(element)

        elif element_type == "Window":
            windows.append(element)

        elif element_type == "Room":
            rooms.append(element)

    # 解析結果
    result = {

        "model_id": model_id,

        "elements": {

            "walls": len(walls),
            "doors": len(doors),
            "windows": len(windows),
            "rooms": len(rooms)

        },

        "graph": {

            "nodes": graph.number_of_nodes(),
            "edges": graph.number_of_edges()

        },

        "analysis": {

            "wall_check": f"{len(walls)} walls detected",

            "door_check": f"{len(doors)} doors detected"

        },

        "issues": []

    }

    return result