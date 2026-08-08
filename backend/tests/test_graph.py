import json

import networkx as nx

from backend.graph.builder import build_graph
from backend.graph.topology import build_topology
from backend.graph.analyzer import (
    analyze_graph,
    find_isolated_elements,
    find_degenerate_walls,
    find_ambiguous_door_ownership,
    ISOLATED_ELEMENT_MESSAGES_JA,
    RELATION_TARGET_TYPES,
)
from backend.engine.relation_builder import rebuild_connections


def test_analyze_graph_reports_connected_layout(sample_elements):
    rebuild_connections()

    graph = build_graph()
    graph = build_topology(graph)

    info = analyze_graph(graph)

    assert info == {"nodes": 4, "edges": 4, "connected": True}


def test_analyze_graph_on_empty_graph():
    info = analyze_graph(nx.Graph())

    assert info == {"nodes": 0, "edges": 0, "connected": False}


def test_analyze_graph_ignores_out_of_scope_types_for_connected(sample_elements):
    # Column等RELATION_RULESに登場しない要素タイプは、どんな距離でも
    # determine_relation()がNoneを返すため永遠に孤立ノードのまま残る。
    # これがconnected判定に混ざると、壁/部屋/ドアが完全に連結していても
    # 常にFalseになってしまう不具合があった(実データ728要素のうち590件が
    # Column/Beam/Slab等でこれに該当し、connectedが常にFalseになっていた)。
    sample_elements.insert_element(
        "column001", "Column", "孤立した柱",
        json.dumps({}),
        json.dumps({"type": "point", "x": 999999, "y": 999999}),
    )

    rebuild_connections()

    graph = build_graph()
    graph = build_topology(graph)

    info = analyze_graph(graph)

    # nodes/edgesは全要素(対象外タイプ含む)を引き続き反映する。
    assert info["nodes"] == 5
    assert info["edges"] == 4
    # connectedは関係計算の対象タイプ(壁/部屋/ドア/窓)のみで判定するため、
    # 孤立したColumnの存在に影響されずTrueのまま。
    assert info["connected"] is True


def test_relation_target_types_matches_isolated_element_messages():
    # ISOLATED_ELEMENT_MESSAGES_JA(孤立要素検出の対象タイプ)は
    # RELATION_TARGET_TYPES(RELATION_RULESが実際にカバーするタイプ)と
    # 常に一致していなければならない。ずれると「関係を持ちうるのに孤立検出
    # 対象外」あるいは「関係を持ちえないのに孤立警告が出続ける」型が生まれる
    # (Windowにルールが無かったために孤立検出できなかった過去の不具合と
    # 同種のドリフトを防ぐためのガード)。
    assert set(ISOLATED_ELEMENT_MESSAGES_JA) == RELATION_TARGET_TYPES


def test_find_isolated_elements_reports_none_when_all_connected(sample_elements):
    # wall001/door001/room001/room002は互いに境界を共有しており、全要素が
    # 何らかの関係を持つ(test_analyze_graph_reports_connected_layoutと同じ
    # データで edges=4 かつ connected=True であることを前提にしている)。
    rebuild_connections()

    graph = build_graph()
    graph = build_topology(graph)

    assert find_isolated_elements(graph) == []


def test_find_isolated_elements_reports_orphan_element(sample_elements):
    # room001/room002から大きく離れた窓を1件追加する。どの壁とも隣接/接続
    # の距離閾値(relation_rules.py)に収まらないため孤立要素として検出される
    # はず。
    sample_elements.insert_element(
        "window001", "Window", "孤立した窓",
        json.dumps({"width": 900, "height": 1200}),
        json.dumps({"type": "point", "x": 100000, "y": 100000}),
    )

    rebuild_connections()

    graph = build_graph()
    graph = build_topology(graph)

    issues = find_isolated_elements(graph)

    assert len(issues) == 1
    assert issues[0]["element_guid"] == "window001"
    assert issues[0]["element_type"] == "Window"
    assert issues[0]["type"] == "isolated_element"


def test_find_isolated_elements_ignores_unmapped_types(test_db):
    # Object等、ISOLATED_ELEMENT_MESSAGES_JAに無いタイプは孤立していても
    # 対象外(壁・部屋・ドア・窓以外は現状このチェックの対象ではない)。
    test_db.insert_element(
        "object001", "Object", "孤立した什器",
        json.dumps({}),
        json.dumps({"type": "point", "x": 0, "y": 0}),
    )

    graph = build_graph()
    graph = build_topology(graph)

    assert find_isolated_elements(graph) == []


def test_find_degenerate_walls_reports_zero_length_wall(test_db):
    test_db.insert_element(
        "wall_bad", "Wall", "退化した壁",
        json.dumps({}),
        json.dumps({"type": "line", "points": [[4000, 0], [4000, 0]]}),
    )

    graph = build_graph()

    issues = find_degenerate_walls(graph)

    assert len(issues) == 1
    assert issues[0]["element_guid"] == "wall_bad"
    assert issues[0]["type"] == "degenerate_wall_geometry"


def test_find_degenerate_walls_ignores_normal_wall(sample_elements):
    graph = build_graph()

    assert find_degenerate_walls(graph) == []


def test_find_degenerate_walls_reports_invalid_geometry_json(test_db):
    # 破損したジオメトリ文字列(実データでの座標欠損等を想定)も、
    # 長さ0と同様に「ジオメトリ不正」として報告する。
    test_db.insert_element(
        "wall_broken", "Wall", "壊れた壁",
        json.dumps({}),
        "not valid json",
    )

    graph = build_graph()

    issues = find_degenerate_walls(graph)

    assert len(issues) == 1
    assert issues[0]["element_guid"] == "wall_broken"


def test_find_ambiguous_door_ownership_reports_door_near_two_walls(sample_elements):
    # wall001(x=4000)に加え、door001(4000,1500)からわずか20mmしか離れて
    # いないwall002(x=4020)を追加する。DOOR_OWNER_MAX_DISTANCE_MM(50mm)
    # 以内に2枚の壁の芯線が同時に来るため、所属が一意に定まらない。
    sample_elements.insert_element(
        "wall002", "Wall", "近接した別の壁",
        json.dumps({"thickness": 150, "height": 3000}),
        json.dumps({"type": "line", "points": [[4020, 0], [4020, 3000]]}),
    )

    rebuild_connections()

    graph = build_graph()
    graph = build_topology(graph)

    issues = find_ambiguous_door_ownership(graph)

    assert len(issues) == 1
    assert issues[0]["element_guid"] == "door001"
    assert issues[0]["type"] == "ambiguous_door_wall_ownership"


def test_find_ambiguous_door_ownership_ignores_wall_beyond_ownership_threshold(sample_elements):
    # 実データ(bim_cache.db、ドア101件)で見つかった不具合の再現:
    # wall002をdoor001(4000,1500)から300mm離れた位置(x=4300)に置く。
    # Wall-Doorの一般的な"adjacent"閾値(600mm)には収まるためcalculate_
    # relations()はエッジを張るが、実データの検証で「ドアの真の所属壁は
    # 常に距離0mmで見つかる」ことが分かっており、300mmはどう見ても別の
    # (ドアが埋め込まれていない)壁である。所属判定はこの一般閾値を
    # そのまま使わず、DOOR_OWNER_MAX_DISTANCE_MM(50mm)で絞るべきで、
    # このwall002は所属候補に数えず"ambiguous"にしてはならない。
    sample_elements.insert_element(
        "wall002", "Wall", "遠い別の壁",
        json.dumps({"thickness": 150, "height": 3000}),
        json.dumps({"type": "line", "points": [[4300, 0], [4300, 3000]]}),
    )

    rebuild_connections()

    graph = build_graph()
    graph = build_topology(graph)

    # 一般的な"adjacent"閾値には収まるため、グラフ上のエッジ自体は張られる
    # ことを前提として確認しておく(そうでなければこのテストの意図と違う)。
    assert graph.edges["door001", "wall002"]["relation"] == "adjacent"

    assert find_ambiguous_door_ownership(graph) == []


def test_find_ambiguous_door_ownership_ignores_single_wall(sample_elements):
    rebuild_connections()

    graph = build_graph()
    graph = build_topology(graph)

    assert find_ambiguous_door_ownership(graph) == []


def test_find_ambiguous_door_ownership_with_real_sync_geometry_shape(test_db):
    # sample_elementsのdoor001は"point"型だが、実際にArchicadから同期
    # されるドア/窓のジオメトリは常に"polygon"型(bounding_box_to_geometry
    # ()、archicad_mcp/tapir.py)で、"point"型は一度も来ない。所属判定
    # ロジックがpolygon型でも正しく動くことを、実データ(bim_cache.db)から
    # 実際に観測した形(ドアの矩形footprintが壁の芯線をまたぐ)を模して
    # 確認する。
    test_db.insert_element(
        "wall001", "Wall", "所属壁",
        json.dumps({"thickness": 150, "height": 3000}),
        json.dumps({"type": "line", "points": [[4000, 0], [4000, 3000]]}),
    )
    test_db.insert_element(
        "door001", "Door", "実データ形式のドア",
        json.dumps({"width": 900, "height": 2100}),
        json.dumps({
            "type": "polygon",
            "points": [[3915, 1050], [4085, 1050], [4085, 1950], [3915, 1950]],
        }),
    )

    rebuild_connections()

    graph = build_graph()
    graph = build_topology(graph)

    assert find_ambiguous_door_ownership(graph) == []

    wall_neighbors = [
        n for n in graph.neighbors("door001")
        if graph.nodes[n].get("type") == "Wall"
    ]
    assert wall_neighbors == ["wall001"]
