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
    assert payload["synced"] == 2
    assert payload["requested"] == 2
    # _make_fake_tapir_server()には"敷地"に一致する要素も「法条件」プロパティも
    # 無いため、legal_conditions_syncedは空({}要素それぞれの敷地判定ロジックは
    # test_sync_from_archicad_tool_populates_legal_conditions_for_site_zone参照)。
    assert payload["legal_conditions_synced"] == {}

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
    # レイヤー名("壁-躯体"、layerIndex=1)が表示用情報として解決される
    # ことを確認する(3Dビューが「敷地外_周辺建物」等の参考レイヤーを
    # 区別して表示するために使う)。
    wall_properties = json.loads(wall["properties"])
    assert wall_properties["layer_name"] == "壁-躯体"
    # GetDetailsOfElementsが型に依らず返す共通の"id"フィールド(Archicad UI
    # の「IDとカテゴリ」→「ID」欄)がproperties.archicad_idとして保存される
    # ことを確認する。Mesh等name(details.name)を持たない型がこれを使って
    # 名前検索できるようになるための土台(engine/site.py参照)。
    assert wall_properties["archicad_id"] == "guid-1"

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
    # ゾーンカテゴリ("住宅-1")が表示用情報として解決されることを確認する
    # (実室/大分類ゾーンの判定はgraph/envelope.pyの幾何包含が根拠であり、
    # このカテゴリ名は使わない)。
    zone_properties = json.loads(zone["properties"])
    assert zone_properties["zone_category"] == "住宅-1"


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


def test_sync_from_archicad_tool_populates_legal_conditions_for_site_zone(test_db, monkeypatch):
    # (2026-08-14追加)敷地Zoneの「法条件」プロパティ(建蔽率・容積率・
    # 前面道路幅)がproperties.legal_conditionsとして同期されることを確認する。
    from mcp.server.mcpserver import MCPServer

    fake_server = MCPServer(name="fake-tapir-legal-conditions")

    @fake_server.tool(name="GetAllElements")
    def get_all_elements(input) -> dict:
        return {"elements": [{"elementId": {"guid": "site-guid"}}]}

    @fake_server.tool(name="GetDetailsOfElements")
    def get_details_of_elements(input) -> dict:
        return {
            "detailsOfElements": [
                {
                    "type": "Zone",
                    "id": "site-guid",
                    "floorIndex": 0,
                    "layerIndex": 0,
                    "drawIndex": 0,
                    "details": {
                        "name": "敷地",
                        "polygonCoordinates": [
                            {"x": 0, "y": 0}, {"x": 10, "y": 0},
                            {"x": 10, "y": 10}, {"x": 0, "y": 10},
                        ],
                    },
                },
            ]
        }

    @fake_server.tool(name="Get3DBoundingBoxes")
    def get_bounding_boxes(input) -> dict:
        return {"boundingBoxes3D": [{"boundingBox3D": {
            "xMin": 0, "yMin": 0, "zMin": 0, "xMax": 10, "yMax": 10, "zMax": 0,
        }}]}

    @fake_server.tool(name="GetAttributesByType")
    def get_attributes_by_type(input) -> dict:
        return {"attributes": []}

    @fake_server.tool(name="GetAllProperties")
    def get_all_properties(input) -> dict:
        return {
            "properties": [
                {"propertyId": {"guid": "prop-kenpei"}, "propertyGroupName": "法条件", "propertyName": "建蔽率"},
                {"propertyId": {"guid": "prop-yoseki"}, "propertyGroupName": "法条件", "propertyName": "容積率"},
                {"propertyId": {"guid": "prop-douro"}, "propertyGroupName": "法条件", "propertyName": "前面道路幅"},
                {"propertyId": {"guid": "prop-unrelated"}, "propertyGroupName": "General", "propertyName": "Name"},
            ]
        }

    @fake_server.tool(name="GetPropertyValuesOfElements")
    def get_property_values(input) -> dict:
        # propertyValuesForElementsの各要素は{"propertyValues": [...]}という
        # オブジェクト(PropertyValuesOrError)であり、素の配列ではない
        # (archicad_mcp/tapir.pyのモジュールdocstring参照、実データでの
        # クラッシュを機に2026-08-14に修正)。
        return {
            "propertyValuesForElements": [
                {
                    "propertyValues": [
                        {"propertyValue": {"value": "60"}},
                        {"propertyValue": {"value": "200"}},
                        {"propertyValue": {"value": "6.0"}},
                    ]
                }
            ]
        }

    monkeypatch.setattr(
        archicad_client, "_default_transport", lambda: InMemoryTransport(fake_server)
    )

    result = call_mcp_tool("sync_from_archicad", {"limit": 10})
    payload = _payload(result)

    assert payload["synced"] == 1
    assert payload["requested"] == 1
    # sync_from_archicad()の戻り値からも、実際に取得できたlegal_conditionsを
    # 直接確認できる(2026-08-14追加、SQLiteキャッシュを見なくても照合キー
    # ワードが実際のプロパティ名と一致しているかを確認できる診断用の情報)。
    assert payload["legal_conditions_synced"] == {
        "site-guid": {"建蔽率": "60", "容積率": "200", "前面道路幅": "6.0"}
    }

    site = db.get_element("site-guid")
    properties = json.loads(site["properties"])
    assert properties["legal_conditions"] == {
        "建蔽率": "60",
        "容積率": "200",
        "前面道路幅": "6.0",
    }


def test_sync_from_archicad_tool_detects_site_marker_on_object_type(test_db, monkeypatch):
    # (2026-08-14実データ調査で発見・修正)実データには"Object"型要素に
    # archicad_id="敷地"が付けられているケースがあった。matches_zone_keyword()
    # のarchicad_id照合はMesh限定(敷地境界線の幾何取得という別用途向けの
    # 制約)のため、以前はこのObject要素が法条件プロパティの取得対象から
    # 漏れていた。型を問わずarchicad_id/layer_nameで拾えることを確認する。
    from mcp.server.mcpserver import MCPServer

    fake_server = MCPServer(name="fake-tapir-object-site-marker")

    @fake_server.tool(name="GetAllElements")
    def get_all_elements(input) -> dict:
        return {"elements": [{"elementId": {"guid": "obj-guid"}}]}

    @fake_server.tool(name="GetDetailsOfElements")
    def get_details_of_elements(input) -> dict:
        return {
            "detailsOfElements": [
                {
                    "type": "Object",
                    "id": "敷地",
                    "floorIndex": 0,
                    "layerIndex": 0,
                    "drawIndex": 0,
                    "details": {},
                },
            ]
        }

    @fake_server.tool(name="Get3DBoundingBoxes")
    def get_bounding_boxes(input) -> dict:
        return {"boundingBoxes3D": [{"boundingBox3D": {
            "xMin": 0, "yMin": 0, "zMin": 0, "xMax": 1, "yMax": 1, "zMax": 1,
        }}]}

    @fake_server.tool(name="GetAttributesByType")
    def get_attributes_by_type(input) -> dict:
        return {"attributes": []}

    @fake_server.tool(name="GetAllProperties")
    def get_all_properties(input) -> dict:
        return {
            "properties": [
                {"propertyId": {"guid": "prop-kenpei"}, "propertyGroupName": "法条件", "propertyName": "建蔽率"},
            ]
        }

    @fake_server.tool(name="GetPropertyValuesOfElements")
    def get_property_values(input) -> dict:
        return {
            "propertyValuesForElements": [
                {"propertyValues": [{"propertyValue": {"value": "60"}}]},
            ]
        }

    monkeypatch.setattr(
        archicad_client, "_default_transport", lambda: InMemoryTransport(fake_server)
    )

    result = call_mcp_tool("sync_from_archicad", {"limit": 10})
    payload = _payload(result)

    assert payload["legal_conditions_synced"] == {"obj-guid": {"建蔽率": "60"}}


def test_sync_from_archicad_tool_excludes_surrounding_site_mesh_from_legal_conditions(
    test_db, monkeypatch
):
    # (2026-08-14実データで発覚・修正)地盤モデリング用のMesh
    # (archicad_id="周辺敷地"、layer_name="敷地外_地盤")が、型を問わない
    # archicad_id/layer_name照合(is_site_marker_element導入前の旧実装)に
    # よって誤って敷地の目印と判定され、GetPropertyValuesOfElementsで
    # (実際には未設定の)法条件プロパティを取得・保存してしまっていた。
    # この誤った値が本来の敷地Zoneより先にresolve_legal_input()にヒットし、
    # 建蔽率が0%として解決される不具合につながった。"周辺敷地"を含む要素は
    # 法条件の同期対象からも除外されるべき(GetPropertyValuesOfElements
    # 自体が呼ばれないため、legal_conditions_syncedは空になる)。
    from mcp.server.mcpserver import MCPServer

    fake_server = MCPServer(name="fake-tapir-surrounding-site-mesh")

    @fake_server.tool(name="GetAllElements")
    def get_all_elements(input) -> dict:
        return {"elements": [{"elementId": {"guid": "mesh-guid"}}]}

    @fake_server.tool(name="GetDetailsOfElements")
    def get_details_of_elements(input) -> dict:
        return {
            "detailsOfElements": [
                {
                    "type": "Mesh",
                    "id": "周辺敷地",
                    "floorIndex": 0,
                    "layerIndex": 0,
                    "details": {"polygonCoordinates": [
                        {"x": 0, "y": 0, "z": 0}, {"x": 1, "y": 0, "z": 0},
                        {"x": 1, "y": 1, "z": 0}, {"x": 0, "y": 1, "z": 0},
                    ]},
                },
            ]
        }

    @fake_server.tool(name="Get3DBoundingBoxes")
    def get_bounding_boxes(input) -> dict:
        return {"boundingBoxes3D": [{"boundingBox3D": {
            "xMin": 0, "yMin": 0, "zMin": 0, "xMax": 1, "yMax": 1, "zMax": 1,
        }}]}

    @fake_server.tool(name="GetAttributesByType")
    def get_attributes_by_type(input) -> dict:
        return {"attributes": []}

    @fake_server.tool(name="GetAllProperties")
    def get_all_properties(input) -> dict:
        raise AssertionError("周辺敷地Meshに対してGetAllPropertiesが呼ばれてはいけない")

    monkeypatch.setattr(
        archicad_client, "_default_transport", lambda: InMemoryTransport(fake_server)
    )

    result = call_mcp_tool("sync_from_archicad", {"limit": 10})
    payload = _payload(result)

    assert payload["legal_conditions_synced"] == {}

    mesh = db.get_element("mesh-guid")
    properties = json.loads(mesh["properties"])
    assert "legal_conditions" not in properties


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
