import networkx as nx

from ..database.db import get_elements


def build_graph():

    graph = nx.Graph()

    elements = get_elements()

    for element in elements:

        graph.add_node(
            element["guid"],
            type=element["type"],
            name=element["name"],
            properties=element["properties"],
            geometry=element["geometry"]
        )

    return graph