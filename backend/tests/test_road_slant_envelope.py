import json

import pytest

from backend.engine.road_slant_envelope import (
    calculate_road_slant_compliance,
    calculate_road_slant_envelope,
)


def _insert_site_and_road(test_db, site_points, road_points):
    test_db.insert_element(
        "site1", "Zone", "敷地",
        json.dumps({}),
        json.dumps({"type": "polygon", "points": site_points}),
    )
    test_db.insert_element(
        "road1", "Zone", "前面道路",
        json.dumps({}),
        json.dumps({"type": "polygon", "points": road_points}),
    )


def _insert_wall(test_db, guid, points, z_max, z_min=0):
    test_db.insert_element(
        guid, "Wall", guid,
        json.dumps({}),
        json.dumps({"type": "line", "points": points, "z_min": z_min, "z_max": z_max}),
    )


def test_calculate_road_slant_envelope_computes_height_per_vertex(test_db, monkeypatch):
    monkeypatch.setenv("LAND_USE_CATEGORY", "residential")
    # 10m x 10mの敷地の南側(y=0)に4m幅の道路。道路の反対側境界線はy=-4000。
    _insert_site_and_road(
        test_db,
        site_points=[[0, 0], [10000, 0], [10000, 10000], [0, 10000]],
        road_points=[[-1000, -4000], [11000, -4000], [11000, 0], [-1000, 0]],
    )

    result = calculate_road_slant_envelope()

    assert len(result) == 1
    entry = result[0]
    assert entry["resolved"] is True
    assert entry["land_use_category"] == "residential"
    assert entry["gradient"] == 1.25
    assert entry["applicable_distance_m"] == 20.0

    by_xy = {(v["x"], v["y"]): v["z_m"] for v in entry["vertices"]}
    # (0,0): 道路反対側境界線(y=-4000)まで4m → 高さ = 1.25 * 4 = 5.0m
    assert by_xy[(0, 0)] == pytest.approx(5.0)
    # (0,10000): 4m+10m=14m → 高さ = 1.25 * 14 = 17.5m
    assert by_xy[(0, 10000)] == pytest.approx(17.5)


def test_calculate_road_slant_envelope_caps_at_applicable_distance(test_db, monkeypatch):
    monkeypatch.setenv("LAND_USE_CATEGORY", "residential")
    # 敷地の奥行きを50mにして、適用距離20mを超える頂点を作る。
    _insert_site_and_road(
        test_db,
        site_points=[[0, 0], [10000, 0], [10000, 50000], [0, 50000]],
        road_points=[[-1000, -4000], [11000, -4000], [11000, 0], [-1000, 0]],
    )

    result = calculate_road_slant_envelope()
    by_xy = {(v["x"], v["y"]): v["z_m"] for v in result[0]["vertices"]}

    # (0,50000): 距離54m > 適用距離20mなので、1.25*20=25.0mで頭打ちになる。
    assert by_xy[(0, 50000)] == pytest.approx(25.0)


def test_calculate_road_slant_envelope_gradient_by_land_use_category(test_db, monkeypatch):
    _insert_site_and_road(
        test_db,
        site_points=[[0, 0], [10000, 0], [10000, 10000], [0, 10000]],
        road_points=[[-1000, -4000], [11000, -4000], [11000, 0], [-1000, 0]],
    )

    residential = calculate_road_slant_envelope("residential")
    commercial = calculate_road_slant_envelope("commercial")
    industrial = calculate_road_slant_envelope("industrial")

    assert residential[0]["gradient"] == 1.25
    assert commercial[0]["gradient"] == 1.5
    assert industrial[0]["gradient"] == 1.5


def test_calculate_road_slant_envelope_unresolved_when_no_road(test_db):
    test_db.insert_element(
        "site1", "Zone", "敷地",
        json.dumps({}),
        json.dumps({"type": "polygon", "points": [[0, 0], [10000, 0], [10000, 10000], [0, 10000]]}),
    )

    result = calculate_road_slant_envelope("residential")

    assert len(result) == 1
    assert result[0]["resolved"] is False
    assert result[0]["vertices"] == []


def test_calculate_road_slant_envelope_empty_when_no_site(test_db):
    test_db.insert_element(
        "road1", "Zone", "前面道路",
        json.dumps({}),
        json.dumps({"type": "polygon", "points": [[-1000, -4000], [11000, -4000], [11000, 0], [-1000, 0]]}),
    )

    assert calculate_road_slant_envelope("residential") == []


def test_calculate_road_slant_envelope_raises_for_unknown_land_use_category(test_db):
    test_db.insert_element(
        "site1", "Zone", "敷地",
        json.dumps({}),
        json.dumps({"type": "polygon", "points": [[0, 0], [10000, 0], [10000, 10000], [0, 10000]]}),
    )

    with pytest.raises(ValueError):
        calculate_road_slant_envelope("unknown_category")


def test_calculate_road_slant_compliance_passes_when_within_limit(test_db, monkeypatch):
    monkeypatch.setenv("LAND_USE_CATEGORY", "residential")
    _insert_site_and_road(
        test_db,
        site_points=[[0, 0], [10000, 0], [10000, 10000], [0, 10000]],
        road_points=[[-1000, -4000], [11000, -4000], [11000, 0], [-1000, 0]],
    )
    # (150, 1000)での高さ上限 = 1.25 * (1000+4000)/1000 = 6.25m。5.0mは範囲内。
    _insert_wall(test_db, "wall1", [[100, 1000], [200, 1000]], z_max=5000)

    result = calculate_road_slant_compliance()

    assert len(result) == 1
    assert result[0]["measured_value"] == pytest.approx(5.0 - 6.25)
    assert result[0]["measured_value"] <= 0


def test_calculate_road_slant_compliance_fails_when_exceeding_limit(test_db, monkeypatch):
    monkeypatch.setenv("LAND_USE_CATEGORY", "residential")
    _insert_site_and_road(
        test_db,
        site_points=[[0, 0], [10000, 0], [10000, 10000], [0, 10000]],
        road_points=[[-1000, -4000], [11000, -4000], [11000, 0], [-1000, 0]],
    )
    # 高さ上限6.25mに対し、8.0mは超過。
    _insert_wall(test_db, "wall1", [[100, 1000], [200, 1000]], z_max=8000)

    result = calculate_road_slant_compliance()

    assert result[0]["measured_value"] == pytest.approx(8.0 - 6.25)
    assert result[0]["measured_value"] > 0
    assert result[0]["evidence"]["worst_element_guid"] == "wall1"


def test_calculate_road_slant_compliance_unknown_when_no_building_elements(test_db, monkeypatch):
    monkeypatch.setenv("LAND_USE_CATEGORY", "residential")
    _insert_site_and_road(
        test_db,
        site_points=[[0, 0], [10000, 0], [10000, 10000], [0, 10000]],
        road_points=[[-1000, -4000], [11000, -4000], [11000, 0], [-1000, 0]],
    )

    result = calculate_road_slant_compliance()

    assert result[0]["measured_value"] is None


def test_calculate_road_slant_compliance_unknown_when_no_road(test_db, monkeypatch):
    monkeypatch.setenv("LAND_USE_CATEGORY", "residential")
    test_db.insert_element(
        "site1", "Zone", "敷地",
        json.dumps({}),
        json.dumps({"type": "polygon", "points": [[0, 0], [10000, 0], [10000, 10000], [0, 10000]]}),
    )
    _insert_wall(test_db, "wall1", [[100, 1000], [200, 1000]], z_max=5000)

    result = calculate_road_slant_compliance()

    assert result[0]["measured_value"] is None


def test_calculate_road_slant_compliance_ignores_elements_outside_site(test_db, monkeypatch):
    monkeypatch.setenv("LAND_USE_CATEGORY", "residential")
    _insert_site_and_road(
        test_db,
        site_points=[[0, 0], [10000, 0], [10000, 10000], [0, 10000]],
        road_points=[[-1000, -4000], [11000, -4000], [11000, 0], [-1000, 0]],
    )
    # 敷地の外(x=20000)にある壁は判定対象に含めない。
    _insert_wall(test_db, "wall_outside", [[20100, 1000], [20200, 1000]], z_max=100000)

    result = calculate_road_slant_compliance()

    assert result[0]["measured_value"] is None
