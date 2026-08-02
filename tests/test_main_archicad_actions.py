import pytest
from mcp.client._memory import InMemoryTransport

from backend.archicad_mcp import client as archicad_client
from backend.database import db
from tests.test_tapir import _make_fake_tapir_server


@pytest.fixture
def fake_archicad(test_db, monkeypatch):
    fake_server = _make_fake_tapir_server()
    monkeypatch.setattr(
        archicad_client, "_default_transport", lambda: InMemoryTransport(fake_server)
    )
    return fake_server


def test_sync_endpoint_populates_cache(api_client, fake_archicad):
    response = api_client.post("/archicad/sync", json={"limit": 10})

    assert response.status_code == 200
    assert response.json() == {"synced": 2, "requested": 2}

    wall = db.get_element("guid-1")
    assert wall["type"] == "Wall"


def test_properties_endpoint(api_client, fake_archicad):
    response = api_client.get("/archicad/properties")

    assert response.status_code == 200
    properties = response.json()
    assert properties[0]["propertyName"] == "Name"


def test_property_values_endpoint(api_client, fake_archicad):
    response = api_client.post(
        "/archicad/properties/values",
        json={"guids": ["guid-1"], "property_guids": ["prop-1"]},
    )

    assert response.status_code == 200
    assert response.json()[0][0]["propertyValue"]["value"] == "Wall A"


def test_set_property_endpoint(api_client, fake_archicad):
    response = api_client.post(
        "/archicad/properties/set",
        json={"guid": "guid-1", "property_guid": "prop-1", "value": "New Name"},
    )

    assert response.status_code == 200
    assert response.json()["executionResults"][0]["success"] is True


def test_move_element_endpoint(api_client, fake_archicad):
    response = api_client.post(
        "/archicad/elements/move",
        json={"guid": "guid-1", "dx": 100, "dy": 0},
    )

    assert response.status_code == 200
    assert response.json()["executionResults"][0]["success"] is True


def test_delete_elements_endpoint(api_client, fake_archicad):
    response = api_client.post(
        "/archicad/elements/delete", json={"guids": ["guid-1"]}
    )

    assert response.status_code == 200
    assert response.json()["executionResults"][0]["success"] is True


def test_sync_endpoint_when_not_configured_returns_400(api_client, monkeypatch):
    monkeypatch.delenv("ARCHICAD_MCP_URL", raising=False)
    archicad_client.set_connection_url(None)

    response = api_client.post("/archicad/sync", json={"limit": 10})

    assert response.status_code == 400
