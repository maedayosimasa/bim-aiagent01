def export_graph(graph):

    nodes = []

    edges = []

    for node, data in graph.nodes(data=True):

        nodes.append({

            "id": node,

            "type": data.get("type"),

            "name": data.get("name")

        })

    for source, target, data in graph.edges(data=True):

        edges.append({

            "source": source,

            "target": target,

            "relation": data.get("relation"),

            "distance": data.get("distance")

        })

    return {

        "nodes": nodes,

        "edges": edges

    }