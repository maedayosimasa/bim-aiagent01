def find_nodes_by_type(graph, element_type):

    result = []

    for node, data in graph.nodes(data=True):

        if data.get("type") == element_type:

            result.append(node)

    return result