import json

from backend.database import db
from backend.engine.spatial import analyze_space


def test_analyze_space_counts_zone_elements_as_rooms(test_db):
    # Archicadの実データは部屋を"Room"ではなく"Zone"と呼ぶため、
    # analyze_space()の要素分類も両方を部屋として数えなければならない。
    test_db.insert_element(
        "zone001", "Zone", "ゾーンA",
        json.dumps({}),
        json.dumps({"type": "polygon", "points": [[0, 0], [1000, 0], [1000, 1000], [0, 1000]]}),
    )

    result = analyze_space("test-model")

    assert result["elements"]["rooms"] == 1


def test_analyze_space_counts_windows(sample_elements):
    # sample_elementsには窓が無いため、analyze_space()の要素分類がWindow型を
    # 正しく数えることを別途確認する。
    sample_elements.insert_element(
        "window001", "Window", "窓",
        json.dumps({"width": 900, "height": 1200}),
        json.dumps({"type": "point", "x": 4000, "y": 500}),
    )

    result = analyze_space("test-model")

    assert result["elements"]["windows"] == 1


def test_analyze_space_end_to_end(sample_elements):
    # analyze_space() must rebuild connections itself, not rely on a
    # previous manual rebuild - otherwise the graph always has 0 edges.
    result = analyze_space("test-model")

    assert result["model_id"] == "test-model"
    assert result["elements"] == {
        "walls": 1,
        "doors": 1,
        "windows": 0,
        "rooms": 2,
    }

    # Regression: "graph" was previously defined twice in the result dict,
    # silently discarding analyze_graph()'s output (including "connected").
    assert result["graph"] == {"nodes": 4, "edges": 4, "connected": True}

    assert len(result["graph_data"]["nodes"]) == 4
    assert len(result["graph_data"]["edges"]) == 4


def test_analyze_space_persists_engine_snapshot_for_verification(sample_elements):
    result = analyze_space("test-model")

    row = db.get_engine_analysis_result()

    assert row["model_id"] == "test-model"
    assert row["node_count"] == result["graph"]["nodes"]
    assert row["edge_count"] == result["graph"]["edges"]
    assert bool(row["connected"]) == result["graph"]["connected"]
    assert row["wall_count"] == 1
    assert row["door_count"] == 1
    assert row["window_count"] == 0
    assert row["room_count"] == 2


def test_analyze_space_snapshot_is_replaced_not_accumulated(sample_elements):
    analyze_space("test-model")
    analyze_space("test-model")

    conn = db.get_connection()
    count = conn.execute("SELECT COUNT(*) FROM engine_analysis_results").fetchone()[0]
    conn.close()

    assert count == 1


def test_analyze_space_persists_graph_relation_results_with_types(sample_elements):
    analyze_space("test-model")

    rows = db.get_graph_relation_results()
    by_pair = {(r["source_guid"], r["target_guid"]): r for r in rows}

    wall_door = by_pair[("wall001", "door001")]
    assert wall_door["source_type"] == "Wall"
    assert wall_door["target_type"] == "Door"
    assert wall_door["relation"] == "adjacent"


def test_analyze_space_relation_results_replaced_not_accumulated(sample_elements):
    analyze_space("test-model")
    first_count = len(db.get_graph_relation_results())

    analyze_space("test-model")
    second_count = len(db.get_graph_relation_results())

    assert first_count == second_count > 0
