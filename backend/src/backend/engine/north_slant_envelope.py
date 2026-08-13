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
"""

import math

from shapely.geometry import Polygon

from .legal_inputs import resolve_legal_input
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
    """legal_inputs.pyのkitagawa_shasen_kubunを解決する(未設定ならNone)。"""
    return resolve_legal_input("kitagawa_shasen_kubun")


def _north_unit_vector(north_degrees: float) -> tuple[float, float]:
    """north_degreesから真北方向の単位ベクトル(プロジェクトのXY座標系)を返す。

    Archicadの北方向角度は、プロジェクトのY+軸を基準に時計回りに測った
    真北の方位角として扱う(未検証の前提——NORTH_SLANT_ENVELOPE_DISCLAIMER
    参照)。
    """
    radians = math.radians(north_degrees)
    return (math.sin(radians), math.cos(radians))


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

        vertices = []
        for (x, y), projection in zip(coords, projections):
            distance_m = (northmost_projection - projection) / _MM_PER_M
            height_m = rise_height_m + _GRADIENT * distance_m
            vertices.append({"x": x, "y": y, "z_m": height_m})

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
