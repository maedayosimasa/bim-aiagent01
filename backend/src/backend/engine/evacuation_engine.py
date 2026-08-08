from ..graph.builder import build_graph
from ..graph.topology import build_topology
from ..graph.room import find_rooms
from ..graph.path import walkable_subgraph, nearest_reachable
from .relation_builder import rebuild_connections


def find_exterior_doors(walkable):
    """Room/Zoneに1つしか"connects"していないドアを外部ドアとみなす簡易判定。

    実データにはArchicad上で「これは外部ドアである」というフラグが無い
    (CLAUDE.md参照)。屋内のドアは通常2つの部屋(またはホール等)を繋ぐ
    のに対し、外部に面するドアは屋外側に対応するRoom/Zoneが存在しない
    ため片側の1部屋にしか繋がらない、という前提のヒューリスティック。
    実際には「行き止まりの物置」等、外部に面していないのに1部屋にしか
    繋がらないドアも誤って外部ドア扱いになり得る(既知の限界)。
    """

    return [
        node
        for node, data in walkable.nodes(data=True)
        if data.get("type") == "Door" and walkable.degree(node) == 1
    ]


def find_evacuation_routes():
    """各部屋(Room/Zone)から最寄りの外部ドアまでの避難経路を求める(単一階のみ)。

    経路はRoom/Zone-Doorの"connects"エッジのみを辿る(壁を共有している
    だけの"adjacent"は通行可能性を意味しない、graph/path.py参照)。
    距離は各エッジの幾何的な隙間(mm)の合計であり、部屋内部を実際に歩く
    距離ではない近似値。複数階建物の階をまたぐ避難経路(階段の接続)は
    未対応(CLAUDE.md参照、実データにStair要素は存在するが関係ルール
    未整備)。
    """

    rebuild_connections()

    graph = build_graph()
    graph = build_topology(graph)

    walkable = walkable_subgraph(graph)
    exterior_doors = find_exterior_doors(walkable)

    reachable = nearest_reachable(walkable, exterior_doors)

    rooms = find_rooms(graph)

    routes = []

    for room in rooms:
        name = graph.nodes[room].get("name")
        hit = reachable.get(room)

        if hit is None:
            routes.append({
                "room_guid": room,
                "room_name": name,
                "reachable": False,
                "nearest_exit_door_guid": None,
                "route": None,
                "total_distance_mm": None,
            })
            continue

        routes.append({
            "room_guid": room,
            "room_name": name,
            "reachable": True,
            "nearest_exit_door_guid": hit["path"][0],
            "route": hit["path"],
            "total_distance_mm": hit["total_distance_mm"],
        })

    return {
        "exterior_doors": exterior_doors,
        "routes": routes,
    }
