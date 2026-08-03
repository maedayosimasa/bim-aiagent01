import networkx as nx

from backend.graph.builder import build_graph
from backend.graph.topology import build_topology
from backend.graph.analyzer import analyze_graph
from backend.engine.relation_builder import rebuild_connections


def test_analyze_graph_reports_connected_layout(sample_elements):
    rebuild_connections()

    graph = build_graph()
    graph = build_topology(graph)

    info = analyze_graph(graph)

    assert info == {"nodes": 4, "edges": 4, "connected": True}


def test_analyze_graph_on_empty_graph():
    info = analyze_graph(nx.Graph())

    assert info == {"nodes": 0, "edges": 0, "connected": False}
