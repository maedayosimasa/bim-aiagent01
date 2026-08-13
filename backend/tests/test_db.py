import json

import pytest

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


def test_insert_token_usage_and_get_daily_aggregates(test_db):
    db.insert_token_usage("chat", "session-1", "claude-opus-5", 1000, 500, 0.0175)
    db.insert_token_usage("chat", "session-1", "claude-opus-5", 200, 100, 0.0035)
    db.insert_token_usage("legal_report", None, "claude-opus-5", 300, 150, 0.00525)

    days = db.get_token_usage_daily()

    assert len(days) == 1
    day = days[0]
    assert day["call_count"] == 3
    assert day["input_tokens"] == 1500
    assert day["output_tokens"] == 750
    assert day["cost_usd"] == pytest.approx(0.02625)


def test_get_token_usage_by_job_groups_chat_by_session_and_report_per_run(test_db):
    db.insert_token_usage("chat", "session-1", "claude-opus-5", 1000, 500, 0.0175)
    db.insert_token_usage("chat", "session-1", "claude-opus-5", 200, 100, 0.0035)
    db.insert_token_usage("chat", "session-2", "claude-opus-5", 50, 20, 0.00075)
    db.insert_token_usage("legal_report", None, "claude-opus-5", 300, 150, 0.00525)
    db.insert_token_usage("legal_report", None, "claude-opus-5", 400, 200, 0.007)

    jobs = db.get_token_usage_by_job()
    jobs_by_id = {(row["kind"], row["job_id"]): row for row in jobs}

    # session-1は2回のchat呼び出しをまたいで1つの「作業」として合算される。
    session1 = jobs_by_id[("chat", "session-1")]
    assert session1["call_count"] == 2
    assert session1["input_tokens"] == 1200
    assert session1["output_tokens"] == 600

    session2 = jobs_by_id[("chat", "session-2")]
    assert session2["call_count"] == 1

    # legal_reportにはsession_idが無いため、実行(行)ごとに別の「作業」になる。
    report_jobs = [row for row in jobs if row["kind"] == "legal_report"]
    assert len(report_jobs) == 2
    assert all(row["call_count"] == 1 for row in report_jobs)


def test_insert_token_usage_allows_null_cost(test_db):
    db.insert_token_usage("chat", "session-1", "unknown-model", 10, 5, None)

    days = db.get_token_usage_daily()

    assert days[0]["cost_usd"] is None


def test_insert_audit_log_proposal_creates_proposed_entry(test_db):
    entry_id = db.insert_audit_log_proposal("create_x", "説明文", json.dumps({"a": 1}))

    entry = db.get_audit_log_entry(entry_id)

    assert entry["status"] == "proposed"
    assert entry["action"] == "create_x"
    assert entry["summary"] == "説明文"
    assert json.loads(entry["payload_json"]) == {"a": 1}
    assert entry["result_guid"] is None
    assert entry["decided_at"] is None


def test_get_audit_log_entry_missing_returns_none(test_db):
    assert db.get_audit_log_entry(99999) is None


def test_mark_audit_log_written_updates_status_and_guid(test_db):
    entry_id = db.insert_audit_log_proposal("create_x", "説明文", "{}")

    db.mark_audit_log_written(entry_id, "guid-abc")

    entry = db.get_audit_log_entry(entry_id)
    assert entry["status"] == "written"
    assert entry["result_guid"] == "guid-abc"
    assert entry["decided_at"] is not None


def test_mark_audit_log_failed_updates_status_and_error(test_db):
    entry_id = db.insert_audit_log_proposal("create_x", "説明文", "{}")

    db.mark_audit_log_failed(entry_id, "接続エラー")

    entry = db.get_audit_log_entry(entry_id)
    assert entry["status"] == "failed"
    assert entry["error_message"] == "接続エラー"
    assert entry["decided_at"] is not None


def test_list_audit_log_returns_newest_first(test_db):
    first_id = db.insert_audit_log_proposal("create_x", "1件目", "{}")
    second_id = db.insert_audit_log_proposal("create_x", "2件目", "{}")

    entries = db.list_audit_log()

    assert [e["id"] for e in entries] == [second_id, first_id]
