import json

import pytest

from backend.engine.effective_daylighting import (
    _daylighting_coefficient,
    calculate_effective_daylighting,
    get_land_use_category,
)


def _insert_room_with_south_window(test_db, *, with_roof=True, with_site_boundary=True):
    # room1: 4m x 3m の部屋。南側(y=0)の壁に窓を1つ持つ。
    test_db.insert_element(
        "room1", "Room", "洋室",
        json.dumps({}),
        json.dumps({"type": "polygon", "points": [[0, 0], [4000, 0], [4000, 3000], [0, 3000]]}),
    )
    # 南側の壁(芯線y=0)。厚みはarchicad_detailsに無いため既定値(150mm)を使う。
    test_db.insert_element(
        "wall1", "Wall", "南壁",
        json.dumps({}),
        json.dumps({"type": "line", "points": [[0, 0], [4000, 0]]}),
    )
    # 窓: wall1所属、幅1m×高さ1m、z中心=1450mm。
    test_db.insert_element(
        "window1", "Window", "窓",
        json.dumps({
            "archicad_details": {
                "width": 1.0, "height": 1.0,
                "ownerElementType": "Wall", "ownerElementId": {"guid": "wall1"},
            },
        }),
        json.dumps({"type": "point", "x": 2000, "y": 0, "z_min": 1400, "z_max": 1500}),
    )

    if with_site_boundary:
        # 敷地境界線: 南端がy=-3000(窓からの水平距離D=3000mmになる)。
        test_db.insert_element(
            "site1", "Zone", "敷地境界線",
            json.dumps({}),
            json.dumps({
                "type": "polygon",
                "points": [[-2000, -3000], [9000, -3000], [9000, 10000], [-2000, 10000]],
            }),
        )

    if with_roof:
        # 窓の直上を覆う屋根。z_min=7450mm → H = 7450 - 1450 = 6000mm。
        test_db.insert_element(
            "roof1", "Roof", "屋根",
            json.dumps({}),
            json.dumps({
                "type": "polygon",
                "points": [[1500, -500], [2500, -500], [2500, 500], [1500, 500]],
                "z_min": 7450, "z_max": 7500,
            }),
        )


def test_daylighting_coefficient_caps_and_floors():
    # 障害物がほぼ無い(比率が大きい)場合は上限3.0でクランプされる。
    assert _daylighting_coefficient(10.0, "residential") == 3.0
    # 比率が小さすぎる(算定値が負になる)場合は0でクランプされる。
    assert _daylighting_coefficient(0.0, "residential") == 0.0
    # 上限にも下限にもかからない値はそのまま。
    assert _daylighting_coefficient(0.5, "residential") == pytest.approx(0.5 * 6.0 - 1.4)


def test_get_land_use_category_defaults_to_residential(test_db, monkeypatch):
    monkeypatch.delenv("LAND_USE_CATEGORY", raising=False)
    assert get_land_use_category() == "residential"

    monkeypatch.setenv("LAND_USE_CATEGORY", "commercial")
    assert get_land_use_category() == "commercial"


def test_get_land_use_category_normalizes_official_zone_name_from_env_var(test_db, monkeypatch):
    # (2026-08-14追加)get_land_use_category()はresolve_legal_input()経由に
    # なり、正式な用途地域名(env var経由でも)を3分類へ変換できる。
    monkeypatch.setenv("LAND_USE_CATEGORY", "第一種住居地域")
    assert get_land_use_category() == "residential"


def test_get_land_use_category_falls_back_to_default_for_unset_placeholder(test_db, monkeypatch):
    # (2026-08-14実データで発覚した500エラーの回帰テスト)Archicadのピック
    # リストプロパティが未選択のまま同期されると値は「未設定」になり、
    # 以前はnormalize_land_use_category()がValueErrorを送出して法規レポート
    # 生成全体が500エラーになっていた。未設定と同じ扱いになり既定値
    # (residential)へフォールバックすることを確認する。
    monkeypatch.delenv("LAND_USE_CATEGORY", raising=False)
    test_db.insert_element(
        "site1", "Zone", "敷地",
        json.dumps({"legal_conditions": {"用途地域": "未設定"}}),
        json.dumps({"type": "polygon", "points": [[0, 0], [10000, 0], [10000, 10000], [0, 10000]]}),
    )

    assert get_land_use_category() == "residential"


def test_calculate_effective_daylighting_resolves_d_and_h(test_db):
    _insert_room_with_south_window(test_db)

    result = calculate_effective_daylighting(land_use_category="residential")

    assert result["land_use_category"] == "residential"
    room = result["rooms"][0]

    assert room["window_count"] == 1
    assert room["unresolved_window_count"] == 0

    window = room["window_details"][0]
    assert window["resolved"] is True
    # D=3000mm, H=6000mm → 採光関係比率0.5 → 係数 = 0.5*6.0-1.4 = 1.6
    assert window["coefficient"] == pytest.approx(1.6)
    assert window["area_m2"] == pytest.approx(1.0)
    assert window["effective_area_m2"] == pytest.approx(1.6)

    assert room["effective_window_area_m2"] == pytest.approx(1.6)
    assert room["floor_area_m2"] == pytest.approx(12.0)
    assert room["ratio"] == pytest.approx(1.6 / 12.0)
    # 1.6/12 ≈ 0.133 は1/7(≈0.143)未満なのでFAIL相当の値になる。
    assert room["ratio"] < 1 / 7


def test_calculate_effective_daylighting_excludes_site_and_road_zones(test_db):
    # 敷地境界線・前面道路のZoneが「部屋」として採光チェック対象に紛れ込む
    # 不具合の回帰テスト(room_polygons(開口部の外側方向判定)は元々除外して
    # いたが、room_resultsを作るループには適用されていなかった)。
    _insert_room_with_south_window(test_db)

    result = calculate_effective_daylighting(land_use_category="residential")

    assert [r["room_guid"] for r in result["rooms"]] == ["room1"]
    assert result["excluded_site_or_road_count"] == 1


def test_calculate_effective_daylighting_no_overhang_is_most_favorable(test_db):
    # 直上に屋根/スラブが無い(屋上に面する窓等)場合は、障害物が無い
    # 最も有利なケースとして係数の上限3.0を採用する。
    _insert_room_with_south_window(test_db, with_roof=False)

    result = calculate_effective_daylighting(land_use_category="residential")
    window = result["rooms"][0]["window_details"][0]

    assert window["resolved"] is True
    assert window["coefficient"] == 3.0


def test_calculate_effective_daylighting_unresolved_when_no_site_boundary(test_db):
    # 敷地境界線がモデル化されていないと水平距離Dが求まらず未解決になる。
    # 未解決の窓がある部屋の値が閾値未満の場合、判定不能(ratio=None)に
    # なる(実際の値がもっと高い可能性を否定できないため)。
    _insert_room_with_south_window(test_db, with_site_boundary=False)

    result = calculate_effective_daylighting(land_use_category="residential")
    room = result["rooms"][0]

    assert room["unresolved_window_count"] == 1
    assert room["window_details"][0]["resolved"] is False
    assert room["ratio"] is None


def test_calculate_effective_daylighting_road_uses_far_side_distance(test_db):
    # room1 + wall1 + window1(南向き)を用意し、敷地境界線の代わりに
    # 前面道路Zoneを窓の南側に置く。近い方の境界(y=-500)ではなく、
    # 法令の規定通り道路の反対側の境界(y=-2000)までの距離が使われる
    # ことを確認する(D=2000mm、近い方なら500mmになってしまう)。
    test_db.insert_element(
        "room1", "Room", "洋室",
        json.dumps({}),
        json.dumps({"type": "polygon", "points": [[0, 0], [4000, 0], [4000, 3000], [0, 3000]]}),
    )
    test_db.insert_element(
        "wall1", "Wall", "南壁",
        json.dumps({}),
        json.dumps({"type": "line", "points": [[0, 0], [4000, 0]]}),
    )
    test_db.insert_element(
        "window1", "Window", "窓",
        json.dumps({
            "archicad_details": {
                "width": 1.0, "height": 1.0,
                "ownerElementType": "Wall", "ownerElementId": {"guid": "wall1"},
            },
        }),
        json.dumps({"type": "point", "x": 2000, "y": 0, "z_min": 1400, "z_max": 1500}),
    )
    test_db.insert_element(
        "roof1", "Roof", "屋根",
        json.dumps({}),
        json.dumps({
            "type": "polygon",
            "points": [[1500, -500], [2500, -500], [2500, 500], [1500, 500]],
            "z_min": 7450, "z_max": 7500,
        }),
    )
    test_db.insert_element(
        "road1", "Zone", "前面道路",
        json.dumps({}),
        json.dumps({
            "type": "polygon",
            "points": [[-1000, -2000], [5000, -2000], [5000, -500], [-1000, -500]],
        }),
    )

    result = calculate_effective_daylighting(land_use_category="residential")
    window = result["rooms"][0]["window_details"][0]

    # D=2000mm(道路の反対側境界), H=6000mm → 比率1/3 → 係数 = 1/3*6-1.4 = 0.6
    assert window["coefficient"] == pytest.approx(0.6)
