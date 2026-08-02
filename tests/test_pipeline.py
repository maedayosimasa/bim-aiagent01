from backend.engine.spatial import analyze_space


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
