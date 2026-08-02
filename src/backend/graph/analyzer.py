import networkx as nx



def analyze_graph(graph):

    return {

        "nodes": graph.number_of_nodes(),

        "edges": graph.number_of_edges(),

        "connected":
            nx.is_connected(graph)
            if graph.number_of_nodes() > 0
            else False

    }



def graph_summary(graph):

    rooms = []

    for node, data in graph.nodes(data=True):

        if data.get("type") == "Room":

            rooms.append(node)


    return {

        "nodes":
            graph.number_of_nodes(),

        "edges":
            graph.number_of_edges(),

        "rooms":
            rooms

    }