RELATION_RULES = {
    ("Wall", "Door"): {
        "relation": "adjacent",
        "max_distance": 600,
    },
    # Windowも壁に埋め込まれる開口部という点でDoorと同じ幾何なので、同じ
    # 閾値を使う。以前はWindowに対応するルールが一つも無く、実データ検証で
    # 窓が(壁とも部屋とも)必ず孤立要素として検出される不具合になっていた。
    ("Wall", "Window"): {
        "relation": "adjacent",
        "max_distance": 600,
    },
    ("Room", "Door"): {
        "relation": "connects",
        "max_distance": 700,
    },
    # 窓は人が通り抜けられる開口ではないため、Room-Doorと違い"connects"
    # (通行可能な接続)ではなく"adjacent"(採光/開口の有無を示す構造的な
    # 隣接)として扱う。
    ("Room", "Window"): {
        "relation": "adjacent",
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
    ("Zone", "Window"): {
        "relation": "adjacent",
        "max_distance": 700,
    },
    ("Zone", "Zone"): {
        "relation": "adjacent",
        "max_distance": 200,
    },
    # Wall-Room/ZoneはあえてOFFのまま: 全ての壁がその壁が属する部屋と
    # ほぼ確実に隣接判定されてしまい(閾値をどう設定しても)、情報量の乏しい
    # エッジでグラフが埋まるため。部屋の接続情報はRoom/Zone-Door経由で
    # 表現する(test_relation.pyのtest_calculate_relations_uses_rules_per_type_pair参照)。
}