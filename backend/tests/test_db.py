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


def _sample_legal_report_checks():
    # building_coverage_ratioを模した、threshold(基準値)・evidence(参照値・
    # 途中結果)・status(判定)を全て含むcheck。
    return [
        {
            "rule_id": "building_coverage_ratio",
            "title": "建蔽率(参考値、建築基準法53条)",
            "concept_id": "building_coverage_ratio",
            "threshold": 0.9,
            "threshold_unit": "ratio",
            "comparator": "lte",
            "disclaimer": "参考値です。",
            "legal_sources": [],
            "items": [
                {
                    "target_guid": "site1",
                    "target_name": "敷地",
                    "status": "pass",
                    "measured_value": 0.761,
                    "unit": "ratio",
                    "evidence": {"building_area_m2": 258.3, "site_area_m2": 339.5},
                }
            ],
            "missing_inputs": [],
        }
    ]


def test_save_legal_report_persists_full_check_detail(test_db):
    checks = _sample_legal_report_checks()

    generated_at = db.save_legal_report("レポート本文", checks)

    entry = db.get_legal_report_history_entry(1)
    assert entry["generated_at"] == generated_at
    assert entry["report"] == "レポート本文"

    saved_checks = json.loads(entry["checks_json"])
    assert saved_checks == checks
    # 基準値(threshold)・参照値/途中結果(evidence)・判定(status)が
    # 最終結果(measured_value)だけでなく全て保存されていることを確認する。
    item = saved_checks[0]["items"][0]
    assert saved_checks[0]["threshold"] == 0.9
    assert item["status"] == "pass"
    assert item["evidence"] == {"building_area_m2": 258.3, "site_area_m2": 339.5}


def test_get_legal_report_history_entry_missing_returns_none(test_db):
    assert db.get_legal_report_history_entry(99999) is None


def test_list_legal_report_history_returns_newest_first_without_checks_json(test_db):
    checks = _sample_legal_report_checks()
    db.save_legal_report("1件目", checks)
    db.save_legal_report("2件目", checks)

    entries = db.list_legal_report_history()

    assert [e["report"] for e in entries] == ["2件目", "1件目"]
    assert set(entries[0].keys()) == {"id", "generated_at", "report", "reused_from_id"}


def test_list_legal_report_history_respects_limit(test_db):
    checks = _sample_legal_report_checks()
    for i in range(3):
        db.save_legal_report(f"{i}件目", checks)

    entries = db.list_legal_report_history(limit=2)

    assert len(entries) == 2


def test_save_legal_report_records_reused_from_id(test_db):
    # (2026-08-14追加、差分キャッシュ)LLMを呼ばずレポート文を再利用した
    # 場合、参照元行のidをreused_from_idに記録する。
    checks = _sample_legal_report_checks()
    first_id_generated_at = db.save_legal_report("1件目", checks)
    first_entry = db.list_legal_report_history()[0]

    db.save_legal_report("1件目(再利用)", checks, reused_from_id=first_entry["id"])

    entries = db.list_legal_report_history()
    assert entries[0]["reused_from_id"] == first_entry["id"]
    assert entries[1]["reused_from_id"] is None
    assert first_id_generated_at  # 型確認(生成時刻が返る既存の契約を壊していないこと)


def test_get_latest_legal_report_returns_none_when_empty(test_db):
    assert db.get_latest_legal_report() is None


def test_get_latest_legal_report_returns_most_recent_entry(test_db):
    checks1 = _sample_legal_report_checks()
    checks2 = _sample_legal_report_checks()
    checks2[0]["items"][0]["status"] = "fail"

    db.save_legal_report("1件目", checks1)
    db.save_legal_report("2件目", checks2)

    latest = db.get_latest_legal_report()

    assert latest["report"] == "2件目"
    assert json.loads(latest["checks_json"]) == checks2
