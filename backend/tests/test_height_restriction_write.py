import asyncio
import json

import pytest

from backend.database import db as db_module
from backend.engine import height_restriction_write


def _insert_site_and_road(test_db):
    test_db.insert_element(
        "site1", "Zone", "敷地",
        json.dumps({}),
        json.dumps({"type": "polygon", "points": [[0, 0], [10000, 0], [10000, 10000], [0, 10000]]}),
    )
    test_db.insert_element(
        "road1", "Zone", "前面道路",
        json.dumps({}),
        json.dumps({"type": "polygon", "points": [[-1000, -4000], [11000, -4000], [11000, 0], [-1000, 0]]}),
    )


def test_propose_creates_proposed_audit_log_entry(test_db, monkeypatch):
    monkeypatch.setenv("LAND_USE_CATEGORY", "residential")
    _insert_site_and_road(test_db)

    result = height_restriction_write.propose_road_slant_envelope_mesh()

    assert len(result["proposals"]) == 1
    proposal = result["proposals"][0]
    assert proposal["envelope"]["site_guid"] == "site1"

    entry = db_module.get_audit_log_entry(proposal["proposal_id"])
    assert entry["status"] == "proposed"
    assert entry["action"] == height_restriction_write.ACTION_ROAD_SLANT_ENVELOPE_MESH
    assert entry["result_guid"] is None


def test_propose_skips_unresolved_sites(test_db, monkeypatch):
    monkeypatch.setenv("LAND_USE_CATEGORY", "residential")
    test_db.insert_element(
        "site1", "Zone", "敷地",
        json.dumps({}),
        json.dumps({"type": "polygon", "points": [[0, 0], [10000, 0], [10000, 10000], [0, 10000]]}),
    )
    # 前面道路が無いため判定不能 → 提案は作られない。

    result = height_restriction_write.propose_road_slant_envelope_mesh()

    assert result["proposals"] == []
    assert db_module.list_audit_log() == []


def test_approve_writes_mesh_and_marks_written(test_db, monkeypatch):
    monkeypatch.setenv("LAND_USE_CATEGORY", "residential")
    _insert_site_and_road(test_db)

    captured = {}

    async def fake_create_mesh(vertices_mm, *args, **kwargs):
        captured["vertices_mm"] = vertices_mm
        return {"elements": [{"elementId": {"guid": "guid-new-mesh"}}]}

    monkeypatch.setattr(height_restriction_write.tapir, "create_mesh", fake_create_mesh)

    proposal = height_restriction_write.propose_road_slant_envelope_mesh()["proposals"][0]

    result = asyncio.run(
        height_restriction_write.approve_road_slant_envelope_mesh(proposal["proposal_id"])
    )

    assert result["result_guid"] == "guid-new-mesh"

    entry = db_module.get_audit_log_entry(proposal["proposal_id"])
    assert entry["status"] == "written"
    assert entry["result_guid"] == "guid-new-mesh"
    assert entry["decided_at"] is not None

    # z(mm)はenvelopeのz_m(メートル)から1000倍して渡される。
    first_vertex = captured["vertices_mm"][0]
    assert first_vertex["z"] == pytest.approx(proposal["envelope"]["vertices"][0]["z_m"] * 1000)


def test_approve_rejects_unknown_proposal_id(test_db):
    with pytest.raises(ValueError):
        asyncio.run(height_restriction_write.approve_road_slant_envelope_mesh(99999))


def test_approve_rejects_already_processed_proposal(test_db, monkeypatch):
    monkeypatch.setenv("LAND_USE_CATEGORY", "residential")
    _insert_site_and_road(test_db)

    async def fake_create_mesh(*args, **kwargs):
        return {"elements": [{"elementId": {"guid": "guid-new-mesh"}}]}

    monkeypatch.setattr(height_restriction_write.tapir, "create_mesh", fake_create_mesh)

    proposal = height_restriction_write.propose_road_slant_envelope_mesh()["proposals"][0]
    asyncio.run(height_restriction_write.approve_road_slant_envelope_mesh(proposal["proposal_id"]))

    with pytest.raises(ValueError):
        asyncio.run(
            height_restriction_write.approve_road_slant_envelope_mesh(proposal["proposal_id"])
        )


def test_approve_marks_failed_and_reraises_on_error(test_db, monkeypatch):
    monkeypatch.setenv("LAND_USE_CATEGORY", "residential")
    _insert_site_and_road(test_db)

    async def failing_create_mesh(*args, **kwargs):
        raise RuntimeError("Archicad tool 'CreateMeshes' failed: connection lost")

    monkeypatch.setattr(height_restriction_write.tapir, "create_mesh", failing_create_mesh)

    proposal = height_restriction_write.propose_road_slant_envelope_mesh()["proposals"][0]

    with pytest.raises(RuntimeError):
        asyncio.run(
            height_restriction_write.approve_road_slant_envelope_mesh(proposal["proposal_id"])
        )

    entry = db_module.get_audit_log_entry(proposal["proposal_id"])
    assert entry["status"] == "failed"
    assert "connection lost" in entry["error_message"]
