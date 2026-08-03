from ..database.db import get_connections



def build_topology(graph):

    connections = get_connections()


    for conn in connections:


        graph.add_edge(

            conn["source_guid"],

            conn["target_guid"],

            relation=conn["relation"],

            distance=conn["distance"]

        )


    return graph