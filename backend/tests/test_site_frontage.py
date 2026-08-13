import json

import pytest

from backend.engine.site_frontage import calculate_site_road_frontage


def test_calculate_site_road_frontage_measures_shared_boundary(test_db):
    # 10m x 10mの敷地の南側(y=0)に4m幅の道路が接する。
    test_db.insert_element(
        "site1", "Zone", "敷地",
        json.dumps({}),
        json.dumps({"type": "polygon", "points": [[0, 0], [10000, 0], [10000, 10000], [0, 10000]]}),
    )
    test_db.insert_element(
        "road1", "Zone", "前面道路",
        json.dumps({}),
        json.dumps({"type": "polygon", "points": [[-1000, -4000], [11000, -4000], [11000, 0], [-1000, 0]]}),
    )

    result = calculate_site_road_frontage()

    assert len(result) == 1
    entry = result[0]
    assert entry["target_guid"] == "site1"
    # 幾何的な突き合わせ許容誤差(300mm)により、南側の辺(10m)よりわずかに
    # 長い近似値になる(角付近で東西の辺も一部拾われるため)。
    assert entry["measured_value"] == pytest.approx(10.6, abs=0.1)
    assert entry["evidence"]["road_details"][0]["road_guid"] == "road1"


def test_calculate_site_road_frontage_sums_multiple_roads(test_db):
    # 角地: 南側と東側それぞれに道路が接する。
    test_db.insert_element(
        "site1", "Zone", "敷地",
        json.dumps({}),
        json.dumps({"type": "polygon", "points": [[0, 0], [10000, 0], [10000, 10000], [0, 10000]]}),
    )
    test_db.insert_element(
        "road_south", "Zone", "前面道路",
        json.dumps({}),
        json.dumps({"type": "polygon", "points": [[-1000, -4000], [11000, -4000], [11000, 0], [-1000, 0]]}),
    )
    test_db.insert_element(
        "road_east", "Zone", "側面道路",
        json.dumps({}),
        json.dumps({"type": "polygon", "points": [[10000, -1000], [14000, -1000], [14000, 11000], [10000, 11000]]}),
    )

    result = calculate_site_road_frontage()

    assert len(result) == 1
    assert len(result[0]["evidence"]["road_details"]) == 2
    # 2つの道路のfrontage_length_mの合計とmeasured_valueが一致する。
    total = sum(d["frontage_length_m"] for d in result[0]["evidence"]["road_details"])
    assert result[0]["measured_value"] == pytest.approx(total)


def test_calculate_site_road_frontage_unresolved_when_no_road(test_db):
    test_db.insert_element(
        "site1", "Zone", "敷地",
        json.dumps({}),
        json.dumps({"type": "polygon", "points": [[0, 0], [10000, 0], [10000, 10000], [0, 10000]]}),
    )

    result = calculate_site_road_frontage()

    assert len(result) == 1
    assert result[0]["measured_value"] is None
    assert result[0]["evidence"]["road_details"] == []


def test_calculate_site_road_frontage_empty_when_no_site(test_db):
    test_db.insert_element(
        "road1", "Zone", "前面道路",
        json.dumps({}),
        json.dumps({"type": "polygon", "points": [[-1000, -4000], [11000, -4000], [11000, 0], [-1000, 0]]}),
    )

    assert calculate_site_road_frontage() == []
