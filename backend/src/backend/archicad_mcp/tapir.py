"""Typed helpers for the specific Tapir JSON API commands this project uses.

Shapes were confirmed against archicad-mcp's actual Tapir definitions
(src/tapir/command_definitions.js, common_schema_definitions.js) - not
guessed. Notably:

- GetAllElements / GetElementsByType return {"elements": [{"elementId":
  {"guid": ...}}, ...]}.
- GetDetailsOfElements returns {"detailsOfElements": [{"type": <str
  ElementType enum, e.g. "Wall"/"Zone">, "id": <guid str>, "floorIndex",
  "layerIndex", "drawIndex", "details": <huge type-specific structure>}]}.
  Archicad calls rooms "Zone", not "Room" - callers that compare against
  this project's own RELATION_RULES (which uses "Room") need to account
  for that; this module intentionally does not rename it.
- Get3DBoundingBoxes returns {"boundingBoxes3D": [{"boundingBox3D": {xMin,
  yMin, zMin, xMax, yMax, zMax}} | {"error": ...}]}, same order as input.
- "details" (from GetDetailsOfElements) is a `oneOf` union with no
  discriminator field of its own - which shape it is depends entirely on
  the sibling "type" field. Confirmed against WallDetails/ZoneDetails:
  - Wall: {"geometryType": "Straight"|"Trapezoid"|"Polygonal",
    "begCoordinate": {x,y}, "endCoordinate": {x,y}, ...,
    "polygonOutline": [{x,y}, ...] (only for "Polygonal")}. beg/end is the
    wall's centerline, not its outline - real thickness is not captured
    as a shape, only as the separate begThickness/endThickness numbers.
  - Zone: {"name": str, "polygonCoordinates": [{x,y}, ...] (>=3), ...} -
    this is the room's real boundary and a real display name, unlike
    every other type here.
  - Everything else (Door, Window, Object, ...) has no direct 2D outline
    in "details" - LibPartBasedElementDetails only carries the owning
    wall's id, not a position. Get3DBoundingBoxes remains the only
    available geometry source for those.
  Curved segments ("polygonArcs") are not tessellated - arcs are treated
  as straight edges between their endpoints, a deliberate simplification.
- MoveElements takes a *relative* vector (elementsWithMoveVectors), not an
  absolute position - it cannot be used to implement "set to this exact
  geometry".
- SetPropertyValuesOfElements accepts propertyValue as the simplified
  {"value": "<display string>"} form; GetPropertyValuesOfElements returns
  the fuller typed union (NormalStringPropertyValue etc.) - this module
  returns that raw structure rather than guessing a normalized shape.
"""

import json

from . import client as archicad_client


async def _call(name, arguments=None, transport=None):
    # archicad-mcp wraps every dynamically-registered Tapir command behind
    # a single "input" parameter (see its register_tapir.py:
    # `def dynamic_command(input, ...)`), so the real Tapir arguments must
    # be nested one level under "input" rather than passed as the MCP
    # tool's top-level arguments - confirmed against a real "'input' is a
    # required property" error from the live server.
    result = await archicad_client.call_tool(
        name, {"input": arguments}, transport=transport
    )

    if result.is_error:
        text = "; ".join(
            block.text for block in result.content if hasattr(block, "text")
        )
        raise RuntimeError(f"Archicad tool {name!r} failed: {text}")

    if result.structured_content is not None:
        return result.structured_content

    # archicad-mcp is built on a different MCP server implementation
    # (fastmcp) than our own (mcp SDK) - structured_content population
    # isn't guaranteed, so fall back to parsing the text content as JSON.
    text = "".join(block.text for block in result.content if hasattr(block, "text"))
    return json.loads(text) if text else {}


def _element_ids(guids):
    return [{"elementId": {"guid": guid}} for guid in guids]


async def get_all_element_guids(transport=None):
    payload = await _call("GetAllElements", transport=transport)
    return [item["elementId"]["guid"] for item in payload.get("elements", [])]


async def get_element_guids_by_type(element_type, transport=None):
    payload = await _call(
        "GetElementsByType", {"elementType": element_type}, transport=transport
    )
    return [item["elementId"]["guid"] for item in payload.get("elements", [])]


async def get_details_of_elements(guids, transport=None):
    payload = await _call(
        "GetDetailsOfElements", {"elements": _element_ids(guids)}, transport=transport
    )
    return payload.get("detailsOfElements", [])


async def get_bounding_boxes(guids, transport=None):
    payload = await _call(
        "Get3DBoundingBoxes", {"elements": _element_ids(guids)}, transport=transport
    )
    return payload.get("boundingBoxes3D", [])


async def move_element(guid, dx, dy, dz=0, copy=False, transport=None):
    return await _call(
        "MoveElements",
        {
            "elementsWithMoveVectors": [
                {
                    "elementId": {"guid": guid},
                    "moveVector": {"x": dx, "y": dy, "z": dz},
                    "copy": copy,
                }
            ]
        },
        transport=transport,
    )


async def delete_elements(guids, transport=None):
    return await _call(
        "DeleteElements", {"elements": _element_ids(guids)}, transport=transport
    )


async def list_properties(transport=None):
    payload = await _call("GetAllProperties", transport=transport)
    return payload.get("properties", [])


async def get_property_values(guids, property_guids, transport=None):
    payload = await _call(
        "GetPropertyValuesOfElements",
        {
            "elements": _element_ids(guids),
            "properties": [
                {"propertyId": {"guid": guid}} for guid in property_guids
            ],
        },
        transport=transport,
    )
    return payload.get("propertyValuesForElements", [])


async def set_property_value(guid, property_guid, value, transport=None):
    return await _call(
        "SetPropertyValuesOfElements",
        {
            "elementPropertyValues": [
                {
                    "elementId": {"guid": guid},
                    "propertyId": {"guid": property_guid},
                    "propertyValue": {"value": str(value)},
                }
            ]
        },
        transport=transport,
    )


# Archicad's JSON/Tapir API expresses all coordinates in meters, while
# this project's geometry model (RELATION_RULES max_distance, the
# synthetic test/demo data in main.py) is in millimeters throughout.
# Confirmed empirically: real Zone polygonCoordinates came back as e.g.
# [0.15, 4.65]..[19.05, 19.35] for an actual building footprint - values
# that only make sense as meters. Every coordinate read from Archicad
# must be converted here, at the ingestion boundary, so nothing
# downstream needs to know which unit a given number originally was in.
_METERS_TO_MM = 1000


def _to_mm(value):
    return value * _METERS_TO_MM


def bounding_box_to_geometry(bounding_box_item):
    """Convert one Get3DBoundingBoxes() array item to our geometry schema.

    Uses the XY footprint of the 3D bounding box as an approximate
    polygon - this is not the element's real (possibly non-rectangular)
    outline. Used only as a fallback by details_to_geometry() for types
    that don't carry a real 2D shape in "details" (see module docstring).
    """

    if not isinstance(bounding_box_item, dict):
        return {"type": "point", "x": 0, "y": 0}

    box = bounding_box_item.get("boundingBox3D")

    if box is None:
        return {"type": "point", "x": 0, "y": 0}

    x_min, y_min = _to_mm(box["xMin"]), _to_mm(box["yMin"])
    x_max, y_max = _to_mm(box["xMax"]), _to_mm(box["yMax"])

    return {
        "type": "polygon",
        "points": [
            [x_min, y_min],
            [x_max, y_min],
            [x_max, y_max],
            [x_min, y_max],
        ],
    }


def _coords_to_points(coords):
    return [[_to_mm(c["x"]), _to_mm(c["y"])] for c in coords]


def details_to_geometry(element_type, details, bounding_box_item=None):
    """Build our geometry schema from Tapir's real per-type "details".

    Falls back to bounding_box_to_geometry() when the type has no direct
    2D outline (most types), or when the expected fields are missing/too
    sparse to form a valid shape (e.g. a degenerate <3-point polygon).
    """

    details = details or {}

    if element_type == "Wall":
        if details.get("geometryType") == "Polygonal":
            outline = details.get("polygonOutline") or []
            if len(outline) >= 3:
                return {"type": "polygon", "points": _coords_to_points(outline)}
        beg = details.get("begCoordinate")
        end = details.get("endCoordinate")
        if beg is not None and end is not None:
            return {
                "type": "line",
                "points": [
                    [_to_mm(beg["x"]), _to_mm(beg["y"])],
                    [_to_mm(end["x"]), _to_mm(end["y"])],
                ],
            }

    elif element_type == "Zone":
        coords = details.get("polygonCoordinates") or []
        if len(coords) >= 3:
            return {"type": "polygon", "points": _coords_to_points(coords)}

    return bounding_box_to_geometry(bounding_box_item)


def details_to_name(element_type, guid, details):
    """Prefer Archicad's own display name when "details" carries one.

    Only Zone details include a real user-facing name; every other type
    falls back to a synthetic "<Type>_<short guid>" label.
    """

    if element_type == "Zone":
        name = (details or {}).get("name")
        if name:
            return name

    return f"{element_type}_{guid[:8]}"
