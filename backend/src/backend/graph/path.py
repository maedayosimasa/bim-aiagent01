import networkx as nx


def walkable_subgraph(graph):
    """人が通行できる経路のみを残した部分グラフを返す。

    RELATION_RULESで"connects"となるのはRoom/Zone-Doorの組み合わせのみ
    (relation_rules.py参照)。Room-Room/Zoneの"adjacent"は壁を共有して
    いるだけで実際に通り抜けられるとは限らず、Wall-Door/Window/Room-Window
    の"adjacent"も同様に通行可能性を意味しない。そのため経路探索は
    "connects"エッジだけを使う。
    """

    connects_edges = [
        (u, v)
        for u, v, data in graph.edges(data=True)
        if data.get("relation") == "connects"
    ]

    if not connects_edges:
        return nx.Graph()

    return graph.edge_subgraph(connects_edges).copy()


def shortest_route(graph, source, target, weight="distance"):
    """2要素間の最短経路(ノード列)と合計距離(mm)を返す。

    到達不能ならNoneを返す。distanceは各エッジの幾何的な隙間(mm)の合計で、
    部屋内部を歩く実際の歩行距離ではない近似値(evacuation_engine.py参照)。
    """

    if not nx.has_path(graph, source, target):
        return None

    path = nx.shortest_path(graph, source, target, weight=weight)
    length = nx.path_weight(graph, path, weight=weight)

    return {"path": path, "total_distance_mm": length}


def nearest_reachable(graph, sources, weight="distance"):
    """複数の始点(例:外部ドア群)それぞれから、グラフ上の全到達可能ノードへの
    最短経路と距離をまとめて計算する(1回のダイクストラ法で済ませる、
    ノードごとにsources全件を試すより効率的)。

    戻り値: {node: {"path": [...], "total_distance_mm": ...}}
    始点が1つも無い、またはグラフが空なら空dictを返す。
    """

    sources = [s for s in sources if s in graph]

    if not sources:
        return {}

    lengths = nx.multi_source_dijkstra_path_length(graph, sources, weight=weight)
    paths = nx.multi_source_dijkstra_path(graph, sources, weight=weight)

    return {
        node: {"path": paths[node], "total_distance_mm": lengths[node]}
        for node in lengths
    }
