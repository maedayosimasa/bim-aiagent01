from .geometry import geometry_from_json
from .relation_rules import RELATION_RULES
from ..database.db import get_elements


def determine_relation(element1, element2, distance):

    type1 = element1["type"]
    type2 = element2["type"]

    rule = RELATION_RULES.get((type1, type2))

    if rule is None:
        rule = RELATION_RULES.get((type2, type1))

    if rule is None:
        return None

    if distance > rule["max_distance"]:
        return None

    return rule["relation"]


def calculate_relations():

    elements = get_elements()

    relations = []

    for i in range(len(elements)):

        for j in range(i + 1, len(elements)):

            e1 = elements[i]
            e2 = elements[j]

            g1 = geometry_from_json(e1["geometry"])
            g2 = geometry_from_json(e2["geometry"])

            distance = g1.distance(g2)

            relation = determine_relation(e1, e2, distance)

            if relation is not None:

                relations.append({
                    "source_guid": e1["guid"],
                    "target_guid": e2["guid"],
                    "relation": relation,
                    "distance": distance,
                })

    return relations