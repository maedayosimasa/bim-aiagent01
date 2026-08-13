import json

import pytest

from backend.engine.north_slant_envelope import calculate_north_slant_envelope


def _insert_site(test_db):
    test_db.insert_element(
        "site1", "Zone", "敷地",
        json.dumps({}),
        json.dumps({"type": "polygon", "points": [[0, 0], [10000, 0], [10000, 10000], [0, 10000]]}),
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
