from .geometry import centroid_from_geometry


def export_graph(graph):

    nodes = []

    edges = []

    for node, data in graph.nodes(data=True):

        centroid = centroid_from_geometry(data.get("geometry"))

        nodes.append({

            "id": node,

            "type": data.get("type"),

            "name": data.get("name"),

            "x": centroid[0] if centroid else None,

            "y": centroid[1] if centroid else None

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