import json

import pytest

from backend.engine.road_slant_envelope import calculate_road_slant_envelope


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
