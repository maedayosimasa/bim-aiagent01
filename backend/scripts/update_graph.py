from src.backend.graph.relation import calculate_relations

from src.backend.database.db import (
    clear_connections,
    insert_connection
)

clear_connections()

relations = calculate_relations()

for relation in relations:

    insert_connection(
        relation["source_guid"],
        relation["target_guid"],
        relation["relation"],
        relation["distance"]
    )

print("Graph Updated")