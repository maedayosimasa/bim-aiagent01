import json
from datetime import datetime, timezone

from ..database.db import get_elements, save_engine_analysis_result
from ..graph.builder import build_graph
from ..graph.topology import build_topology
from ..graph.analyzer import analyze_graph
from ..graph.export import export_graph
from .relation_builder import rebuild_connections


def analyze_space(model_id: str):

    # SQLiteからBIM要素を取得
    elements = get_elements()

    # 要素間の関係を最新の状態に再計算
    rebuild_connections()

    # NetworkXグラフを構築
    graph = build_graph()
    graph = build_topology(graph)
    graph_info = analyze_graph(graph)
    graph_json = export_graph(graph)

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

        elif element_type in ("Room", "Zone"):
            # Archicadの実データは部屋を"Room"ではなく"Zone"と呼ぶ
            # (archicad_mcp/tapir.pyのモジュールdocstring参照)。
            rooms.append(element)

    # 解析結果
    result = {

        "model_id": model_id,
        "graph": graph_info,
        "graph_data": graph_json,
        
        "elements": {

            "walls": len(walls),
            "doors": len(doors),
            "windows": len(windows),
            "rooms": len(rooms)

        },

        "analysis": {

            "wall_check": f"{len(walls)} walls detected",

            "door_check": f"{len(doors)} doors detected"

        },

        "issues": []

    }

    # 開発時に検証できるよう、この解析結果一式をSQLiteへ保存する。
    # 履歴は積まず、再計算のたびに全削除→1行だけ書き込みで置き換える
    # (sync_from_archicad/rebuild_connectionsと同じ方式)。
    save_engine_analysis_result(
        model_id=model_id,
        computed_at=datetime.now(timezone.utc).isoformat(),
        node_count=graph_info["nodes"],
        edge_count=graph_info["edges"],
        connected=graph_info["connected"],
        wall_count=len(walls),
        door_count=len(doors),
        window_count=len(windows),
        room_count=len(rooms),
        issues=json.dumps(result["issues"]),
        graph_data=json.dumps(graph_json),
    )

    return result