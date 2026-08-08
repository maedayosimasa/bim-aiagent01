from shapely.geometry import Polygon

from backend.graph.envelope import find_envelope_zone_guids


def _record(guid, floor, points):
    return {"guid": guid, "floor": floor, "polygon": Polygon(points)}


def test_detects_zone_containing_three_or_more_others():
    records = [
        _record("envelope", 0, [[0, 0], [10000, 0], [10000, 10000], [0, 10000]]),
        _record("room1", 0, [[0, 0], [3000, 0], [3000, 3000], [0, 3000]]),
        _record("room2", 0, [[4000, 0], [7000, 0], [7000, 3000], [4000, 3000]]),
        _record("room3", 0, [[0, 4000], [3000, 4000], [3000, 7000], [0, 7000]]),
    ]

    assert find_envelope_zone_guids(records) == {"envelope"}


def test_ignores_containment_below_minimum_count():
    # 包含している他Zoneが2件のみ(MIN_CONTAINED_COUNT=3未満)なら、
    # 単なる部屋同士のわずかな重なりとみなし包括ゾーンと判定しない。
    records = [
        _record("big", 0, [[0, 0], [10000, 0], [10000, 10000], [0, 10000]]),
        _record("room1", 0, [[0, 0], [3000, 0], [3000, 3000], [0, 3000]]),
        _record("room2", 0, [[4000, 0], [7000, 0], [7000, 3000], [4000, 3000]]),
    ]

    assert find_envelope_zone_guids(records) == set()


def test_ignores_similarly_sized_overlapping_zones():
    # ほぼ同面積のZone同士が偶然大きく重なっただけのケース(実データの
    # 異常/重複)は、どちらも「包含している側」とはみなさない
    # (MIN_SIZE_RATIO)。
    records = [
        _record(f"z{i}", 0, [[0, 0], [3000, 0], [3000, 3000], [0, 3000]])
        for i in range(4)
    ]

    assert find_envelope_zone_guids(records) == set()


def test_ignores_containment_across_different_floors():
    # 階が違えば同じ座標に重なっていても比較しない(縦シャフト系Zoneの
    # 階またぎ重複対策)。
    records = [
        _record("envelope", 0, [[0, 0], [10000, 0], [10000, 10000], [0, 10000]]),
        _record("room1", 1, [[0, 0], [3000, 0], [3000, 3000], [0, 3000]]),
        _record("room2", 1, [[4000, 0], [7000, 0], [7000, 3000], [4000, 3000]]),
        _record("room3", 1, [[0, 4000], [3000, 4000], [3000, 7000], [0, 7000]]),
    ]

    assert find_envelope_zone_guids(records) == set()


def test_ignores_degenerate_zero_area_polygon():
    records = [
        _record("degenerate", 0, [[0, 0], [1000, 0], [2000, 0], [0, 0]]),
        _record("room1", 0, [[0, 0], [3000, 0], [3000, 3000], [0, 3000]]),
        _record("room2", 0, [[4000, 0], [7000, 0], [7000, 3000], [4000, 3000]]),
        _record("room3", 0, [[0, 4000], [3000, 4000], [3000, 7000], [0, 7000]]),
    ]

    assert find_envelope_zone_guids(records) == set()


def test_empty_input_returns_empty_set():
    assert find_envelope_zone_guids([]) == set()
