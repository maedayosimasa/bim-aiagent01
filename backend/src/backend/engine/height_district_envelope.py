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
"""

from shapely.geometry import Polygon

from .legal_inputs import resolve_legal_input
from .north_slant_envelope import _north_unit_vector
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
