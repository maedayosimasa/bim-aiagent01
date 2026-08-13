import json

import pytest

from backend.engine.height_district_envelope import (
    calculate_height_district_compliance,
    calculate_height_district_envelope,
)


def _insert_site(test_db):
    test_db.insert_element(
        "site1", "Zone", "敷地",
        json.dumps({}),
        json.dumps({"type": "polygon", "points": [[0, 0], [10000, 0], [10000, 10000], [0, 10000]]}),
    )


def _insert_wall(test_db, guid, points, z_max, z_min=0):
    test_db.insert_element(
        guid, "Wall", guid,
        json.dumps({}),
        json.dumps({"type": "line", "points": points, "z_min": z_min, "z_max": z_max}),
    )


def test_flat_kubun_applies_uniform_height_plus_kanwa(test_db):
    _insert_site(test_db)

    result = calculate_height_district_envelope(kubun="flat", max_height_m=10.0, kanwa_m=2.0)

    assert len(result) == 1
    entry = result[0]
    assert entry["resolved"] is True
    assert entry["max_height_m"] == 10.0
    assert entry["kanwa_m"] == 2.0
    assert all(v["z_m"] == pytest.approx(12.0) for v in entry["vertices"])


def test_flat_kubun_unresolved_when_max_height_missing(test_db):
    _insert_site(test_db)

    result = calculate_height_district_envelope(kubun="flat")

    assert result[0]["resolved"] is False
    assert result[0]["vertices"] == []


def test_north_slant_kubun_computes_distance_based_height(test_db):
    _insert_site(test_db)

    result = calculate_height_district_envelope(
        north_degrees=0, kubun="north_slant", rise_m=3.0, gradient=0.6
    )

    entry = result[0]
    assert entry["resolved"] is True
    by_xy = {(v["x"], v["y"]): v["z_m"] for v in entry["vertices"]}
    # y=10000が最北端(距離0m) → 高さ = 3.0m
    assert by_xy[(0, 10000)] == pytest.approx(3.0)
    # y=0は最北端から10m → 高さ = 3.0 + 0.6*10 = 9.0m
    assert by_xy[(0, 0)] == pytest.approx(9.0)


def test_north_slant_kubun_applies_max_height_cap_before_kanwa(test_db):
    _insert_site(test_db)

    result = calculate_height_district_envelope(
        north_degrees=0, kubun="north_slant", rise_m=3.0, gradient=0.6,
        max_height_m=5.0, kanwa_m=1.0,
    )

    by_xy = {(v["x"], v["y"]): v["z_m"] for v in result[0]["vertices"]}
    # (0,0): 基本計算9.0mだが上限5.0mで頭打ち → 5.0 + 緩和1.0 = 6.0m
    assert by_xy[(0, 0)] == pytest.approx(6.0)
    # (0,10000): 基本計算3.0m(上限未満なので頭打ちされない) → 3.0 + 1.0 = 4.0m
    assert by_xy[(0, 10000)] == pytest.approx(4.0)


def test_north_slant_kubun_unresolved_when_north_degrees_missing(test_db):
    _insert_site(test_db)

    result = calculate_height_district_envelope(kubun="north_slant", rise_m=3.0, gradient=0.6)

    assert result[0]["resolved"] is False


def test_north_slant_kubun_unresolved_when_rise_or_gradient_missing(test_db):
    _insert_site(test_db)

    result = calculate_height_district_envelope(north_degrees=0, kubun="north_slant", rise_m=3.0)

    assert result[0]["resolved"] is False


def test_unresolved_when_kubun_not_applicable(test_db):
    _insert_site(test_db)

    result = calculate_height_district_envelope(kubun="none")

    assert result[0]["resolved"] is False


def test_unresolved_when_kubun_unset(test_db, monkeypatch):
    monkeypatch.delenv("KODO_CHIKU_KUBUN", raising=False)
    _insert_site(test_db)

    result = calculate_height_district_envelope()

    assert result[0]["resolved"] is False


def test_reads_all_values_from_env_vars(test_db, monkeypatch):
    monkeypatch.setenv("KODO_CHIKU_KUBUN", "flat")
    monkeypatch.setenv("KODO_CHIKU_MAX_HEIGHT_M", "8")
    monkeypatch.setenv("KODO_CHIKU_KANWA_M", "1.5")
    _insert_site(test_db)

    result = calculate_height_district_envelope()

    assert result[0]["resolved"] is True
    assert result[0]["vertices"][0]["z_m"] == pytest.approx(9.5)


def test_kanwa_defaults_to_zero_when_unset(test_db, monkeypatch):
    monkeypatch.delenv("KODO_CHIKU_KANWA_M", raising=False)
    _insert_site(test_db)

    result = calculate_height_district_envelope(kubun="flat", max_height_m=10.0)

    assert result[0]["kanwa_m"] == 0.0
    assert result[0]["vertices"][0]["z_m"] == pytest.approx(10.0)


def test_empty_when_no_site(test_db):
    assert calculate_height_district_envelope(kubun="flat", max_height_m=10.0) == []


def test_raises_for_unparseable_numeric_env_var(test_db, monkeypatch):
    monkeypatch.setenv("KODO_CHIKU_KANWA_M", "たくさん")
    _insert_site(test_db)

    with pytest.raises(ValueError):
        calculate_height_district_envelope(kubun="flat", max_height_m=10.0)


def test_compliance_flat_kubun_passes_when_within_limit(test_db):
    _insert_site(test_db)
    _insert_wall(test_db, "wall1", [[4900, 5000], [5100, 5000]], z_max=11000)

    result = calculate_height_district_compliance(kubun="flat", max_height_m=10.0, kanwa_m=2.0)

    assert result[0]["measured_value"] == pytest.approx(11.0 - 12.0)
    assert result[0]["measured_value"] <= 0


def test_compliance_flat_kubun_fails_when_exceeding_limit(test_db):
    _insert_site(test_db)
    _insert_wall(test_db, "wall1", [[4900, 5000], [5100, 5000]], z_max=15000)

    result = calculate_height_district_compliance(kubun="flat", max_height_m=10.0, kanwa_m=2.0)

    assert result[0]["measured_value"] == pytest.approx(15.0 - 12.0)
    assert result[0]["measured_value"] > 0


def test_compliance_flat_kubun_unknown_when_max_height_missing(test_db):
    _insert_site(test_db)
    _insert_wall(test_db, "wall1", [[4900, 5000], [5100, 5000]], z_max=11000)

    result = calculate_height_district_compliance(kubun="flat")

    assert result[0]["measured_value"] is None


def test_compliance_north_slant_kubun_passes_when_within_limit(test_db):
    _insert_site(test_db)
    # 最北端(y=10000)から5m南(y=5000)での高さ上限 = 3.0 + 0.6*5 = 6.0m。
    _insert_wall(test_db, "wall1", [[4900, 5000], [5100, 5000]], z_max=5000)

    result = calculate_height_district_compliance(
        north_degrees=0, kubun="north_slant", rise_m=3.0, gradient=0.6
    )

    assert result[0]["measured_value"] == pytest.approx(5.0 - 6.0)
    assert result[0]["measured_value"] <= 0


def test_compliance_north_slant_kubun_fails_when_exceeding_limit(test_db):
    _insert_site(test_db)
    _insert_wall(test_db, "wall1", [[4900, 5000], [5100, 5000]], z_max=8000)

    result = calculate_height_district_compliance(
        north_degrees=0, kubun="north_slant", rise_m=3.0, gradient=0.6
    )

    assert result[0]["measured_value"] == pytest.approx(8.0 - 6.0)
    assert result[0]["measured_value"] > 0


def test_compliance_north_slant_kubun_unknown_when_north_degrees_missing(test_db, monkeypatch):
    monkeypatch.delenv("NORTH_DEGREES", raising=False)
    _insert_site(test_db)
    _insert_wall(test_db, "wall1", [[4900, 5000], [5100, 5000]], z_max=5000)

    result = calculate_height_district_compliance(kubun="north_slant", rise_m=3.0, gradient=0.6)

    assert result[0]["measured_value"] is None


def test_compliance_unknown_when_kubun_not_applicable(test_db):
    _insert_site(test_db)
    _insert_wall(test_db, "wall1", [[4900, 5000], [5100, 5000]], z_max=5000)

    result = calculate_height_district_compliance(kubun="none")

    assert result[0]["measured_value"] is None


def test_compliance_unknown_when_no_building_elements(test_db):
    _insert_site(test_db)

    result = calculate_height_district_compliance(kubun="flat", max_height_m=10.0)

    assert result[0]["measured_value"] is None
