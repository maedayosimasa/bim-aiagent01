from ..graph.relation import calculate_relations

from ..database.db import (
    clear_connections,
    insert_connection
)


def rebuild_connections():

    print("=== Rebuilding Connections ===")

    clear_connections()

    relations = calculate_relations()

    for relation in relations:

        insert_connection(

            relation["source_guid"],

            relation["target_guid"],

            relation["relation"],

            relation["distance"]

        )

    print(
        f"{len(relations)} connections created."
    )

    return relations