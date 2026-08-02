import asyncio

import pytest
from mcp.client._memory import InMemoryTransport
from mcp.server.mcpserver import MCPServer

from backend.archicad_mcp import client as archicad_client


def _make_fake_archicad_server():
    # Stands in for the real PC-side Archicad MCP server (unknown tool
    # contract until the Tailscale bridge is live) so the generic
    # passthrough client can be exercised end-to-end without a network.
    server = MCPServer(name="fake-archicad")

    @server.tool()
    def get_elements() -> list[dict]:
        """Fake tool mimicking an eventual Archicad element listing."""
        return [{"guid": "wall999", "type": "Wall"}]

    return server


def test_is_configured_reflects_env_var(monkeypatch):
    monkeypatch.delenv("ARCHICAD_MCP_URL", raising=False)
    assert archicad_client.is_configured() is False

    monkeypatch.setenv("ARCHICAD_MCP_URL", "https://example.invalid/mcp")
    assert archicad_client.is_configured() is True


def test_list_tools_without_configuration_raises(monkeypatch):
    monkeypatch.delenv("ARCHICAD_MCP_URL", raising=False)

    async def _run():
        with pytest.raises(archicad_client.ArchicadNotConfiguredError):
            await archicad_client.list_tools()

    asyncio.run(_run())


def test_list_tools_against_fake_server():
    server = _make_fake_archicad_server()

    async def _run():
        transport = InMemoryTransport(server)
        return await archicad_client.list_tools(transport=transport)

    assert asyncio.run(_run()) == ["get_elements"]


def test_call_tool_against_fake_server():
    server = _make_fake_archicad_server()

    async def _run():
        transport = InMemoryTransport(server)
        return await archicad_client.call_tool(
            "get_elements", {}, transport=transport
        )

    result = asyncio.run(_run())

    assert result.is_error is False
    assert "wall999" in result.content[0].text


def test_check_connection_when_not_configured(monkeypatch):
    monkeypatch.delenv("ARCHICAD_MCP_URL", raising=False)

    status = asyncio.run(archicad_client.check_connection())

    assert status == {
        "configured": False,
        "reachable": False,
        "error": "ARCHICAD_MCP_URL is not set",
    }


def test_check_connection_when_reachable():
    server = _make_fake_archicad_server()

    async def _run():
        transport = InMemoryTransport(server)
        return await archicad_client.check_connection(transport=transport)

    status = asyncio.run(_run())

    assert status == {
        "configured": True,
        "reachable": True,
        "tools": ["get_elements"],
    }


def test_check_connection_when_configured_but_unreachable(monkeypatch):
    # A URL is set, but nothing is actually listening there - the check
    # must catch the connection error rather than let it propagate.
    monkeypatch.setenv("ARCHICAD_MCP_URL", "http://127.0.0.1:1/mcp")

    status = asyncio.run(archicad_client.check_connection())

    assert status["configured"] is True
    assert status["reachable"] is False
    # The real cause must be visible, not anyio's generic TaskGroup wrapper
    # message ("unhandled errors in a TaskGroup (1 sub-exception)").
    assert "TaskGroup" not in status["error"]
    assert "ConnectError" in status["error"]


def test_describe_exception_unwraps_exception_groups():
    inner = ConnectionRefusedError("nope")
    group = ExceptionGroup("boom", [inner])

    described = archicad_client._describe_exception(group)

    assert "TaskGroup" not in described
    assert "ConnectionRefusedError" in described
    assert "nope" in described


def test_runtime_override_takes_priority_over_env_var(monkeypatch):
    monkeypatch.setenv("ARCHICAD_MCP_URL", "https://remote.example.invalid/mcp")

    assert archicad_client.get_active_url() == "https://remote.example.invalid/mcp"

    archicad_client.set_connection_url("http://127.0.0.1:8765/mcp/")
    assert archicad_client.get_active_url() == "http://127.0.0.1:8765/mcp/"


def test_runtime_override_can_be_cleared_back_to_env_var(monkeypatch):
    monkeypatch.setenv("ARCHICAD_MCP_URL", "https://remote.example.invalid/mcp")

    archicad_client.set_connection_url("http://127.0.0.1:8765/mcp/")
    archicad_client.set_connection_url(None)

    assert archicad_client.get_active_url() == "https://remote.example.invalid/mcp"


def test_get_connection_info_reports_override_state(monkeypatch):
    monkeypatch.setenv("ARCHICAD_MCP_URL", "https://remote.example.invalid/mcp")

    info = archicad_client.get_connection_info()
    assert info == {
        "active_url": "https://remote.example.invalid/mcp",
        "overridden": False,
        "env_default": "https://remote.example.invalid/mcp",
        "local_preset_url": archicad_client.LOCAL_PRESET_URL,
    }

    archicad_client.set_connection_url(archicad_client.LOCAL_PRESET_URL)
    info = archicad_client.get_connection_info()
    assert info["active_url"] == archicad_client.LOCAL_PRESET_URL
    assert info["overridden"] is True
    assert info["env_default"] == "https://remote.example.invalid/mcp"
