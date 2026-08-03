from backend.graph.builder import build_graph



def test_build_graph_stores_element_attributes(sample_elements):
    graph = build_graph()

    assert graph.nodes["wall001"]["type"] == "Wall"
    assert graph.nodes["wall001"]["name"] == "間仕切壁"
