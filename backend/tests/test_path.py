import networkx as nx

from backend.graph.path import walkable_subgraph, shortest_route, nearest_reachable


def _make_graph():
    graph = nx.Graph()
    graph.add_node("room1", type="Room")
    graph.add_node("room2", type="Room")
    graph.add_node("door1", type="Door")
    graph.add_node("wall1", type="Wall")
    graph.add_edge("room1", "door1", relation="connects", distance=500)
    graph.add_edge("door1", "room2", relation="connects", distance=300)
    # 壁を共有しているだけ(adjacent)は通行可能とはみなさない。
    graph.add_edge("room1", "room2", relation="adjacent", distance=0)
    graph.add_edge("wall1", "door1", relation="adjacent", distance=0)
    return graph


def test_walkable_subgraph_keeps_only_connects_edges():
    walkable = walkable_subgraph(_make_graph())

    edges = {frozenset(edge) for edge in walkable.edges()}
    assert edges == {frozenset(("room1", "door1")), frozenset(("door1", "room2"))}
    assert "wall1" not in walkable


def test_walkable_subgraph_empty_when_no_connects_edges():
    graph = nx.Graph()
    graph.add_edge("a", "b", relation="adjacent", distance=0)

    walkable = walkable_subgraph(graph)

    assert walkable.number_of_nodes() == 0


def test_shortest_route_returns_path_and_distance():
    walkable = walkable_subgraph(_make_graph())

    result = shortest_route(walkable, "room1", "room2")

    assert result["path"] == ["room1", "door1", "room2"]
    assert result["total_distance_mm"] == 800


def test_shortest_route_returns_none_when_unreachable():
    graph = nx.Graph()
    graph.add_node("a")
    graph.add_node("b")

    assert shortest_route(graph, "a", "b") is None


def test_nearest_reachable_computes_all_nodes_in_one_pass():
    walkable = walkable_subgraph(_make_graph())

    reachable = nearest_reachable(walkable, ["door1"])

    assert reachable["room1"]["total_distance_mm"] == 500
    assert reachable["room1"]["path"] == ["door1", "room1"]
    assert reachable["room2"]["total_distance_mm"] == 300


def test_nearest_reachable_empty_sources_returns_empty_dict():
    walkable = walkable_subgraph(_make_graph())

    assert nearest_reachable(walkable, []) == {}


def test_nearest_reachable_ignores_sources_not_in_graph():
    walkable = walkable_subgraph(_make_graph())

    reachable = nearest_reachable(walkable, ["door1", "does-not-exist"])

    assert "room1" in reachable
