import json

from backend.database import db


def test_get_element_returns_row(sample_elements):
    row = db.get_element("wall001")

    assert row["name"] == "間仕切壁"


def test_get_element_missing_returns_none(test_db):
    assert db.get_element("nope") is None


def test_update_element_properties_merges_with_existing(sample_elements):
    updated = db.update_element_properties(
        "wall001", {"thickness": 200, "color": "gray"}
    )

    assert updated is True

    row = db.get_element("wall001")
    properties = json.loads(row["properties"])

    assert properties["thickness"] == 200
    assert properties["height"] == 3000
    assert properties["color"] == "gray"


def test_update_element_properties_missing_guid_returns_false(test_db):
    assert db.update_element_properties("nope", {"a": 1}) is False


def test_update_element_geometry_replaces_wholesale(sample_elements):
    new_geometry = {"type": "point", "x": 1, "y": 2}

    updated = db.update_element_geometry("door001", new_geometry)

    assert updated is True

    row = db.get_element("door001")
    assert json.loads(row["geometry"]) == new_geometry


def test_update_element_geometry_missing_guid_returns_false(test_db):
    assert db.update_element_geometry("nope", {"type": "point", "x": 0, "y": 0}) is False


def test_delete_element_removes_row(sample_elements):
    assert db.delete_element("room002") is True
    assert db.get_element("room002") is None


def test_delete_element_missing_guid_returns_false(test_db):
    assert db.delete_element("nope") is False
