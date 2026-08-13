import json

import pytest

from backend.engine.adjacent_boundary_slant_envelope import (
    calculate_adjacent_boundary_slant_compliance,
    calculate_adjacent_boundary_slant_envelope,
)


def _insert_wall(test_db, guid, points, z_max, z_min=0):
    test_db.insert_element(
        guid, "Wall", guid,
        json.dumps({}),
        json.dumps({"type": "line", "points": points, "z_min": z_min, "z_max": z_max}),
    )


def test_calculate_adjacent_boundary_slant_envelope_applies_rise_and_gradient(test_db, monkeypatch):
    monkeypatch.setenv("LAND_USE_CATEGORY", "residential")
    # 敷地の南側(y=0)に道路。道路は敷地より狭く(x:2000-8000)、東西・北の
    # 辺は道路に接しない(隣地境界線)。中点(5000,0)を頂点として持たせ、
    # 隣地境界線までの実際の距離が計算されることを確認する。
    test_db.insert_element(
        "site1", "Zone", "敷地",
        json.dumps({}),
        json.dumps({
            "type": "polygon",
            "points": [[0, 0], [5000, 0], [10000, 0], [10000, 10000], [0, 10000]],
        }),
    )
    test_db.insert_element(
        "road1", "Zone", "前面道路",
        json.dumps({}),
        json.dumps({"type": "polygon", "points": [[2000, -4000], [8000, -4000], [8000, 0], [2000, 0]]}),
    )

    result = calculate_adjacent_boundary_slant_envelope()

    assert len(result) == 1
    entry = result[0]
    assert entry["resolved"] is True
    assert entry["gradient"] == 1.25
    assert entry["rise_height_m"] == 20.0

    by_xy = {(v["x"], v["y"]): v["z_m"] for v in entry["vertices"]}
    # (5000,0): 道路に接する南辺の中点。最寄りの隣地境界線(東/北/西辺)までの
    # 距離は5m → 高さ = 20 + 1.25*5 = 26.25m
    assert by_xy[(5000, 0)] == pytest.approx(26.25)
    # (10000,10000): 東辺・北辺の交点(隣地境界線自体)なので距離0m → 20m
    assert by_xy[(10000, 10000)] == pytest.approx(20.0)


def test_calculate_adjacent_boundary_slant_envelope_gradient_by_land_use_category(test_db):
    test_db.insert_element(
        "site1", "Zone", "敷地",
        json.dumps({}),
        json.dumps({"type": "polygon", "points": [[0, 0], [10000, 0], [10000, 10000], [0, 10000]]}),
    )

    residential = calculate_adjacent_boundary_slant_envelope("residential")
    commercial = calculate_adjacent_boundary_slant_envelope("commercial")

    assert residential[0]["gradient"] == 1.25
    assert residential[0]["rise_height_m"] == 20.0
    assert commercial[0]["gradient"] == 2.5
    assert commercial[0]["rise_height_m"] == 31.0


def test_calculate_adjacent_boundary_slant_envelope_unresolved_when_fully_road_facing(test_db):
    # 敷地の全周が道路に接している(隣地境界線が1つも無い)場合は判定不能。
    test_db.insert_element(
        "site1", "Zone", "敷地",
        json.dumps({}),
        json.dumps({"type": "polygon", "points": [[0, 0], [10000, 0], [10000, 10000], [0, 10000]]}),
    )
    test_db.insert_element(
        "road1", "Zone", "前面道路",
        json.dumps({}),
        json.dumps({"type": "polygon", "points": [[-2000, -2000], [12000, -2000], [12000, 12000], [-2000, 12000]]}),
    )

    result = calculate_adjacent_boundary_slant_envelope("residential")

    assert result[0]["resolved"] is False
    assert result[0]["vertices"] == []


def test_calculate_adjacent_boundary_slant_envelope_works_without_any_road(test_db):
    # 前面道路が無くても(接道の有無自体はこの計算の対象外)、敷地の全周を
    # 隣地境界線とみなして計算できる。
    test_db.insert_element(
        "site1", "Zone", "敷地",
        json.dumps({}),
        json.dumps({"type": "polygon", "points": [[0, 0], [10000, 0], [10000, 10000], [0, 10000]]}),
    )

    result = calculate_adjacent_boundary_slant_envelope("residential")

    assert result[0]["resolved"] is True


def test_calculate_adjacent_boundary_slant_envelope_empty_when_no_site(test_db):
    assert calculate_adjacent_boundary_slant_envelope("residential") == []


def test_calculate_adjacent_boundary_slant_envelope_raises_for_unknown_land_use_category(test_db):
    test_db.insert_element(
        "site1", "Zone", "敷地",
        json.dumps({}),
        json.dumps({"type": "polygon", "points": [[0, 0], [10000, 0], [10000, 10000], [0, 10000]]}),
    )

    with pytest.raises(ValueError):
        calculate_adjacent_boundary_slant_envelope("unknown_category")


def test_calculate_adjacent_boundary_slant_compliance_passes_when_within_limit(test_db, monkeypatch):
    monkeypatch.setenv("LAND_USE_CATEGORY", "residential")
    test_db.insert_element(
        "site1", "Zone", "敷地",
        json.dumps({}),
        json.dumps({"type": "polygon", "points": [[0, 0], [10000, 0], [10000, 10000], [0, 10000]]}),
    )
    # 中心(5000,5000)での高さ上限 = 20 + 1.25*5 = 26.25m(最寄り境界線まで5m)。
    _insert_wall(test_db, "wall1", [[4900, 5000], [5100, 5000]], z_max=25000)

    result = calculate_adjacent_boundary_slant_compliance()

    assert result[0]["measured_value"] == pytest.approx(25.0 - 26.25)
    assert result[0]["measured_value"] <= 0


def test_calculate_adjacent_boundary_slant_compliance_fails_when_exceeding_limit(test_db, monkeypatch):
    monkeypatch.setenv("LAND_USE_CATEGORY", "residential")
    test_db.insert_element(
        "site1", "Zone", "敷地",
        json.dumps({}),
        json.dumps({"type": "polygon", "points": [[0, 0], [10000, 0], [10000, 10000], [0, 10000]]}),
    )
    _insert_wall(test_db, "wall1", [[4900, 5000], [5100, 5000]], z_max=30000)

    result = calculate_adjacent_boundary_slant_compliance()

    assert result[0]["measured_value"] == pytest.approx(30.0 - 26.25)
    assert result[0]["measured_value"] > 0


def test_calculate_adjacent_boundary_slant_compliance_unknown_when_fully_road_facing(test_db, monkeypatch):
    monkeypatch.setenv("LAND_USE_CATEGORY", "residential")
    test_db.insert_element(
        "site1", "Zone", "敷地",
        json.dumps({}),
        json.dumps({"type": "polygon", "points": [[0, 0], [10000, 0], [10000, 10000], [0, 10000]]}),
    )
    test_db.insert_element(
        "road1", "Zone", "前面道路",
        json.dumps({}),
        json.dumps({"type": "polygon", "points": [[-2000, -2000], [12000, -2000], [12000, 12000], [-2000, 12000]]}),
    )
    _insert_wall(test_db, "wall1", [[4900, 5000], [5100, 5000]], z_max=25000)

    result = calculate_adjacent_boundary_slant_compliance()

    assert result[0]["measured_value"] is None


def test_calculate_adjacent_boundary_slant_compliance_unknown_when_no_building_elements(test_db, monkeypatch):
    monkeypatch.setenv("LAND_USE_CATEGORY", "residential")
    test_db.insert_element(
        "site1", "Zone", "敷地",
        json.dumps({}),
        json.dumps({"type": "polygon", "points": [[0, 0], [10000, 0], [10000, 10000], [0, 10000]]}),
    )

    result = calculate_adjacent_boundary_slant_compliance()

    assert result[0]["measured_value"] is None
