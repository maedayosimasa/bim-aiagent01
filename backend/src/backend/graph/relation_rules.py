RELATION_RULES = {
    ("Wall", "Door"): {
        "relation": "adjacent",
        "max_distance": 600,
    },
    ("Room", "Door"): {
        "relation": "connects",
        "max_distance": 700,
    },
    ("Room", "Room"): {
        "relation": "adjacent",
        "max_distance": 200,
    },
    # Archicad's own element type for a room is "Zone", not "Room" (see
    # archicad_mcp/tapir.py module docstring). sync_from_archicad() keeps
    # Archicad's native type names as-is rather than renaming them, so
    # real synced data needs these mirrored rules to produce any
    # Room/Zone-related relations at all.
    ("Zone", "Door"): {
        "relation": "connects",
        "max_distance": 700,
    },
    ("Zone", "Zone"): {
        "relation": "adjacent",
        "max_distance": 200,
    },
}