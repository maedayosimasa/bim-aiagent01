from src.backend.graph.relation import calculate_relations
from src.backend.database.db import insert_connection

from src.backend.engine.relation_builder import rebuild_connections


relations = rebuild_connections()


print(relations)