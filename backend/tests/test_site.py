import json

from backend.engine.site import (
    find_zones_by_name,
    get_site_boundary,
    get_road_boundaries,
)


def test_find_zones_by_name_matches_substring(test_db):
    test_db.insert_element(
        "zone_site", "Zone", "敷地境界",
        json.dumps({}),
        json.dumps({"type": "polygon", "points": [[0, 0], [10000, 0], [10000, 10000], [0, 10000]]}),
    )
    test_db.insert_element(
        "zone_room", "Zone", "洋室",
        json.dumps({}),
        json.dumps({"type": "polygon", "points": [[0, 0], [4000, 0], [4000, 3000], [0, 3000]]}),
    )

    matches = find_zones_by_name("敷地")

    assert [m["guid"] for m in matches] == ["zone_site"]


def test_find_zones_by_name_ignores_non_zone_types(test_db):
    # Line/PolyLine等はTapirから実境界を取得できない(GetDetailsOfElements
    # が"Not yet supported element type"を返す)ため、名前が一致しても
    # Zone/Room以外のタイプは対象外。
    test_db.insert_element(
        "line_site", "Line", "敷地境界線",
        json.dumps({}),
        json.dumps({"type": "polygon", "points": [[0, 0], [10000, 0], [10000, 10000], [0, 10000]]}),
    )

    assert find_zones_by_name("敷地") == []


def test_get_site_boundary_returns_points_and_area(test_db):
    test_db.insert_element(
        "zone_site", "Zone", "敷地",
        json.dumps({}),
        json.dumps({"type": "polygon", "points": [[0, 0], [10000, 0], [10000, 8000], [0, 8000]]}),
    )

    boundary = get_site_boundary()

    assert len(boundary) == 1
    assert boundary[0]["guid"] == "zone_site"
    # 10m x 8m = 80平方メートル
    assert boundary[0]["area_m2"] == 80.0


def test_get_site_boundary_empty_when_not_found(sample_elements):
    assert get_site_boundary() == []


def test_get_site_boundary_excludes_mesh_with_gaichi_layer_name(test_db):
    # 実データで発覚した不具合: 地盤モデリング用のMeshのlayer_nameが
    # 「敷地外_地盤」(=敷地の"外側"の地盤)という、"敷地"を含むが意味は
    # 反転した複合語だったため、素朴な部分一致では誤って敷地境界線として
    # 検出されてしまっていた。
    test_db.insert_element(
        "zone_site", "Zone", "敷地",
        json.dumps({}),
        json.dumps({"type": "polygon", "points": [[0, 0], [10000, 0], [10000, 8000], [0, 8000]]}),
    )
    test_db.insert_element(
        "mesh_ground", "Mesh", "Mesh_12345678",
        json.dumps({"archicad_id": "道路", "layer_name": "敷地外_地盤"}),
        json.dumps({
            "type": "polygon",
            "points": [[-30000, -30000], [-20000, -30000], [-20000, -20000], [-30000, -20000]],
        }),
    )

    boundary = get_site_boundary()

    assert [b["guid"] for b in boundary] == ["zone_site"]


def test_get_site_boundary_excludes_mesh_with_shuhen_shikichi_id(test_db):
    # 実データで発覚した不具合: 隣接・周辺の別敷地を示すMeshの
    # archicad_id「周辺敷地」も"敷地"を含むが、当該敷地そのものではない。
    test_db.insert_element(
        "zone_site", "Zone", "敷地",
        json.dumps({}),
        json.dumps({"type": "polygon", "points": [[0, 0], [10000, 0], [10000, 8000], [0, 8000]]}),
    )
    test_db.insert_element(
        "mesh_surrounding", "Mesh", "Mesh_abcdefgh",
        json.dumps({"archicad_id": "周辺敷地"}),
        json.dumps({
            "type": "polygon",
            "points": [[-30000, -30000], [30000, -30000], [30000, 30000], [-30000, 30000]],
        }),
    )

    boundary = get_site_boundary()

    assert [b["guid"] for b in boundary] == ["zone_site"]


def test_get_road_boundaries_estimates_width_and_centerline(test_db):
    # 幅6m x 長さ20mの帯状の道路Zone。
    test_db.insert_element(
        "zone_road", "Zone", "道路",
        json.dumps({}),
        json.dumps({
            "type": "polygon",
            "points": [[0, 0], [6000, 0], [6000, 20000], [0, 20000]],
        }),
    )

    roads = get_road_boundaries()

    assert len(roads) == 1
    road = roads[0]
    assert road["estimated_width_m"] == 6.0

    xs = {round(p[0]) for p in road["estimated_centerline"]}
    ys = {round(p[1]) for p in road["estimated_centerline"]}
    # 中心線は幅方向の中点(x=3000)を通り、道路の長さ方向(y=0〜20000)を結ぶ。
    assert xs == {3000}
    assert ys == {0, 20000}


def test_find_zones_by_name_matches_mesh_by_archicad_id(test_db):
    # MeshDetailsには"name"が無いため、Meshの実名は常に合成名
    # ("Mesh_<guid先頭8文字>")になり、道路等のキーワードと一致しない。
    # 実データで確認済みの通り、GetDetailsOfElementsが型に依らず返す共通の
    # "id"フィールド(Archicad UIの「IDとカテゴリ」→「ID」欄)を
    # properties.archicad_idとして保存し、これで照合する。
    test_db.insert_element(
        "mesh_road", "Mesh", "Mesh_12345678",
        json.dumps({"archicad_id": "前面道路"}),
        json.dumps({
            "type": "polygon",
            "points": [[0, 0], [6000, 0], [6000, 20000], [0, 20000]],
        }),
    )

    matches = find_zones_by_name("道路")

    assert [m["guid"] for m in matches] == ["mesh_road"]


def test_find_zones_by_name_matches_mesh_by_layer_name(test_db):
    # archicad_idが無い/一致しない場合のフォールバックとして
    # properties.layer_name(sync_from_archicad()が解決するレイヤー名)でも照合する。
    test_db.insert_element(
        "mesh_road", "Mesh", "Mesh_12345678",
        json.dumps({"layer_name": "前面道路"}),
        json.dumps({
            "type": "polygon",
            "points": [[0, 0], [6000, 0], [6000, 20000], [0, 20000]],
        }),
    )

    matches = find_zones_by_name("道路")

    assert [m["guid"] for m in matches] == ["mesh_road"]


def test_find_zones_by_name_mesh_without_matching_layer_name_excluded(test_db):
    test_db.insert_element(
        "mesh_other", "Mesh", "Mesh_abcdefgh",
        json.dumps({"layer_name": "外構"}),
        json.dumps({
            "type": "polygon",
            "points": [[0, 0], [6000, 0], [6000, 20000], [0, 20000]],
        }),
    )

    assert find_zones_by_name("道路") == []


def test_get_road_boundaries_supports_mesh(test_db):
    test_db.insert_element(
        "mesh_road", "Mesh", "Mesh_12345678",
        json.dumps({"layer_name": "前面道路"}),
        json.dumps({
            "type": "polygon",
            "points": [[0, 0], [6000, 0], [6000, 20000], [0, 20000]],
        }),
    )

    roads = get_road_boundaries()

    assert len(roads) == 1
    assert roads[0]["guid"] == "mesh_road"
    assert roads[0]["estimated_width_m"] == 6.0


def test_find_zones_by_name_skips_invalid_geometry_json(test_db):
    # 破損したジオメトリ文字列(実データでの座標欠損等を想定)。名前が
    # 一致してもジオメトリが解釈できない場合は結果から除外する。
    test_db.insert_element(
        "zone_site", "Zone", "敷地",
        json.dumps({}),
        "not valid json",
    )

    assert find_zones_by_name("敷地") == []


def test_find_zones_by_name_skips_non_polygon_geometry(test_db):
    # 敷地・道路のZoneは通常polygonだが、防御的に他の形状も除外する。
    test_db.insert_element(
        "zone_site", "Zone", "敷地",
        json.dumps({}),
        json.dumps({"type": "point", "x": 0, "y": 0}),
    )

    assert find_zones_by_name("敷地") == []


def test_get_road_boundaries_handles_degenerate_polygon(test_db):
    # 3点が一直線上に並ぶなど、面積の無い(退化した)ポリゴン。
    # minimum_rotated_rectangle()がPolygonではなくLineString等を返す
    # ケースを、幅員・中心線をNoneにして安全に扱えることを確認する。
    test_db.insert_element(
        "zone_road", "Zone", "道路",
        json.dumps({}),
        json.dumps({
            "type": "polygon",
            "points": [[0, 0], [1000, 0], [2000, 0], [0, 0]],
        }),
    )

    roads = get_road_boundaries()

    assert len(roads) == 1
    assert roads[0]["estimated_width_m"] is None
    assert roads[0]["estimated_centerline"] is None


def test_get_road_boundaries_estimates_width_when_long_edge_comes_first(test_db):
    # 最小外接矩形の辺の並び順によってはedge_lengths[0]の方が長辺になる
    # (shapelyの矩形の向きは入力の頂点順とは独立に決まるため)。その場合の
    # 分岐(if edge_lengths[0] <= edge_lengths[1])を確認する。
    test_db.insert_element(
        "zone_road", "Zone", "道路",
        json.dumps({}),
        json.dumps({
            "type": "polygon",
            "points": [[0, 0], [20000, 0], [20000, 6000], [0, 6000]],
        }),
    )

    roads = get_road_boundaries()

    assert roads[0]["estimated_width_m"] == 6.0


def test_get_road_boundaries_supports_multiple_roads(test_db):
    # 角地など、道路が複数本ある場合も全件返す。
    test_db.insert_element(
        "zone_road_front", "Zone", "前面道路",
        json.dumps({}),
        json.dumps({"type": "polygon", "points": [[0, 0], [4000, 0], [4000, 15000], [0, 15000]]}),
    )
    test_db.insert_element(
        "zone_road_side", "Zone", "側面道路",
        json.dumps({}),
        json.dumps({"type": "polygon", "points": [[0, 0], [3000, 0], [3000, 12000], [0, 12000]]}),
    )

    roads = get_road_boundaries()

    assert {r["guid"] for r in roads} == {"zone_road_front", "zone_road_side"}
    assert all(r["estimated_width_m"] is not None for r in roads)
