"""agent/tools.pyの一覧・スナップショット系ツールの出力サイズ対策のテスト。

2026-08-11に実データ(5708要素)でLLMのコンテキスト上限(Claude Opus 5は
100万トークン)を超過する事故が起きた(list_bim_elements_toolが全要素を
properties/geometry込みで無制限に返しており、実測で約224万トークンあった)。
その根本原因への回帰テスト——一覧/スナップショット系ツールは絞り込み条件を
省略すると件数サマリのみを返し、指定時も返却件数の上限でクランプされることを
確認する。
"""

import asyncio
import json

from backend.agent import tools as agent_tools
from backend.engine.relation_builder import rebuild_connections


def test_list_bim_elements_tool_default_is_summary_only(sample_elements):
    result = json.loads(agent_tools.list_bim_elements_tool.invoke({}))

    assert result["total"] == 4
    assert result["by_type"] == {"Wall": 1, "Door": 1, "Room": 2}
    # properties/geometryを含む全件ダンプになっていないことを確認する
    # (これが2026-08-11の事故の直接の原因だった)。
    assert "elements" not in result


def test_list_bim_elements_tool_filtered_returns_compact_fields(sample_elements):
    result = json.loads(
        agent_tools.list_bim_elements_tool.invoke({"element_type": "Room"})
    )

    assert result["matched_total"] == 2
    assert result["returned"] == 2
    for element in result["elements"]:
        assert set(element.keys()) == {"guid", "type", "name"}


def test_list_bim_elements_tool_caps_at_element_list_limit(test_db):
    total = agent_tools._ELEMENT_LIST_LIMIT + 20

    for i in range(total):
        test_db.insert_element(
            f"wall{i:04d}", "Wall", f"壁{i}",
            json.dumps({}),
            json.dumps({"type": "line", "points": [[0, 0], [1, 1]]}),
        )

    # 呼び出し側(LLM)が上限を超える値を指定してもクランプされることを確認する。
    result = json.loads(
        agent_tools.list_bim_elements_tool.invoke(
            {"element_type": "Wall", "limit": 10_000}
        )
    )

    assert result["matched_total"] == total
    assert result["returned"] == agent_tools._ELEMENT_LIST_LIMIT


def test_get_bim_element_tool_returns_full_detail(sample_elements):
    result = json.loads(agent_tools.get_bim_element_tool.invoke({"guid": "door001"}))

    assert result["guid"] == "door001"
    assert result["properties"] == {"width": 900, "height": 2100}


def test_get_bim_element_tool_missing_guid_returns_error(sample_elements):
    result = json.loads(
        agent_tools.get_bim_element_tool.invoke({"guid": "does-not-exist"})
    )

    assert "error" in result


def test_search_bim_elements_tool_caps_n_results(monkeypatch):
    captured = {}

    def fake_search_elements(query, n_results=5):
        captured["n_results"] = n_results
        return []

    monkeypatch.setattr(agent_tools, "search_elements", fake_search_elements)

    agent_tools.search_bim_elements_tool.invoke({"query": "test", "n_results": 10_000})

    assert captured["n_results"] == agent_tools._SEARCH_RESULTS_LIMIT


def test_get_graph_relation_snapshot_tool_default_is_summary_only(sample_elements):
    rebuild_connections()

    result = json.loads(agent_tools.get_graph_relation_snapshot_tool.invoke({}))

    assert "total" in result
    assert "by_relation" in result
    assert "relations" not in result


def test_get_graph_relation_snapshot_tool_filtered_by_relation(sample_elements):
    rebuild_connections()

    summary = json.loads(agent_tools.get_graph_relation_snapshot_tool.invoke({}))
    some_relation = next(iter(summary["by_relation"]))

    result = json.loads(
        agent_tools.get_graph_relation_snapshot_tool.invoke({"relation": some_relation})
    )

    assert result["matched_total"] == summary["by_relation"][some_relation]
    assert all(row["relation"] == some_relation for row in result["relations"])


def test_analyze_bim_space_tool_drops_graph_data(sample_elements):
    result = json.loads(agent_tools.analyze_bim_space_tool.invoke({}))

    assert "graph_data" not in result
    assert "graph" in result


def test_engine_windows_tool_classifies_by_adjacent_room_count(test_db):
    test_db.insert_element(
        "room1", "Room", "居室A",
        json.dumps({}),
        json.dumps({"type": "polygon", "points": [[0, 0], [4000, 0], [4000, 3000], [0, 3000]]}),
    )
    test_db.insert_element(
        "window_ext", "Window", "外部窓",
        json.dumps({}),
        json.dumps({"type": "point", "x": -100, "y": 1500}),
    )

    result = json.loads(agent_tools.engine_windows_tool.invoke({}))

    assert result["total"] == 1
    assert result["exterior_count"] == 1
    assert result["windows"][0]["classification"] == "exterior"


def test_engine_legal_inputs_tool_lists_definitions_with_used_by(monkeypatch):
    monkeypatch.setenv("LAND_USE_CATEGORY", "residential")

    result = json.loads(agent_tools.engine_legal_inputs_tool.invoke({}))

    by_key = {entry["key"]: entry for entry in result}
    assert by_key["land_use_category"]["value"] == "residential"
    assert by_key["land_use_category"]["used_by_rule_ids"] == ["effective_daylighting_ratio"]


def test_engine_legal_rules_evaluate_tool_surfaces_missing_inputs(sample_elements, monkeypatch):
    monkeypatch.delenv("LAND_USE_CATEGORY", raising=False)
    monkeypatch.delenv("LEGAL_API_URL", raising=False)

    result = json.loads(
        asyncio.run(
            agent_tools.engine_legal_rules_evaluate_tool.ainvoke({"rule_id": "effective_daylighting_ratio"})
        )
    )

    assert result["items"] == []
    assert [m["key"] for m in result["missing_inputs"]] == ["land_use_category"]
