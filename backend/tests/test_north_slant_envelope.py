import json

import pytest

from backend.engine.north_slant_envelope import (
    calculate_north_slant_compliance,
    calculate_north_slant_envelope,
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


def test_calculate_north_slant_envelope_north_along_y_axis(test_db):
    _insert_site(test_db)

    # north_degrees=0のとき、真北はプロジェクトのY+軸と一致する前提。
    result = calculate_north_slant_envelope(north_degrees=0, kitagawa_shasen_kubun="low_rise")

    assert len(result) == 1
    entry = result[0]
    assert entry["resolved"] is True
    assert entry["gradient"] == 1.25
    assert entry["rise_height_m"] == 5.0

    by_xy = {(v["x"], v["y"]): v["z_m"] for v in entry["vertices"]}
    # y=10000が敷地の最北端(真北方向の水平距離0m) → 高さ = 5.0m
    assert by_xy[(0, 10000)] == pytest.approx(5.0)
    # y=0は最北端から10m離れている → 高さ = 5 + 1.25*10 = 17.5m
    assert by_xy[(0, 0)] == pytest.approx(17.5)


def test_calculate_north_slant_envelope_north_along_x_axis(test_db):
    _insert_site(test_db)

    # north_degrees=90のとき、真北はプロジェクトのX+軸と一致する前提。
    result = calculate_north_slant_envelope(north_degrees=90, kitagawa_shasen_kubun="low_rise")

    by_xy = {(v["x"], v["y"]): v["z_m"] for v in result[0]["vertices"]}
    assert by_xy[(10000, 0)] == pytest.approx(5.0)
    assert by_xy[(0, 0)] == pytest.approx(17.5)


def test_calculate_north_slant_envelope_mid_rise_uses_10m_rise(test_db):
    _insert_site(test_db)

    result = calculate_north_slant_envelope(north_degrees=0, kitagawa_shasen_kubun="mid_rise")

    assert result[0]["rise_height_m"] == 10.0
    by_xy = {(v["x"], v["y"]): v["z_m"] for v in result[0]["vertices"]}
    assert by_xy[(0, 10000)] == pytest.approx(10.0)


def test_calculate_north_slant_envelope_unresolved_when_not_applicable(test_db):
    _insert_site(test_db)

    result = calculate_north_slant_envelope(north_degrees=0, kitagawa_shasen_kubun="not_applicable")

    assert result[0]["resolved"] is False
    assert result[0]["vertices"] == []


def test_calculate_north_slant_envelope_unresolved_when_kubun_unset(test_db, monkeypatch):
    monkeypatch.delenv("KITAGAWA_SHASEN_KUBUN", raising=False)
    _insert_site(test_db)

    result = calculate_north_slant_envelope(north_degrees=0)

    assert result[0]["resolved"] is False


def test_calculate_north_slant_envelope_reads_kubun_from_env_var(test_db, monkeypatch):
    monkeypatch.setenv("KITAGAWA_SHASEN_KUBUN", "low_rise")
    _insert_site(test_db)

    result = calculate_north_slant_envelope(north_degrees=0)

    assert result[0]["resolved"] is True
    assert result[0]["rise_height_m"] == 5.0


def test_calculate_north_slant_envelope_empty_when_no_site(test_db):
    assert calculate_north_slant_envelope(north_degrees=0, kitagawa_shasen_kubun="low_rise") == []


def test_calculate_north_slant_envelope_derives_kubun_from_land_use_category(test_db, monkeypatch):
    # (2026-08-14追加)kitagawa_shasen_kubun未設定でも、land_use_categoryに
    # 正式な用途地域名が設定されていれば自動導出される
    # (get_kitagawa_shasen_kubun())。
    monkeypatch.delenv("KITAGAWA_SHASEN_KUBUN", raising=False)
    monkeypatch.setenv("LAND_USE_CATEGORY", "第一種低層住居専用地域")
    _insert_site(test_db)

    result = calculate_north_slant_envelope(north_degrees=0)

    assert result[0]["resolved"] is True
    assert result[0]["rise_height_m"] == 5.0


def test_calculate_north_slant_envelope_stays_unresolved_for_coarse_land_use_category(test_db, monkeypatch):
    # 粗い3分類("residential"等)のみでは低層/中高層の区別が付かないため、
    # 自動導出されない(not_applicable扱い、推測しない)。
    monkeypatch.delenv("KITAGAWA_SHASEN_KUBUN", raising=False)
    monkeypatch.setenv("LAND_USE_CATEGORY", "residential")
    _insert_site(test_db)

    result = calculate_north_slant_envelope(north_degrees=0)

    assert result[0]["resolved"] is False


def test_calculate_north_slant_compliance_passes_when_within_limit(test_db):
    _insert_site(test_db)
    # 最北端(y=10000)から5m南(y=5000)での高さ上限 = 5 + 1.25*5 = 11.25m。
    _insert_wall(test_db, "wall1", [[4900, 5000], [5100, 5000]], z_max=10000)

    result = calculate_north_slant_compliance(north_degrees=0, kitagawa_shasen_kubun="low_rise")

    assert result[0]["measured_value"] == pytest.approx(10.0 - 11.25)
    assert result[0]["measured_value"] <= 0


def test_calculate_north_slant_compliance_fails_when_exceeding_limit(test_db):
    _insert_site(test_db)
    _insert_wall(test_db, "wall1", [[4900, 5000], [5100, 5000]], z_max=15000)

    result = calculate_north_slant_compliance(north_degrees=0, kitagawa_shasen_kubun="low_rise")

    assert result[0]["measured_value"] == pytest.approx(15.0 - 11.25)
    assert result[0]["measured_value"] > 0


def test_calculate_north_slant_compliance_unknown_when_not_applicable(test_db):
    _insert_site(test_db)
    _insert_wall(test_db, "wall1", [[4900, 5000], [5100, 5000]], z_max=10000)

    result = calculate_north_slant_compliance(north_degrees=0, kitagawa_shasen_kubun="not_applicable")

    assert result[0]["measured_value"] is None


def test_calculate_north_slant_compliance_unknown_when_north_degrees_missing(test_db, monkeypatch):
    monkeypatch.delenv("NORTH_DEGREES", raising=False)
    _insert_site(test_db)
    _insert_wall(test_db, "wall1", [[4900, 5000], [5100, 5000]], z_max=10000)

    result = calculate_north_slant_compliance(kitagawa_shasen_kubun="low_rise")

    assert result[0]["measured_value"] is None


def test_calculate_north_slant_compliance_reads_north_degrees_from_env_var(test_db, monkeypatch):
    monkeypatch.setenv("NORTH_DEGREES", "0")
    _insert_site(test_db)
    _insert_wall(test_db, "wall1", [[4900, 5000], [5100, 5000]], z_max=10000)

    result = calculate_north_slant_compliance(kitagawa_shasen_kubun="low_rise")

    assert result[0]["measured_value"] == pytest.approx(10.0 - 11.25)


def test_calculate_north_slant_compliance_unknown_when_no_building_elements(test_db):
    _insert_site(test_db)

    result = calculate_north_slant_compliance(north_degrees=0, kitagawa_shasen_kubun="low_rise")

    assert result[0]["measured_value"] is None
