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


# RELATION_RULESに登場する要素タイプの集合。Column/Beam/Slabなど関係計算の
# 対象外タイプはRELATION_RULESにどの組み合わせでも登場しないため、絶対に
# 関係(エッジ)を持ち得ない。graph/relation.pyのcalculate_relations()は
# これを使って候補から事前に除外し(実データ5699要素のうち対象は1653要素
# のみ)、graph/analyzer.pyはconnected判定や孤立要素検出の対象タイプ判定に
# 使う。両モジュールが別々に定義すると値がずれた際に検出漏れが起きるため
# (Windowにルールが無かった過去の不具合と同種)、ここで一元管理する。
RELATION_TARGET_TYPES = {element_type for pair in RELATION_RULES for element_type in pair}