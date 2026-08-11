from backend.engine import legal_inputs


def test_resolve_legal_input_reads_uppercased_env_var(monkeypatch):
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


def test_list_legal_inputs_covers_all_definitions_with_value_and_used_by(monkeypatch):
    monkeypatch.setenv("LAND_USE_CATEGORY", "residential")

    entries = legal_inputs.list_legal_inputs(used_by={"land_use_category": ["effective_daylighting_ratio"]})

    assert len(entries) == len(legal_inputs.LEGAL_INPUT_DEFINITIONS)
    by_key = {e["key"]: e for e in entries}

    assert by_key["land_use_category"]["value"] == "residential"
    assert by_key["land_use_category"]["used_by_rule_ids"] == ["effective_daylighting_ratio"]
    assert by_key["kenpei_ritsu"]["value"] is None
    assert by_key["kenpei_ritsu"]["used_by_rule_ids"] == []


def test_list_legal_inputs_defaults_used_by_to_empty():
    entries = legal_inputs.list_legal_inputs()
    assert all(e["used_by_rule_ids"] == [] for e in entries)
