import json

import pytest

from backend.engine.building_coverage import calculate_building_area_m2, calculate_building_coverage_ratio


def test_calculate_building_area_includes_wall_footprint(test_db):
    # 10m x 10mの部屋に、部屋の外側(南側)にはみ出す厚い壁を1本追加する。
    # 壁の帯状ポリゴンが部屋面積の外側に加算され、建築面積が部屋面積(100m^2)
    # より大きくなることを確認する(壁芯を考慮しない単純な床面積合計との違い)。
    test_db.insert_element(
        "room1", "Room", "居室",
        json.dumps({}),
        json.dumps({"type": "polygon", "points": [[0, 0], [10000, 0], [10000, 10000], [0, 10000]]}),
    )
    test_db.insert_element(
        "wall1", "Wall", "南壁",
        json.dumps({"archicad_details": {"begThickness": 2.0, "endThickness": 2.0}}),
        json.dumps({"type": "line", "points": [[0, -500], [10000, -500]]}),
    )

    area = calculate_building_area_m2()

    assert area is not None
    assert area > 100.0


def test_calculate_building_area_counts_overhang_beyond_1m_exemption(test_db):
    # 部屋の全周から1500mm張り出すSlab(庇相当)。施行令2条1項2号により
    # 1m未満の張り出しは不算入のため、1500-1000=500mm分の張り出しリング
    # だけが加算される(壁は無し、部屋のみで外郭を構成)。
    test_db.insert_element(
        "room1", "Room", "居室",
        json.dumps({}),
        json.dumps({"type": "polygon", "points": [[0, 0], [10000, 0], [10000, 10000], [0, 10000]]}),
    )
    test_db.insert_element(
        "slab1", "Slab", "庇",
        json.dumps({}),
        json.dumps({
            "type": "polygon",
            "points": [[-1500, -1500], [11500, -1500], [11500, 11500], [-1500, 11500]],
            "z_min": 2500, "z_max": 2600,
        }),
    )

    area = calculate_building_area_m2()

    assert area == pytest.approx(125.86345, abs=0.001)


def test_calculate_building_area_excludes_overhang_under_1m_exemption(test_db):
    # 張り出しが500mm(1m未満)のみのSlabは施行令2条1項2号の緩和規定により
    # 建築面積に一切算入されない。
    test_db.insert_element(
        "room1", "Room", "居室",
        json.dumps({}),
        json.dumps({"type": "polygon", "points": [[0, 0], [10000, 0], [10000, 10000], [0, 10000]]}),
    )
    test_db.insert_element(
        "slab1", "Slab", "庇",
        json.dumps({}),
        json.dumps({
            "type": "polygon",
            "points": [[-500, -500], [10500, -500], [10500, 10500], [-500, 10500]],
            "z_min": 2500, "z_max": 2600,
        }),
    )

    area = calculate_building_area_m2()

    assert area == pytest.approx(100.0)


def test_calculate_building_area_excludes_site_and_road_zones(test_db):
    test_db.insert_element(
        "site1", "Zone", "敷地",
        json.dumps({}),
        json.dumps({"type": "polygon", "points": [[-2000, -2000], [12000, -2000], [12000, 12000], [-2000, 12000]]}),
    )
    test_db.insert_element(
        "room1", "Room", "居室",
        json.dumps({}),
        json.dumps({"type": "polygon", "points": [[0, 0], [10000, 0], [10000, 10000], [0, 10000]]}),
    )

    area = calculate_building_area_m2()

    # 敷地Zone(196m^2)が建築面積に混入していれば100m^2を大きく超える。
    assert area == pytest.approx(100.0)


def test_calculate_building_area_none_when_no_data(test_db):
    assert calculate_building_area_m2() is None


def test_calculate_building_coverage_ratio_returns_empty_when_no_site(test_db):
    test_db.insert_element(
        "room1", "Room", "居室",
        json.dumps({}),
        json.dumps({"type": "polygon", "points": [[0, 0], [10000, 0], [10000, 10000], [0, 10000]]}),
    )

    assert calculate_building_coverage_ratio() == []


def test_calculate_building_coverage_ratio_computes_ratio(test_db):
    test_db.insert_element(
        "site1", "Zone", "敷地",
        json.dumps({}),
        json.dumps({"type": "polygon", "points": [[-2000, -2000], [12000, -2000], [12000, 12000], [-2000, 12000]]}),
    )
    test_db.insert_element(
        "room1", "Room", "居室",
        json.dumps({}),
        json.dumps({"type": "polygon", "points": [[0, 0], [10000, 0], [10000, 10000], [0, 10000]]}),
    )

    result = calculate_building_coverage_ratio()

    assert len(result) == 1
    entry = result[0]
    assert entry["target_guid"] == "site1"
    assert entry["evidence"]["building_area_m2"] == pytest.approx(100.0)
    # 敷地 = 14m x 14m = 196m^2
    assert entry["evidence"]["site_area_m2"] == pytest.approx(196.0)
    assert entry["measured_value"] == pytest.approx(100.0 / 196.0)
