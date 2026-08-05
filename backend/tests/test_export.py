from backend.graph.builder import build_graph
from backend.graph.export import export_graph


def test_export_graph_includes_node_centroid(sample_elements):
    graph = build_graph()

    graph_data = export_graph(graph)

    nodes_by_id = {node["id"]: node for node in graph_data["nodes"]}

    # wall001は芯線 [[4000, 0], [4000, 3000]] なので中点は(4000, 1500)。
    assert nodes_by_id["wall001"]["x"] == 4000
    assert nodes_by_id["wall001"]["y"] == 1500

    # door001は代表点(4000, 1500)そのもの。
    assert nodes_by_id["door001"]["x"] == 4000
    assert nodes_by_id["door001"]["y"] == 1500
