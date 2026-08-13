"""高度地区(都市計画法8条1項3号、建築基準法58条)のenvelopeを近似する。

道路斜線・隣地斜線・北側斜線(建築基準法56条)と異なり、高度地区は自治体
(都道府県・市町村)の条例が最高限度・最低限度の内容を個別に定める地域地区
であり、全国一律の計算式が存在しない。そのため、この計算は「よくある2つの
定め方(絶対高さ制限/真北方向の斜線制限型)のどちらかを前提に、条例の
具体的な数値をlegal_inputs.pyから明示的に受け取って計算する」という
枠組みのみを提供する——判定式自体を推測することはしない。

  - kubun="flat"(絶対高さ制限): 敷地全体で一律の高さ上限
    (kodo_chiku_max_height_m)。
  - kubun="north_slant"(真北方向の斜線制限型、多くの自治体条例が採用):
    engine/north_slant_envelope.pyと同じ「敷地境界線の各頂点の真北方向への
    投影座標と敷地最北端との差」を距離の近似値とし、条例が定める立ち上がり
    高さ(kodo_chiku_rise_m)・勾配(kodo_chiku_gradient)を適用する。
    kodo_chiku_max_height_mが指定されていれば、それを頭打ちの上限として
    あわせて適用する(自治体条例が併用する場合がある)。
  - いずれのkubunでも、kodo_chiku_kanwa_m(高度地区規制緩和による加算値)を
    最後に一律加算する。緩和条件(天空率・敷地内空地・角地等)を満たすか
    どうかの判定はBIMデータからはできないため、満たしていることを確認済み
    の加算値をそのままユーザーが指定する(条件判定は行わない)。

**既知の限界(結果のdisclaimerにも記載)**:
  - 最低限度(最低高さ)は未実装(対象は最高限度のみ)。
  - kubun="flat"/"north_slant"以外の定め方(例: 複数の折れ点を持つ複合的な
    斜線、方位を問わない全周からの距離を使う条例等)は表現できない。
  - north_slant_envelope.pyと同じく、道路の反対側境界線までの緩和・
    north_degreesの角度の向きは未検証の前提。

**(2026-08-14追加)`calculate_height_district_compliance()`**: 上記envelope
計算と実際の建物高さ(engine/building_height.py)を比較したPASS/FAIL用実測値
を返す。
"""

from shapely.geometry import Point, Polygon

from .building_height import building_height_points
from .legal_inputs import resolve_legal_input
from .north_slant_envelope import _north_unit_vector, resolve_north_degrees_for_compliance
from .site import get_site_boundary

_MM_PER_M = 1_000


def _resolve_float_input(key: str, override: float | None) -> float | None:
    if override is not None:
        return override
    raw = resolve_legal_input(key)
    if raw is None:
        return None
    try:
        return float(raw)
    except (TypeError, ValueError) as exc:
        raise ValueError(
            f"{key}の値が数値として解釈できません: {raw!r}(数値を設定してください)"
        ) from exc


HEIGHT_DISTRICT_ENVELOPE_DISCLAIMER = (
    "参考値です。高度地区は自治体条例が個別に最高限度・最低限度の内容を定める"
    "地域地区で、全国一律の計算式はありません。この結果はlegal_inputs.pyで"
    "明示的に指定した区分(絶対高さ制限/真北方向の斜線制限型)・数値のみに"
    "基づく機械的な計算であり、実際の条例内容と異なる場合は結果が変わります。"
    "最低限度(最低高さ)は未実装です。高度地区規制緩和による加算値"
    "(kodo_chiku_kanwa_m)は、緩和条件を満たすことを別途確認済みの値を"
    "そのまま使用しており、条件判定自体は行っていません。法的な適合を"
    "保証するものではありません。"
)


def calculate_height_district_envelope(
    north_degrees: float | None = None,
    kubun: str | None = None,
    max_height_m: float | None = None,
    rise_m: float | None = None,
    gradient: float | None = None,
    kanwa_m: float | None = None,
) -> list[dict]:
    """敷地(敷地境界線Zone)ごとに、高度地区のenvelope頂点群を返す。

    kubunが"flat"/"north_slant"以外(未設定・"none"・不正な値)、または
    必要な数値が揃わない場合、各敷地についてresolved=False(判定不能)を
    返す(推測しない)。north_slantでnorth_degreesが無い場合も同様。
    """
    kubun = kubun or resolve_legal_input("kodo_chiku_kubun")
    kanwa = _resolve_float_input("kodo_chiku_kanwa_m", kanwa_m) or 0.0
    max_height = _resolve_float_input("kodo_chiku_max_height_m", max_height_m)

    site_zones = get_site_boundary()

    def _unresolved():
        return [
            {
                "site_guid": site["guid"],
                "site_name": site["name"],
                "resolved": False,
                "kodo_chiku_kubun": kubun,
                "kanwa_m": kanwa,
                "vertices": [],
            }
            for site in site_zones
        ]

    if kubun == "flat":
        if max_height is None:
            return _unresolved()

        height_m = max_height + kanwa
        return [
            {
                "site_guid": site["guid"],
                "site_name": site["name"],
                "resolved": True,
                "kodo_chiku_kubun": kubun,
                "kanwa_m": kanwa,
                "max_height_m": max_height,
                "vertices": [
                    {"x": x, "y": y, "z_m": height_m}
                    for x, y in Polygon(site["points"]).exterior.coords[:-1]
                ],
            }
            for site in site_zones
        ]

    if kubun == "north_slant":
        rise = _resolve_float_input("kodo_chiku_rise_m", rise_m)
        grad = _resolve_float_input("kodo_chiku_gradient", gradient)

        if rise is None or grad is None or north_degrees is None:
            return _unresolved()

        north_x, north_y = _north_unit_vector(north_degrees)

        results = []
        for site in site_zones:
            coords = list(Polygon(site["points"]).exterior.coords[:-1])
            projections = [x * north_x + y * north_y for x, y in coords]
            northmost_projection = max(projections)

            vertices = []
            for (x, y), projection in zip(coords, projections):
                distance_m = (northmost_projection - projection) / _MM_PER_M
                height_m = rise + grad * distance_m
                if max_height is not None:
                    height_m = min(height_m, max_height)
                height_m += kanwa
                vertices.append({"x": x, "y": y, "z_m": height_m})

            results.append({
                "site_guid": site["guid"],
                "site_name": site["name"],
                "resolved": True,
                "kodo_chiku_kubun": kubun,
                "kanwa_m": kanwa,
                "rise_height_m": rise,
                "gradient": grad,
                "max_height_m": max_height,
                "vertices": vertices,
            })

        return results

    # kubun未設定・"none"・不正な値はいずれも「計算しない」扱い。
    return _unresolved()


def _height_limit_m_north_slant(
    x: float, y: float, northmost_projection: float, north_x: float, north_y: float,
    rise: float, grad: float, max_height: float | None, kanwa: float,
) -> float:
    projection = x * north_x + y * north_y
    distance_m = (northmost_projection - projection) / _MM_PER_M
    height_m = rise + grad * distance_m
    if max_height is not None:
        height_m = min(height_m, max_height)
    return height_m + kanwa


def calculate_height_district_compliance(
    north_degrees: float | None = None,
    kubun: str | None = None,
    max_height_m: float | None = None,
    rise_m: float | None = None,
    gradient: float | None = None,
    kanwa_m: float | None = None,
) -> list[dict]:
    """敷地(敷地境界線Zone)ごとに、高度地区envelopeと実際の建物高さ
    (Wall/Slab/Roof/Column、engine/building_height.py)を比較したPASS/FAIL用
    実測値(超過高さ、m)を返す。engine/road_slant_envelope.pyの
    `calculate_road_slant_compliance()`と同じ考え方(site単位1項目)。

    kubun="north_slant"の場合のnorth_degreesは、calculate_height_district_
    envelope()と異なりArchicadから都度取得せず、north_slant_envelope.pyの
    resolve_north_degrees_for_compliance()(legal_inputs.pyの"north_degrees"
    キー、環境変数NORTH_DEGREES)で解決する——rule_engine.py経由の合否判定は
    他のengine/*.pyと同じ「SQLiteキャッシュのみを読む、Archicad非依存」と
    いうアーキテクチャ境界を守る必要があるため。

    区分が未設定・対象外、必要な数値(絶対高さ・立ち上がり・勾配・真北方向)
    が揃わない、または敷地内に高さ情報を持つ建物要素が無い場合、
    measured_value=None(UNKNOWN)を返す。
    """
    kubun = kubun or resolve_legal_input("kodo_chiku_kubun")
    kanwa = _resolve_float_input("kodo_chiku_kanwa_m", kanwa_m) or 0.0
    max_height = _resolve_float_input("kodo_chiku_max_height_m", max_height_m)

    site_zones = get_site_boundary()
    all_points = building_height_points()

    def _unresolved(reason: str):
        return [
            {
                "target_guid": site["guid"],
                "target_name": site["name"],
                "measured_value": None,
                "evidence": {"reason": reason},
            }
            for site in site_zones
        ]

    if kubun not in ("flat", "north_slant"):
        return _unresolved("高度地区の指定区分(kodo_chiku_kubun)が未設定、または対象外です。")

    if kubun == "flat":
        if max_height is None:
            return _unresolved("高度地区の最高限度(kodo_chiku_max_height_m)が未設定です。")

        height_limit_m = max_height + kanwa

        results = []
        for site in site_zones:
            site_polygon = Polygon(site["points"])
            points_in_site = [p for p in all_points if site_polygon.contains(Point(p["x"], p["y"]))]

            if not points_in_site:
                results.append({
                    "target_guid": site["guid"],
                    "target_name": site["name"],
                    "measured_value": None,
                    "evidence": {"reason": "敷地内に高さ情報を持つ建物要素(Wall/Slab/Roof/Column)がありません。"},
                })
                continue

            worst_point = max(points_in_site, key=lambda p: p["height_m"])
            excess_m = worst_point["height_m"] - height_limit_m

            results.append({
                "target_guid": site["guid"],
                "target_name": site["name"],
                "measured_value": excess_m,
                "evidence": {
                    "kodo_chiku_kubun": kubun,
                    "worst_element_guid": worst_point["guid"],
                    "worst_element_name": worst_point["name"],
                    "worst_element_height_m": worst_point["height_m"],
                    "height_limit_m_at_worst_element": height_limit_m,
                },
            })

        return results

    # kubun == "north_slant"
    rise = _resolve_float_input("kodo_chiku_rise_m", rise_m)
    grad = _resolve_float_input("kodo_chiku_gradient", gradient)
    resolved_north_degrees = resolve_north_degrees_for_compliance(north_degrees)

    if rise is None or grad is None or resolved_north_degrees is None:
        return _unresolved(
            "高度地区の立ち上がり高さ・勾配(kodo_chiku_rise_m/kodo_chiku_gradient)、"
            "または真北方向(north_degrees)が未設定です。"
        )

    north_x, north_y = _north_unit_vector(resolved_north_degrees)

    results = []
    for site in site_zones:
        site_polygon = Polygon(site["points"])
        coords = list(site_polygon.exterior.coords[:-1])
        northmost_projection = max(x * north_x + y * north_y for x, y in coords)

        points_in_site = [p for p in all_points if site_polygon.contains(Point(p["x"], p["y"]))]

        if not points_in_site:
            results.append({
                "target_guid": site["guid"],
                "target_name": site["name"],
                "measured_value": None,
                "evidence": {"reason": "敷地内に高さ情報を持つ建物要素(Wall/Slab/Roof/Column)がありません。"},
            })
            continue

        worst_point = max(
            points_in_site,
            key=lambda p: p["height_m"] - _height_limit_m_north_slant(
                p["x"], p["y"], northmost_projection, north_x, north_y, rise, grad, max_height, kanwa
            ),
        )
        limit_m = _height_limit_m_north_slant(
            worst_point["x"], worst_point["y"], northmost_projection, north_x, north_y, rise, grad, max_height, kanwa
        )
        excess_m = worst_point["height_m"] - limit_m

        results.append({
            "target_guid": site["guid"],
            "target_name": site["name"],
            "measured_value": excess_m,
            "evidence": {
                "kodo_chiku_kubun": kubun,
                "worst_element_guid": worst_point["guid"],
                "worst_element_name": worst_point["name"],
                "worst_element_height_m": worst_point["height_m"],
                "height_limit_m_at_worst_element": limit_m,
            },
        })

    return results
