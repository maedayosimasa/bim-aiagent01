"""北側斜線制限(建築基準法56条1項3号)のenvelopeを幾何的に近似する。

法令の規定: 第一種/第二種低層住居専用地域・田園住居地域(立ち上がり5m)、
第一種/第二種中高層住居専用地域(立ち上がり10m)に限り、建築物の各部分の
高さは、立ち上がり高さに、真北方向の水平距離に勾配1.25を乗じた値を加えた
値以下としなければならない。それ以外の用途地域では適用されない。

適用区分(低層/中高層/対象外)はBIMデータからは判定できないプロジェクト
単位の情報のため、engine/legal_inputs.pyの新規キー"kitagawa_shasen_kubun"
として明示的な入力を要求する(未設定・"not_applicable"の場合は計算しない
——他の用途地域推定と同様、推測しない)。

真北方向はArchicadのGetGeoLocation(archicad_mcp/tapir.py)からのみ取得
できる、プロジェクト単位の設定値でありSQLiteキャッシュには保存されない。
この関数自体はnorth_degreesを引数として受け取るだけで、Archicadへは
問い合わせない——他のengine/*.pyが「SQLiteキャッシュのみを読む、Archicad
非依存」という既存のアーキテクチャ境界を守っているのに合わせ、Archicadへの
都度問い合わせはmain.pyのエンドポイント層(_run_archicad_action経由)が
担う。

**距離の近似方法**: 敷地境界線の各頂点について、真北方向への投影座標を
計算し、敷地全体で最も北にある頂点の投影座標との差を「真北方向の水平
距離」の近似値とする(隣地境界線・道路の区別、道路の反対側境界線までの
緩和は考慮しない簡略化)。

**既知の限界(結果のdisclaimerにも記載)**:
  - 対象区域かどうかの判定はkitagawa_shasen_kubunの明示的な入力に依存する
    (推測しない)。
  - 北側の前面道路に対する反対側境界線までの緩和(道路斜線と同様の規定)は
    未実装——常に敷地自身の北端までの距離を使う。
  - north_degreesの角度の向き(時計回り/反時計回り、基準軸)は未検証の前提
    (_north_unit_vector()のdocstring参照)。実データ・実際の敷地図と
    突き合わせて確認することを推奨する。
  - 天空率による代替緩和は未実装。

**(2026-08-14追加)`calculate_north_slant_compliance()`**: 上記envelope計算と
実際の建物高さ(engine/building_height.py)を比較したPASS/FAIL用実測値を
返す。上記envelope計算(表示専用)とは異なり、north_degreesはArchicadから
都度取得せずlegal_inputs.pyの"north_degrees"キー(環境変数NORTH_DEGREES)
から解決する(rule_engine.py経由のRULE_CHECK_REGISTRYはSQLiteキャッシュ
のみを読む前提のため)。
"""

import math

from shapely.geometry import Point, Polygon

from .building_height import building_height_points
from .legal_inputs import normalize_land_use_category, resolve_legal_input
from .site import get_site_boundary

_MM_PER_M = 1_000

# 建築基準法56条1項3号: 勾配は区分に関わらず常に1.25。
_GRADIENT = 1.25

_RISE_HEIGHTS_M = {
    "low_rise": 5.0,
    "mid_rise": 10.0,
}

NORTH_SLANT_ENVELOPE_DISCLAIMER = (
    "参考値です。建築基準法56条1項3号の北側斜線制限を、敷地境界線の各頂点の"
    "真北方向への投影座標と敷地最北端との差(真北方向の水平距離の近似)に"
    "勾配1.25・立ち上がり高さを乗じて近似したものです。適用区分(低層/"
    "中高層/対象外)はlegal_inputs.pyのkitagawa_shasen_kubunで明示的に指定した"
    "値を使用しており、実際の用途地域と異なる場合は結果が変わります。真北の"
    "角度の向き(時計回り/反時計回り)は未検証の前提であり、実際の敷地図・"
    "方位記号と突き合わせて確認してください。北側の前面道路に対する反対側"
    "境界線までの緩和(道路斜線と同様の規定)、天空率による代替緩和は未実装"
    "です。法的な適合を保証するものではありません。"
)


def get_kitagawa_shasen_kubun() -> str | None:
    """legal_inputs.pyのkitagawa_shasen_kubunを解決する。

    (2026-08-14追加)明示的な指定が無い場合、land_use_categoryに正式な
    用途地域名(例:「第一種低層住居専用地域」)が設定されていれば、そこから
    自動で導出する(normalize_land_use_category())。land_use_categoryが
    無い、または粗い3分類("residential"等)のみで細分類が判定できない
    場合はNone(未設定)を返す(推測しない)。
    """
    explicit = resolve_legal_input("kitagawa_shasen_kubun")
    if explicit is not None:
        return explicit

    raw_land_use = resolve_legal_input("land_use_category")
    if raw_land_use is None:
        return None
    try:
        _category, kubun = normalize_land_use_category(raw_land_use)
    except ValueError:
        return None
    return kubun


def _north_unit_vector(north_degrees: float) -> tuple[float, float]:
    """north_degreesから真北方向の単位ベクトル(プロジェクトのXY座標系)を返す。

    Archicadの北方向角度は、プロジェクトのY+軸を基準に時計回りに測った
    真北の方位角として扱う(未検証の前提——NORTH_SLANT_ENVELOPE_DISCLAIMER
    参照)。
    """
    radians = math.radians(north_degrees)
    return (math.sin(radians), math.cos(radians))


def _height_limit_m(
    x: float, y: float, northmost_projection: float, north_x: float, north_y: float, rise_height_m: float
) -> float:
    """北側斜線制限による、(x, y)の直上に許容される高さ上限(m)を返す。

    calculate_north_slant_envelope()の頂点ループと
    calculate_north_slant_compliance()の両方から使う共通ロジック。
    northmost_projectionは敷地境界線の頂点群から求めた基準値(呼び出し側で
    計算する)。
    """
    projection = x * north_x + y * north_y
    distance_m = (northmost_projection - projection) / _MM_PER_M
    return rise_height_m + _GRADIENT * distance_m


def calculate_north_slant_envelope(
    north_degrees: float, kitagawa_shasen_kubun: str | None = None
) -> list[dict]:
    """敷地(敷地境界線Zone)ごとに、北側斜線制限のenvelope頂点群を返す。

    適用区分(kitagawa_shasen_kubun)が未設定・"not_applicable"・不正な値の
    場合、各敷地についてresolved=False(判定不能)を返す(北側斜線制限が
    そもそも適用されるかどうかが不明なため)。
    """
    kubun = kitagawa_shasen_kubun or get_kitagawa_shasen_kubun()
    site_zones = get_site_boundary()

    if kubun not in _RISE_HEIGHTS_M:
        return [
            {
                "site_guid": site["guid"],
                "site_name": site["name"],
                "resolved": False,
                "kitagawa_shasen_kubun": kubun,
                "gradient": _GRADIENT,
                "rise_height_m": None,
                "vertices": [],
            }
            for site in site_zones
        ]

    rise_height_m = _RISE_HEIGHTS_M[kubun]
    north_x, north_y = _north_unit_vector(north_degrees)

    results = []

    for site in site_zones:
        site_polygon = Polygon(site["points"])
        coords = list(site_polygon.exterior.coords[:-1])

        projections = [x * north_x + y * north_y for x, y in coords]
        northmost_projection = max(projections)

        vertices = [
            {"x": x, "y": y, "z_m": _height_limit_m(x, y, northmost_projection, north_x, north_y, rise_height_m)}
            for x, y in coords
        ]

        results.append({
            "site_guid": site["guid"],
            "site_name": site["name"],
            "resolved": True,
            "kitagawa_shasen_kubun": kubun,
            "gradient": _GRADIENT,
            "rise_height_m": rise_height_m,
            "vertices": vertices,
        })

    return results


def resolve_north_degrees_for_compliance(north_degrees: float | None) -> float | None:
    if north_degrees is not None:
        return north_degrees

    raw = resolve_legal_input("north_degrees")
    if raw is None:
        return None
    try:
        return float(raw)
    except (TypeError, ValueError) as exc:
        raise ValueError(
            f"north_degreesの値が数値として解釈できません: {raw!r}(度数を数値で設定してください)"
        ) from exc


def calculate_north_slant_compliance(
    north_degrees: float | None = None, kitagawa_shasen_kubun: str | None = None
) -> list[dict]:
    """敷地(敷地境界線Zone)ごとに、北側斜線制限envelopeと実際の建物高さ
    (Wall/Slab/Roof/Column、engine/building_height.py)を比較したPASS/FAIL用
    実測値(超過高さ、m)を返す。engine/road_slant_envelope.pyの
    `calculate_road_slant_compliance()`と同じ考え方(site単位1項目)。

    north_degreesは、calculate_north_slant_envelope()と異なりArchicadから
    都度取得せず、legal_inputs.pyの"north_degrees"キー(環境変数
    NORTH_DEGREES)から解決する——rule_engine.py(RULE_CHECK_REGISTRY)経由の
    合否判定は他のengine/*.pyと同じ「SQLiteキャッシュのみを読む、Archicad
    非依存」というアーキテクチャ境界を守る必要があるため。

    適用区分が対象外・未設定、north_degreesが未設定、または敷地内に高さ
    情報を持つ建物要素が無い場合、measured_value=None(UNKNOWN)を返す
    (missing_inputsではなく、判定は実行された上で値が無い状態として扱う)。
    """
    kubun = kitagawa_shasen_kubun or get_kitagawa_shasen_kubun()
    site_zones = get_site_boundary()

    if kubun not in _RISE_HEIGHTS_M:
        return [
            {
                "target_guid": site["guid"],
                "target_name": site["name"],
                "measured_value": None,
                "evidence": {
                    "reason": "適用区分(kitagawa_shasen_kubun)が未設定、または対象区域外です。",
                },
            }
            for site in site_zones
        ]

    resolved_north_degrees = resolve_north_degrees_for_compliance(north_degrees)
    if resolved_north_degrees is None:
        return [
            {
                "target_guid": site["guid"],
                "target_name": site["name"],
                "measured_value": None,
                "evidence": {"reason": "真北方向(north_degrees)が未設定のため算出できません。"},
            }
            for site in site_zones
        ]

    rise_height_m = _RISE_HEIGHTS_M[kubun]
    north_x, north_y = _north_unit_vector(resolved_north_degrees)
    all_points = building_height_points()

    results = []
    for site in site_zones:
        site_polygon = Polygon(site["points"])
        coords = list(site_polygon.exterior.coords[:-1])
        northmost_projection = max(x * north_x + y * north_y for x, y in coords)

        points_in_site = [
            p for p in all_points if site_polygon.contains(Point(p["x"], p["y"]))
        ]

        if not points_in_site:
            results.append({
                "target_guid": site["guid"],
                "target_name": site["name"],
                "measured_value": None,
                "evidence": {
                    "reason": "敷地内に高さ情報を持つ建物要素(Wall/Slab/Roof/Column)がありません。",
                },
            })
            continue

        worst_point = max(
            points_in_site,
            key=lambda p: p["height_m"]
            - _height_limit_m(p["x"], p["y"], northmost_projection, north_x, north_y, rise_height_m),
        )
        limit_m = _height_limit_m(
            worst_point["x"], worst_point["y"], northmost_projection, north_x, north_y, rise_height_m
        )
        excess_m = worst_point["height_m"] - limit_m

        results.append({
            "target_guid": site["guid"],
            "target_name": site["name"],
            "measured_value": excess_m,
            "evidence": {
                "kitagawa_shasen_kubun": kubun,
                "worst_element_guid": worst_point["guid"],
                "worst_element_name": worst_point["name"],
                "worst_element_height_m": worst_point["height_m"],
                "height_limit_m_at_worst_element": limit_m,
            },
        })

    return results
