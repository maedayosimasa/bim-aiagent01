from backend.graph.builder import build_graph
from backend.graph.topology import build_topology
from backend.engine.relation_builder import rebuild_connections


def test_build_topology_adds_edges_from_connections(sample_elements):
    rebuild_connections()

    graph = build_graph()
    graph = build_topology(graph)

    assert graph.number_of_edges() == 4
    assert graph.has_edge("wall001", "door001")
    assert graph["wall001"]["door001"]["relation"] == "adjacent"
