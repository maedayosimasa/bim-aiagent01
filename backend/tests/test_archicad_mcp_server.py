import asyncio
import json

from mcp import ClientSession
from mcp.client._memory import InMemoryTransport

from backend.archicad_mcp import client as archicad_client
from backend.archicad_mcp.server import mcp_server
from backend.database import db
from tests.test_tapir import _make_fake_tapir_server


def call_mcp_tool(name, arguments=None):
    async def _run():
        async with InMemoryTransport(mcp_server) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()
                return await session.call_tool(name, arguments or {})

    return asyncio.run(_run())


def _payload(result):
    return json.loads(result.content[0].text)


def test_list_elements_tool_returns_cached_elements(sample_elements):
    result = call_mcp_tool("list_elements")

    # list-returning tools populate structured_content as {"result": [...]}
    # rather than a single JSON text block (see _payload for dict tools).
    guids = {el["guid"] for el in result.structured_content["result"]}
    assert guids == {"wall001", "door001", "room001", "room002"}


def test_update_element_properties_tool_merges(sample_elements):
    result = call_mcp_tool(
        "update_element_properties",
        {"guid": "wall001", "properties": {"thickness": 999}},
    )

    payload = _payload(result)
    assert payload["properties"]["thickness"] == 999
    assert payload["properties"]["height"] == 3000


def test_update_element_properties_tool_missing_guid_is_error(test_db):
    result = call_mcp_tool(
        "update_element_properties", {"guid": "nope", "properties": {}}
    )

    assert result.is_error is True


def test_update_element_geometry_tool_replaces_geometry(sample_elements):
    result = call_mcp_tool(
        "update_element_geometry",
        {"guid": "door001", "geometry": {"type": "point", "x": 1, "y": 2}},
    )

    payload = _payload(result)
    assert payload["geometry"] == {"type": "point", "x": 1, "y": 2}


def test_update_element_geometry_tool_rejects_invalid_geometry(sample_elements):
    result = call_mcp_tool(
        "update_element_geometry",
        {"guid": "door001", "geometry": {"type": "polygon", "points": [[0, 0]]}},
    )

    assert result.is_error is True


def test_create_and_delete_element_tools(test_db):
    create_result = call_mcp_tool(
        "create_element",
        {
            "guid": "new001",
            "element_type": "Window",
            "name": "テスト窓",
            "properties": {"width": 800},
            "geometry": {"type": "point", "x": 0, "y": 0},
        },
    )

    assert create_result.is_error is False
    assert db.get_element("new001") is not None

    delete_result = call_mcp_tool("delete_element", {"guid": "new001"})

    assert delete_result.is_error is False
    assert db.get_element("new001") is None


def test_delete_element_tool_missing_guid_is_error(test_db):
    result = call_mcp_tool("delete_element", {"guid": "nope"})

    assert result.is_error is True


def test_rebuild_relations_tool(sample_elements):
    result = call_mcp_tool("rebuild_relations")

    payload = _payload(result)
    assert payload["count"] == 4


def test_list_archicad_tools_without_configuration_is_error(monkeypatch, test_db):
    monkeypatch.delenv("ARCHICAD_MCP_URL", raising=False)

    result = call_mcp_tool("list_archicad_tools")

    assert result.is_error is True


def _patch_fake_tapir_transport(monkeypatch):
    fake_server = _make_fake_tapir_server()
    monkeypatch.setattr(
        archicad_client, "_default_transport", lambda: InMemoryTransport(fake_server)
    )


def test_sync_from_archicad_tool_populates_cache(test_db, monkeypatch):
    _patch_fake_tapir_transport(monkeypatch)

    result = call_mcp_tool("sync_from_archicad", {"limit": 10})

    payload = _payload(result)
    assert payload == {"synced": 2, "requested": 2}

    wall = db.get_element("guid-1")
    assert wall["type"] == "Wall"
    # Wall centerline (begCoordinate/endCoordinate), not the bounding-box
    # approximation - see tapir.details_to_geometry().
    assert json.loads(wall["geometry"]) == {
        "type": "line",
        "points": [[0, 0], [4000, 0]],
        "z_min": 0,
        "z_max": 3000,
    }

    zone = db.get_element("guid-2")
    # Archicad calls rooms "Zone", not "Room" - sync must not silently
    # rename it (see tapir.py module docstring).
    assert zone["type"] == "Zone"
    assert zone["name"] == "居室A"
    assert json.loads(zone["geometry"]) == {
        "type": "polygon",
        "points": [[0, 0], [4000, 0], [4000, 3000], [0, 3000]],
        "z_min": 0,
        "z_max": 3000,
    }


def test_list_archicad_properties_tool(test_db, monkeypatch):
    _patch_fake_tapir_transport(monkeypatch)

    result = call_mcp_tool("list_archicad_properties")

    properties = result.structured_content["result"]
    assert properties[0]["propertyName"] == "Name"
    assert properties[0]["propertyId"]["guid"] == "prop-1"


def test_get_archicad_property_values_tool(test_db, monkeypatch):
    _patch_fake_tapir_transport(monkeypatch)

    result = call_mcp_tool(
        "get_archicad_property_values",
        {"guids": ["guid-1"], "property_guids": ["prop-1"]},
    )

    assert result.is_error is False
    assert "Wall A" in result.content[0].text


def test_set_archicad_property_value_tool(test_db, monkeypatch):
    _patch_fake_tapir_transport(monkeypatch)

    result = call_mcp_tool(
        "set_archicad_property_value",
        {"guid": "guid-1", "property_guid": "prop-1", "value": "New Name"},
    )

    assert result.is_error is False


def test_move_archicad_element_tool(test_db, monkeypatch):
    _patch_fake_tapir_transport(monkeypatch)

    result = call_mcp_tool(
        "move_archicad_element", {"guid": "guid-1", "dx": 100, "dy": 0}
    )

    assert result.is_error is False


def test_delete_archicad_elements_tool(test_db, monkeypatch):
    _patch_fake_tapir_transport(monkeypatch)

    result = call_mcp_tool("delete_archicad_elements", {"guids": ["guid-1"]})

    assert result.is_error is False
