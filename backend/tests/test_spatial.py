from backend.graph.builder import build_graph
from backend.graph.room import find_rooms
from backend.graph.search import find_nodes_by_type


def test_find_rooms(sample_elements):
    graph = build_graph()

    assert set(find_rooms(graph)) == {"room001", "room002"}


def test_find_nodes_by_type(sample_elements):
    graph = build_graph()

    assert set(find_nodes_by_type(graph, "Door")) == {"door001"}
    assert find_nodes_by_type(graph, "Window") == []
