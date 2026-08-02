def export_graph(graph):

    nodes = []

    edges = []

    for node, data in graph.nodes(data=True):

        nodes.append({

            "id": node,

            "type": data.get("type"),

            "name": data.get("name")

        })

    for source, target in graph.edges():

        edges.append({

            "source": source,

            "target": target

        })

    return {

        "nodes": nodes,

        "edges": edges

    }