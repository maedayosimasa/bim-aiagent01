import json

import pytest

from backend.engine import legal_inputs


def _insert_site_with_legal_conditions(test_db, legal_conditions):
    test_db.insert_element(
        "site1", "Zone", "敷地",
        json.dumps({"legal_conditions": legal_conditions}),
        json.dumps({"type": "polygon", "points": [[0, 0], [10000, 0], [10000, 10000], [0, 10000]]}),
    )


def test_resolve_legal_input_reads_uppercased_env_var(test_db, monkeypatch):
    monkeypatch.delenv("LAND_USE_CATEGORY", raising=False)
    assert legal_inputs.resolve_legal_input("land_use_category") is None

    monkeypatch.setenv("LAND_USE_CATEGORY", "commercial")
    assert legal_inputs.resolve_legal_input("land_use_category") == "commercial"


def test_get_legal_input_definition_returns_none_for_unknown_key():
    assert legal_inputs.get_legal_input_definition("does_not_exist") is None


def test_get_legal_input_definition_returns_definition():
    definition = legal_inputs.get_legal_input_definition("land_use_category")
    assert definition is not None
    assert definition.label == "用途地域"
    assert definition.category == "都市計画"


def test_list_legal_inputs_covers_all_definitions_with_value_and_used_by(test_db, monkeypatch):
    monkeypatch.setenv("LAND_USE_CATEGORY", "residential")

    entries = legal_inputs.list_legal_inputs(used_by={"land_use_category": ["effective_daylighting_ratio"]})

    assert len(entries) == len(legal_inputs.LEGAL_INPUT_DEFINITIONS)
    by_key = {e["key"]: e for e in entries}

    assert by_key["land_use_category"]["value"] == "residential"
    assert by_key["land_use_category"]["used_by_rule_ids"] == ["effective_daylighting_ratio"]
    assert by_key["kenpei_ritsu"]["value"] is None
    assert by_key["kenpei_ritsu"]["used_by_rule_ids"] == []


def test_list_legal_inputs_defaults_used_by_to_empty(test_db):
    entries = legal_inputs.list_legal_inputs()
    assert all(e["used_by_rule_ids"] == [] for e in entries)


def test_resolve_legal_input_prefers_archicad_property_over_env_var(test_db, monkeypatch):
    monkeypatch.setenv("KENPEI_RITSU", "50")
    _insert_site_with_legal_conditions(test_db, {"建蔽率": "60"})

    assert legal_inputs.resolve_legal_input("kenpei_ritsu") == "60"


def test_resolve_legal_input_falls_back_to_env_var_when_no_archicad_property(test_db, monkeypatch):
    monkeypatch.setenv("KENPEI_RITSU", "50")
    _insert_site_with_legal_conditions(test_db, {"容積率": "200"})  # 建蔽率は無い

    assert legal_inputs.resolve_legal_input("kenpei_ritsu") == "50"


def test_resolve_legal_input_matches_property_name_by_keyword_substring(test_db):
    # プロパティ名の完全一致は要求しない(例: "前面道路幅(m)"のような表記の
    # 揺れにも対応する)。
    _insert_site_with_legal_conditions(test_db, {"前面道路幅(m)": "6.0"})

    assert legal_inputs.resolve_legal_input("douro_haba") == "6.0"


def test_resolve_legal_input_strips_percent_suffix_from_archicad_property(test_db):
    _insert_site_with_legal_conditions(test_db, {"容積率": "200%"})

    assert legal_inputs.resolve_legal_input("yoseki_ritsu") == "200"


def test_resolve_legal_input_strips_meter_suffix_from_archicad_property(test_db):
    _insert_site_with_legal_conditions(test_db, {"前面道路幅": "6.0m"})

    assert legal_inputs.resolve_legal_input("douro_haba") == "6.0"


def test_resolve_legal_input_ignores_unregistered_keys(test_db):
    # ARCHICAD_LEGAL_PROPERTY_KEYWORDSに未登録のkeyは敷地Zoneを検索せず、
    # 常に環境変数のみで解決する。
    _insert_site_with_legal_conditions(test_db, {"接道長さ": "5.0"})

    assert legal_inputs.resolve_legal_input("setsudo_nagasa") is None


def test_resolve_legal_input_reads_land_use_category_from_site_property(test_db):
    # (2026-08-14追加)land_use_categoryもkenpei_ritsu等と同様、敷地Zoneの
    # 「用途地域」プロパティから解決できる。
    _insert_site_with_legal_conditions(test_db, {"用途地域": "第一種住居地域"})

    assert legal_inputs.resolve_legal_input("land_use_category") == "第一種住居地域"


def test_resolve_legal_input_none_when_no_site_zone_or_property(test_db, monkeypatch):
    monkeypatch.delenv("KENPEI_RITSU", raising=False)
    test_db.insert_element(
        "room1", "Room", "居室",
        json.dumps({}),
        json.dumps({"type": "polygon", "points": [[0, 0], [1000, 0], [1000, 1000], [0, 1000]]}),
    )

    assert legal_inputs.resolve_legal_input("kenpei_ritsu") is None


def test_normalize_land_use_category_passes_through_coarse_categories():
    assert legal_inputs.normalize_land_use_category("residential") == ("residential", "not_applicable")
    assert legal_inputs.normalize_land_use_category("industrial") == ("industrial", "not_applicable")
    assert legal_inputs.normalize_land_use_category("commercial") == ("commercial", "not_applicable")


def test_normalize_land_use_category_maps_official_zone_names():
    assert legal_inputs.normalize_land_use_category("第一種低層住居専用地域") == ("residential", "low_rise")
    assert legal_inputs.normalize_land_use_category("第二種中高層住居専用地域") == ("residential", "mid_rise")
    assert legal_inputs.normalize_land_use_category("第一種住居地域") == ("residential", "not_applicable")
    assert legal_inputs.normalize_land_use_category("準工業地域") == ("industrial", "not_applicable")
    assert legal_inputs.normalize_land_use_category("商業地域") == ("commercial", "not_applicable")


def test_normalize_land_use_category_raises_for_unknown_value():
    with pytest.raises(ValueError):
        legal_inputs.normalize_land_use_category("よくわからない地域")


def test_resolve_legal_input_treats_placeholder_property_value_as_unset(test_db):
    # (2026-08-14実データで発覚)Archicadのピックリスト型プロパティは未選択
    # 時に「未設定」という文字列を値として返すことがあり、これを実際の値
    # として扱うとnormalize_land_use_category()等が誤動作する。
    _insert_site_with_legal_conditions(test_db, {"用途地域": "未設定"})

    assert legal_inputs.resolve_legal_input("land_use_category") is None


def test_resolve_legal_input_skips_placeholder_and_finds_next_match(test_db):
    # 1件目の敷地要素が「未設定」でも、他の要素に実際の値があれば拾う。
    test_db.insert_element(
        "site1", "Zone", "敷地A",
        json.dumps({"legal_conditions": {"用途地域": "未設定"}}),
        json.dumps({"type": "polygon", "points": [[0, 0], [10000, 0], [10000, 10000], [0, 10000]]}),
    )
    test_db.insert_element(
        "site2", "Zone", "敷地B",
        json.dumps({"legal_conditions": {"用途地域": "第一種住居地域"}}),
        json.dumps({"type": "polygon", "points": [[0, 0], [10000, 0], [10000, 10000], [0, 10000]]}),
    )

    assert legal_inputs.resolve_legal_input("land_use_category") == "第一種住居地域"
