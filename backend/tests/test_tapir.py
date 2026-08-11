import asyncio

from mcp.client._memory import InMemoryTransport
from mcp.server.mcpserver import MCPServer

from backend.archicad_mcp import tapir


def _make_fake_tapir_server():
    # Response shapes match the real Tapir command/schema definitions
    # (confirmed against archicad-mcp's command_definitions.js /
    # common_schema_definitions.js), not guessed.
    #
    # Every tool takes a single untyped "input" parameter (no default),
    # mirroring archicad-mcp's actual register_tapir.py:
    # `def dynamic_command(input, _name=name, ...)`. The real Tapir
    # arguments are nested one level under "input" - a first version of
    # this fake server used flat named parameters instead and passed
    # every test while the real integration was broken (confirmed via a
    # live "'input' is a required property" error), so this shape is
    # deliberately kept faithful to the real wrapper.
    server = MCPServer(name="fake-tapir")

    @server.tool(name="GetAllElements")
    def get_all_elements(input) -> dict:
        return {
            "elements": [
                {"elementId": {"guid": "guid-1"}},
                {"elementId": {"guid": "guid-2"}},
            ]
        }

    @server.tool(name="GetElementsByType")
    def get_elements_by_type(input) -> dict:
        element_type = (input or {}).get("elementType")
        if element_type == "Wall":
            return {"elements": [{"elementId": {"guid": "guid-1"}}]}
        return {"elements": []}

    @server.tool(name="GetDetailsOfElements")
    def get_details_of_elements(input) -> dict:
        return {
            "detailsOfElements": [
                {
                    "type": "Wall",
                    "id": "guid-1",
                    "floorIndex": 0,
                    "layerIndex": 1,
                    "drawIndex": 0,
                    "details": {
                        # Archicad's own coordinates are in meters (see
                        # tapir._to_mm) - a 4m wall, not a 4000m one.
                        "geometryType": "Straight",
                        "begCoordinate": {"x": 0, "y": 0},
                        "endCoordinate": {"x": 4, "y": 0},
                        "zCoordinate": 0,
                        "height": 3,
                        "bottomOffset": 0,
                        "offset": 0,
                    },
                },
                {
                    "type": "Zone",
                    "id": "guid-2",
                    "floorIndex": 0,
                    "layerIndex": 2,
                    "drawIndex": 0,
                    "details": {
                        "name": "居室A",
                        "categoryAttributeId": {"guid": "cat-housing-1"},
                        "polygonCoordinates": [
                            {"x": 0, "y": 0},
                            {"x": 4, "y": 0},
                            {"x": 4, "y": 3},
                            {"x": 0, "y": 3},
                        ],
                    },
                },
            ]
        }

    @server.tool(name="GetAttributesByType")
    def get_attributes_by_type(input) -> dict:
        attribute_type = (input or {}).get("attributeType")

        if attribute_type == "Layer":
            # 実データ(bim_cache.db)で確認した構造を模す。guid-1(Wall)は
            # layerIndex=1のレイヤー(壁-躯体)に属するという想定。
            return {
                "attributes": [
                    {"index": 1, "name": "壁-躯体", "attributeId": {"guid": "layer-wall"}},
                    {"index": 88, "name": "敷地外_周辺建物.*", "attributeId": {"guid": "layer-context"}},
                ]
            }

        # 実データ(bim_cache.db)で確認した構造を模す: 番号なしの大分類
        # ("住宅")と、その番号付きサブタイプ("住宅-1")。guid-2のZoneは
        # サブタイプ側("住宅-1")に属する実室という想定。
        return {
            "attributes": [
                {"index": 1, "name": "住宅", "attributeId": {"guid": "cat-housing"}},
                {"index": 10, "name": "住宅-1", "attributeId": {"guid": "cat-housing-1"}},
            ]
        }

    @server.tool(name="Get3DBoundingBoxes")
    def get_3d_bounding_boxes(input) -> dict:
        return {
            "boundingBoxes3D": [
                {
                    "boundingBox3D": {
                        "xMin": 0, "yMin": 0, "zMin": 0,
                        "xMax": 1, "yMax": 0.2, "zMax": 3,
                    }
                },
                {
                    "boundingBox3D": {
                        "xMin": 1, "yMin": 0, "zMin": 0,
                        "xMax": 5, "yMax": 4, "zMax": 3,
                    }
                },
            ]
        }

    @server.tool(name="GetGeoLocation")
    def get_geo_location(input) -> dict:
        # 実際のTapirの出力形状(command_definitions.js)通り、northは
        # ラジアン。45度 = pi/4を返す。
        return {
            "projectLocation": {
                "longitude": 139.767,
                "latitude": 35.681,
                "altitude": 10.0,
                "north": 0.7853981633974483,
            },
            "surveyPoint": {
                "position": {"eastings": 0, "northings": 0, "elevation": 0},
                "geoReferencingParameters": {
                    "crsName": "", "description": "", "geodeticDatum": "",
                    "verticalDatum": "", "mapProjection": "", "mapZone": "",
                },
            },
        }

    @server.tool(name="GetSelectedElements")
    def get_selected_elements(input) -> dict:
        return {"elements": [{"elementId": {"guid": "guid-1"}}]}

    @server.tool(name="ChangeSelectionOfElements")
    def change_selection_of_elements(input) -> dict:
        payload = input or {}
        return {
            "executionResultsOfAddToSelection": [
                {"success": True} for _ in payload.get("addElementsToSelection", [])
            ],
            "executionResultsOfRemoveFromSelection": [
                {"success": True} for _ in payload.get("removeElementsFromSelection", [])
            ],
        }

    @server.tool(name="HighlightElements")
    def highlight_elements(input) -> dict:
        return {"success": True}

    @server.tool(name="MoveElements")
    def move_elements(input) -> dict:
        return {"executionResults": [{"success": True}]}

    @server.tool(name="DeleteElements")
    def delete_elements(input) -> dict:
        return {"executionResults": [{"success": True}]}

    @server.tool(name="GetAllProperties")
    def get_all_properties(input) -> dict:
        return {
            "properties": [
                {
                    "propertyId": {"guid": "prop-1"},
                    "propertyType": "StaticBuiltIn",
                    "propertyGroupName": "General",
                    "propertyName": "Name",
                    "propertyCollectionType": "Single",
                    "propertyValueType": "String",
                    "propertyMeasureType": "Undefined",
                    "propertyIsEditable": True,
                }
            ]
        }

    @server.tool(name="GetPropertyValuesOfElements")
    def get_property_values_of_elements(input) -> dict:
        return {
            "propertyValuesForElements": [
                [
                    {
                        "propertyValue": {
                            "type": "string",
                            "status": "normal",
                            "value": "Wall A",
                        }
                    }
                ]
            ]
        }

    @server.tool(name="SetPropertyValuesOfElements")
    def set_property_values_of_elements(input) -> dict:
        return {"executionResults": [{"success": True}]}

    return server


def _run(coro):
    return asyncio.run(coro)


def test_get_all_element_guids():
    transport = InMemoryTransport(_make_fake_tapir_server())

    guids = _run(tapir.get_all_element_guids(transport=transport))

    assert guids == ["guid-1", "guid-2"]


def test_get_element_guids_by_type():
    transport = InMemoryTransport(_make_fake_tapir_server())

    guids = _run(tapir.get_element_guids_by_type("Wall", transport=transport))

    assert guids == ["guid-1"]


def test_get_details_of_elements():
    transport = InMemoryTransport(_make_fake_tapir_server())

    details = _run(
        tapir.get_details_of_elements(["guid-1", "guid-2"], transport=transport)
    )

    assert [d["type"] for d in details] == ["Wall", "Zone"]
    assert details[0]["details"]["begCoordinate"] == {"x": 0, "y": 0}
    assert details[1]["details"]["name"] == "居室A"


def test_get_bounding_boxes_and_geometry_conversion():
    transport = InMemoryTransport(_make_fake_tapir_server())

    boxes = _run(
        tapir.get_bounding_boxes(["guid-1", "guid-2"], transport=transport)
    )

    assert len(boxes) == 2

    geometry = tapir.bounding_box_to_geometry(boxes[0])
    # Fake bbox is in meters (1 x 0.2 x 3) - output must be mm.
    assert geometry == {
        "type": "polygon",
        "points": [[0, 0], [1000, 0], [1000, 200], [0, 200]],
        "z_min": 0,
        "z_max": 3000,
    }


def test_get_geo_location_adds_north_degrees():
    transport = InMemoryTransport(_make_fake_tapir_server())

    result = _run(tapir.get_geo_location(transport=transport))

    assert result["projectLocation"]["north"] == 0.7853981633974483
    assert result["north_degrees"] == 45.0


def test_get_zone_categories():
    transport = InMemoryTransport(_make_fake_tapir_server())

    categories = _run(tapir.get_zone_categories(transport=transport))

    assert {c["name"] for c in categories} == {"住宅", "住宅-1"}


def test_get_layer_names():
    transport = InMemoryTransport(_make_fake_tapir_server())

    layers = _run(tapir.get_layer_names(transport=transport))

    assert {layer["name"] for layer in layers} == {"壁-躯体", "敷地外_周辺建物.*"}


def test_get_selected_element_guids():
    transport = InMemoryTransport(_make_fake_tapir_server())

    guids = _run(tapir.get_selected_element_guids(transport=transport))

    assert guids == ["guid-1"]


def test_select_elements_removes_current_then_adds_new():
    transport = InMemoryTransport(_make_fake_tapir_server())

    result = _run(tapir.select_elements(["guid-2"], transport=transport))

    assert result["executionResultsOfRemoveFromSelection"] == [{"success": True}]
    assert result["executionResultsOfAddToSelection"] == [{"success": True}]


def test_select_elements_empty_only_clears_current_selection():
    transport = InMemoryTransport(_make_fake_tapir_server())

    result = _run(tapir.select_elements([], transport=transport))

    assert result["executionResultsOfRemoveFromSelection"] == [{"success": True}]
    assert result["executionResultsOfAddToSelection"] == []


def test_highlight_elements():
    transport = InMemoryTransport(_make_fake_tapir_server())

    result = _run(tapir.highlight_elements(["guid-1"], transport=transport))

    assert result["success"] is True


def test_highlight_elements_empty_clears_highlight():
    transport = InMemoryTransport(_make_fake_tapir_server())

    result = _run(tapir.highlight_elements([], transport=transport))

    assert result["success"] is True


def test_focus_elements_selects_and_highlights():
    transport = InMemoryTransport(_make_fake_tapir_server())

    result = _run(tapir.focus_elements(["guid-2"], transport=transport))

    assert result["selection"]["executionResultsOfAddToSelection"] == [{"success": True}]
    assert result["highlight"]["success"] is True


def test_move_element():
    transport = InMemoryTransport(_make_fake_tapir_server())

    result = _run(
        tapir.move_element("guid-1", 100, 200, 0, transport=transport)
    )

    assert result["executionResults"][0]["success"] is True


def test_delete_elements():
    transport = InMemoryTransport(_make_fake_tapir_server())

    result = _run(tapir.delete_elements(["guid-1"], transport=transport))

    assert result["executionResults"][0]["success"] is True


def test_list_properties():
    transport = InMemoryTransport(_make_fake_tapir_server())

    properties = _run(tapir.list_properties(transport=transport))

    assert properties[0]["propertyName"] == "Name"
    assert properties[0]["propertyId"]["guid"] == "prop-1"


def test_get_property_values():
    transport = InMemoryTransport(_make_fake_tapir_server())

    values = _run(
        tapir.get_property_values(["guid-1"], ["prop-1"], transport=transport)
    )

    assert values[0][0]["propertyValue"]["value"] == "Wall A"


def test_set_property_value():
    transport = InMemoryTransport(_make_fake_tapir_server())

    result = _run(
        tapir.set_property_value("guid-1", "prop-1", "New Name", transport=transport)
    )

    assert result["executionResults"][0]["success"] is True


def test_details_to_geometry_straight_wall_uses_centerline():
    # Input is in meters (a real 4m wall); output must be converted to mm.
    geometry = tapir.details_to_geometry(
        "Wall",
        {
            "geometryType": "Straight",
            "begCoordinate": {"x": 0, "y": 0},
            "endCoordinate": {"x": 4, "y": 0},
        },
    )

    assert geometry == {"type": "line", "points": [[0, 0], [4000, 0]]}


def test_details_to_geometry_polygonal_wall_uses_outline():
    geometry = tapir.details_to_geometry(
        "Wall",
        {
            "geometryType": "Polygonal",
            "polygonOutline": [
                {"x": 0, "y": 0},
                {"x": 0.1, "y": 0},
                {"x": 0.1, "y": 0.05},
            ],
        },
    )

    assert geometry == {
        "type": "polygon",
        "points": [[0, 0], [100, 0], [100, 50]],
    }


def test_details_to_geometry_zone_uses_real_boundary():
    # Matches the real-world magnitude observed from an actual project
    # (e.g. [0.15, 4.65]..[19.05, 19.35]) - meters, not millimeters.
    geometry = tapir.details_to_geometry(
        "Zone",
        {
            "name": "居室A",
            "polygonCoordinates": [
                {"x": 0, "y": 0},
                {"x": 4, "y": 0},
                {"x": 4, "y": 3},
                {"x": 0, "y": 3},
            ],
        },
    )

    assert geometry == {
        "type": "polygon",
        "points": [[0, 0], [4000, 0], [4000, 3000], [0, 3000]],
    }


def test_details_to_geometry_falls_back_to_bounding_box_for_unhandled_type():
    bbox_item = {
        "boundingBox3D": {
            "xMin": 0, "yMin": 0, "zMin": 0,
            "xMax": 0.9, "yMax": 0.1, "zMax": 2.1,
        }
    }

    geometry = tapir.details_to_geometry("Door", {}, bbox_item)

    assert geometry == tapir.bounding_box_to_geometry(bbox_item)


def test_details_to_geometry_falls_back_on_degenerate_zone_polygon():
    # Fewer than 3 points can't form a valid polygon.
    geometry = tapir.details_to_geometry(
        "Zone", {"polygonCoordinates": [{"x": 0, "y": 0}]}, None
    )

    assert geometry == {"type": "point", "x": 0, "y": 0}


def test_details_to_name_uses_real_zone_name():
    assert tapir.details_to_name("Zone", "guid-1234", {"name": "居室A"}) == "居室A"


def test_details_to_name_falls_back_to_synthetic_name():
    assert tapir.details_to_name("Zone", "abcdefgh-1234", {}) == "Zone_abcdefgh"
    assert tapir.details_to_name("Wall", "abcdefgh-1234", {"name": "ignored"}) == (
        "Wall_abcdefgh"
    )


def test_calling_without_input_wrapper_fails_like_the_real_server():
    # Regression test: tapir._call() used to pass Tapir arguments directly
    # as the top-level MCP tool arguments instead of nesting them under
    # "input" - that broke against the real archicad-mcp with
    # "'input' is a required property". This fake server now requires
    # "input" the same way, so calling without it must fail here too.
    from backend.archicad_mcp import client as archicad_client

    transport = InMemoryTransport(_make_fake_tapir_server())

    async def _call_without_wrapper():
        return await archicad_client.call_tool(
            "GetAllElements", {}, transport=transport
        )

    result = _run(_call_without_wrapper())
    assert result.is_error is True


def test_bounding_box_to_geometry_handles_missing_box():
    assert tapir.bounding_box_to_geometry({"error": {"code": 1}}) == {
        "type": "point",
        "x": 0,
        "y": 0,
    }


def test_z_range_from_bounding_box_returns_empty_when_box_missing():
    # details_to_geometry()は、Get3DBoundingBoxesが対応していない要素型
    # (Text/Dimension等、"error"だけを返す)に対してもこの関数を呼ぶため、
    # boundingBox3Dキー自体が無いケースを個別に扱える必要がある。
    assert tapir._z_range_from_bounding_box({"error": "Not yet supported element type"}) == {}


def test_call_returns_structured_content_when_present(monkeypatch):
    # 自前のMCPサーバー(mcp SDKベース)はstructured_contentを常に返すが、
    # 既存テストのフェイクサーバー(fastmcpベース)はこれを保証しないため、
    # テキストcontentへのフォールバック経路(_call()の後半)しか
    # これまで踏んでいなかった。structured_contentがある場合の経路を
    # 直接確認する。
    from backend.archicad_mcp import client as archicad_client

    class _FakeResult:
        is_error = False
        structured_content = {"elements": ["fake"]}
        content = []

    async def fake_call_tool(name, arguments, transport=None):
        return _FakeResult()

    monkeypatch.setattr(archicad_client, "call_tool", fake_call_tool)

    result = _run(tapir._call("SomeCommand"))

    assert result == {"elements": ["fake"]}


def test_select_elements_returns_empty_results_when_nothing_to_change():
    # 現在の選択も新規選択も両方空の場合、Tapirへ問い合わせるまでもなく
    # 空の実行結果を即座に返す(_make_fake_tapir_server()のGetSelected
    # Elementsは常にguid-1を返すため、既存テストではこの分岐に到達
    # できない)。
    empty_selection_server = MCPServer(name="fake-tapir-empty-selection")

    @empty_selection_server.tool(name="GetSelectedElements")
    def get_selected_elements(input) -> dict:
        return {"elements": []}

    transport = InMemoryTransport(empty_selection_server)

    result = _run(tapir.select_elements([], transport=transport))

    assert result == {
        "executionResultsOfAddToSelection": [],
        "executionResultsOfRemoveFromSelection": [],
    }
