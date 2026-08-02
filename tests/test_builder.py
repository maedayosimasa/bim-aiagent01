from backend.graph.builder import build_graph


def test_build_graph_creates_a_node_per_element(sample_elements):
    graph = build_graph()

    assert graph.number_of_nodes() == 4
    assert set(graph.nodes) == {"wall001", "door001", "room001", "room002"}


def test_build_graph_stores_element_attributes(sample_elements):
    graph = build_graph()

    assert graph.nodes["wall001"]["type"] == "Wall"
    assert graph.nodes["wall001"]["name"] == "間仕切壁"
