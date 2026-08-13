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


_MM_PER_M = 1_000

REFERENCE_MAX_EVACUATION_WALK_DISTANCE_M = 30.0
EVACUATION_WALK_DISTANCE_DISCLAIMER = (
    "参考値です。建築基準法施行令120条の歩行距離制限(構造・用途により"
    "30m〜60mと幅がある)のうち、最も厳しい値(30m)を一律の目安として"
    "使っています。実際の適用値は建物の耐火構造区分・主要用途・避難階段の"
    "配置により異なります。距離はRoom/Zone-Doorのグラフ上の幾何的な隙間の"
    "合計であり、部屋内部を実際に歩く経路長そのものではありません。複数階を"
    "またぐ経路(階段の接続)は未対応です。法的な適合を保証するものではありません。"
)


def compute_evacuation_walking_distances() -> list[dict]:
    """RULE_CHECK_REGISTRY用: 各部屋から最寄り外部ドアまでの歩行距離(m)を返す。

    find_evacuation_routes()の結果をそのまま使う(新たな幾何計算はしない)。
    到達不能な部屋(reachable=False)はmeasured_value=None(判定不能)にする
    ——「歩行距離が長すぎてFAIL」と「そもそも経路が無い」は別の問題であり、
    後者は既にengine_evacuation_routes_tool/accessibility.pyで別途報告される。
    """
    result = find_evacuation_routes()

    items = []
    for route in result["routes"]:
        distance_mm = route["total_distance_mm"]
        items.append({
            "target_guid": route["room_guid"],
            "target_name": route["room_name"],
            "measured_value": distance_mm / _MM_PER_M if distance_mm is not None else None,
            "evidence": {
                "reachable": route["reachable"],
                "nearest_exit_door_guid": route["nearest_exit_door_guid"],
            },
        })

    return items
