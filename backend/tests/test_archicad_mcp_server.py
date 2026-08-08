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
    # ゾーンカテゴリ("住宅-1"、番号付きサブタイプ)が解決され、実室として
    # 扱われる(zone_is_envelope=False)ことを確認する。
    zone_properties = json.loads(zone["properties"])
    assert zone_properties["zone_category"] == "住宅-1"
    assert zone_properties["zone_is_envelope"] is False


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


def test_get_archicad_geo_location_tool(test_db, monkeypatch):
    _patch_fake_tapir_transport(monkeypatch)

    result = call_mcp_tool("get_archicad_geo_location")

    payload = _payload(result)
    assert payload["north_degrees"] == 45.0


def test_focus_archicad_elements_tool(test_db, monkeypatch):
    _patch_fake_tapir_transport(monkeypatch)

    result = call_mcp_tool("focus_archicad_elements", {"guids": ["guid-1"]})

    assert result.is_error is False


def test_call_archicad_tool_passthrough(test_db, monkeypatch):
    _patch_fake_tapir_transport(monkeypatch)

    # call_archicad_toolは汎用パススルーで、tapir.pyの_call()と違い引数を
    # {"input": ...}へ自動でラップしない。呼び出し側がTapirの規約
    # (register_tapir.pyのdynamic_command)通りに渡す必要がある。
    result = call_mcp_tool(
        "call_archicad_tool", {"name": "GetAllElements", "arguments": {"input": None}}
    )

    assert result.is_error is False
    assert "guid-1" in result.content[0].text


def test_sync_from_archicad_tool_with_no_elements(test_db, monkeypatch):
    # GetAllElementsが1件も返さない場合、詳細/バウンディングボックスの
    # 問い合わせをスキップして即座に0件で返す(server.pyのsync_from_
    # archicad()の早期returnを確認する)。
    from mcp.server.mcpserver import MCPServer

    empty_server = MCPServer(name="fake-tapir-empty")

    @empty_server.tool(name="GetAllElements")
    def get_all_elements(input) -> dict:
        return {"elements": []}

    monkeypatch.setattr(
        archicad_client, "_default_transport", lambda: InMemoryTransport(empty_server)
    )

    result = call_mcp_tool("sync_from_archicad", {"limit": 10})

    assert _payload(result) == {"synced": 0, "requested": 0}


def test_update_element_geometry_tool_missing_guid_is_error(test_db):
    result = call_mcp_tool(
        "update_element_geometry",
        {"guid": "nope", "geometry": {"type": "point", "x": 0, "y": 0}},
    )

    assert result.is_error is True


def test_analyze_bim_space_tool(sample_elements):
    result = call_mcp_tool("analyze_bim_space", {"model_id": "test-model"})

    payload = _payload(result)
    assert payload["model_id"] == "test-model"


def test_search_bim_elements_tool(sample_elements, monkeypatch):
    # search_elements()自体(ChromaDB連携)はtest_vector_store.pyで別途
    # 検証済み。ここではMCPツールの薄いラッパーが引数をそのまま
    # search_elements()へ渡し、その戻り値をそのまま返すことだけを確認する
    # (実ChromaDB/埋め込みモデルは使わない)。
    from backend.archicad_mcp import server as server_module

    captured = {}

    def fake_search_elements(query, n_results=5):
        captured["query"] = query
        captured["n_results"] = n_results
        return [{"guid": "wall001", "text": "壁"}]

    monkeypatch.setattr(server_module, "search_elements", fake_search_elements)

    result = call_mcp_tool("search_bim_elements", {"query": "壁", "n_results": 3})

    hits = result.structured_content["result"]
    assert hits == [{"guid": "wall001", "text": "壁"}]
    assert captured == {"query": "壁", "n_results": 3}


def test_get_engine_analysis_snapshot_tool_returns_none_when_not_analyzed(test_db):
    result = call_mcp_tool("get_engine_analysis_snapshot")

    # Noneはtext contentブロックを持たない({"result": None}のみ)ため
    # _payload()ではなくstructured_contentを直接見る。
    assert result.structured_content["result"] is None


def test_get_engine_analysis_snapshot_tool_returns_saved_snapshot(sample_elements):
    call_mcp_tool("analyze_bim_space", {"model_id": "test-model"})

    result = call_mcp_tool("get_engine_analysis_snapshot")

    payload = _payload(result)
    assert payload["model_id"] == "test-model"
    assert payload["connected"] is True
    assert isinstance(payload["issues"], list)
    assert isinstance(payload["graph_data"], dict)


def test_get_graph_relation_snapshot_tool(sample_elements):
    call_mcp_tool("rebuild_relations")

    result = call_mcp_tool("get_graph_relation_snapshot")

    rows = result.structured_content["result"]
    assert len(rows) == 4
