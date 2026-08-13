"""BIMデータからは判定できない、プロジェクト単位の法規条件(外部の都市計画・
建築規制情報)を定義・解決する。

`effective_daylighting_ratio`(engine/effective_daylighting.py)の用途地域の
ように、法規チェックの中にはBIMモデルの幾何・属性だけでは判定できず、
都市計画図・自治体条例等の外部情報を要する項目がある。これらを「必要な
入力(required_inputs)」としてLegalRuleに明示的に宣言させ(engine/
legal_rules.json)、値が無ければ判定不能(UNKNOWN)として黙って処理する
のではなく、「何が・なぜ足りないか」を具体的に返すことで、AIエージェント
(⑨)がユーザーに聞き返せるようにする(rule_engine.evaluate_legal_rule()
参照)。

LEGAL_INPUT_DEFINITIONSは、ユーザーが想定している法規条件の分類(都市計画/
建築規制/防火規制/道路条件/日影・斜線)をそのまま反映した一覧。実際に
どこかのLegalRuleのrequired_inputsから参照されている項目と、まだどの
チェックからも使われていない項目(将来のチェック実装に備えたもの)が
混在する——後者は「値をここに用意しておけば将来のチェックがすぐ使える」
という意味でのテンプレート/スキーマとして機能する(GET /engine/legal_inputs
で一覧を確認できる)。

**値の取得元(現状は環境変数のみ)**: 各入力キーは大文字化した環境変数名
(例: land_use_category → LAND_USE_CATEGORY)から読む。ユーザーはArchicad
のカスタムProperty(「法規条件」のようなプロパティグループとしてテンプレート
化したいと考えている)から直接読み込みたいと考えているが、現状の
sync_from_archicad()は組み込みのGetDetailsOfElements情報のみを同期し、
カスタムProperty値(GetPropertyValuesOfElements)は含まないため、この経路は
未実装(既知の今後の課題としてここに明記する。実装する場合、プロジェクト
単位の値をどの要素に持たせるか——敷地境界線Zoneのプロパティとして持たせる
案が有力——という設計判断が別途必要になる)。
"""

import os
from dataclasses import dataclass


@dataclass(frozen=True)
class LegalInputDefinition:
    key: str
    label: str
    description: str
    category: str


LEGAL_INPUT_DEFINITIONS: dict[str, LegalInputDefinition] = {
    "land_use_category": LegalInputDefinition(
        key="land_use_category",
        label="用途地域",
        description=(
            "residential(住居系)/industrial(工業系)/commercial(商業系・"
            "無指定)のいずれか。effective_daylighting_ratio(採光補正係数の"
            "算式選択)が使用する。"
        ),
        category="都市計画",
    ),
    "toshi_keikaku_kuiki": LegalInputDefinition(
        key="toshi_keikaku_kuiki",
        label="都市計画区域",
        description="市街化区域/市街化調整区域/非線引き区域/都市計画区域外のいずれか。",
        category="都市計画",
    ),
    "kodo_chiku_kubun": LegalInputDefinition(
        key="kodo_chiku_kubun",
        label="高度地区の指定区分",
        description=(
            "none(指定なし)/flat(絶対高さ制限のみ)/north_slant(真北方向の"
            "斜線制限型、多くの自治体条例が採用する形式)のいずれか。高度地区は"
            "都市計画法8条1項3号の地域地区で、自治体(都道府県・市町村)の"
            "条例により最高限度・最低限度の内容が個別に定められ、建築基準法"
            "56条の道路斜線・隣地斜線・北側斜線のような全国一律の計算式が"
            "無いため、この区分と下記kodo_chiku_*の各値を明示的に指定する"
            "必要がある(推測しない)。engine/height_district_envelope.pyが使用する。"
        ),
        category="都市計画",
    ),
    "kodo_chiku_max_height_m": LegalInputDefinition(
        key="kodo_chiku_max_height_m",
        label="高度地区の最高限度(絶対高さ、m)",
        description=(
            "kodo_chiku_kubun=flatの場合の絶対高さ上限、またはnorth_slantの"
            "場合の頭打ち上限(自治体条例が併用する場合)。未指定なら"
            "north_slantでは頭打ちなし。"
        ),
        category="都市計画",
    ),
    "kodo_chiku_rise_m": LegalInputDefinition(
        key="kodo_chiku_rise_m",
        label="高度地区の立ち上がり高さ(m)",
        description="kodo_chiku_kubun=north_slantの場合の立ち上がり高さ(自治体条例による)。",
        category="都市計画",
    ),
    "kodo_chiku_gradient": LegalInputDefinition(
        key="kodo_chiku_gradient",
        label="高度地区の勾配",
        description="kodo_chiku_kubun=north_slantの場合の勾配(自治体条例による、北側斜線制限の1.25とは別に個別に定められる)。",
        category="都市計画",
    ),
    "kodo_chiku_kanwa_m": LegalInputDefinition(
        key="kodo_chiku_kanwa_m",
        label="高度地区規制緩和による加算値(m)",
        description=(
            "天空率・敷地内空地の確保・角地等、自治体条例が定める緩和条件を"
            "満たす場合に高さ制限へ加算できる値(m)。緩和条件を満たすかどうか"
            "自体はBIMデータからは判定できないため、満たしていることを確認済み"
            "の加算値をそのまま指定する(条件判定は行わない)。未指定なら0m"
            "(緩和なし)として扱う。"
        ),
        category="都市計画",
    ),
    "chiku_keikaku": LegalInputDefinition(
        key="chiku_keikaku",
        label="地区計画",
        description="指定の有無と、指定されている場合の制限内容。",
        category="都市計画",
    ),
    "kenpei_ritsu": LegalInputDefinition(
        key="kenpei_ritsu",
        label="建蔽率",
        description="都市計画で指定された建蔽率の上限(%)。角地緩和等の加算前の指定値。",
        category="建築規制",
    ),
    "yoseki_ritsu": LegalInputDefinition(
        key="yoseki_ritsu",
        label="容積率",
        description=(
            "都市計画で指定された容積率の上限(%)。前面道路幅員による"
            "制限(道路幅員×法定乗数)は別途計算が必要。"
        ),
        category="建築規制",
    ),
    "bouka_chiiki": LegalInputDefinition(
        key="bouka_chiiki",
        label="防火地域",
        description="防火地域/準防火地域/指定なしのいずれか。",
        category="防火規制",
    ),
    "bouka_shitei": LegalInputDefinition(
        key="bouka_shitei",
        label="防火指定(その他)",
        description="準防火地域外の建築物に対する市町村独自の防火指定等、あれば内容。",
        category="防火規制",
    ),
    "zenmen_douro_shubetsu": LegalInputDefinition(
        key="zenmen_douro_shubetsu",
        label="前面道路種別",
        description="建築基準法42条何項の道路か(1項1号〜5号、2項等)。",
        category="道路条件",
    ),
    "douro_haba": LegalInputDefinition(
        key="douro_haba",
        label="道路幅員(確認済み値)",
        description=(
            "前面道路の幅員(m)。engine/site.pyのget_road_boundaries()が"
            "Zoneポリゴンから幾何的に推定するが、実測値・道路台帳記載値では"
            "ない近似値のため、容積率計算等の正式な判定にはここで行政資料に"
            "基づく確認値を別途指定できるようにする。"
        ),
        category="道路条件",
    ),
    "setsudo_nagasa": LegalInputDefinition(
        key="setsudo_nagasa",
        label="接道長さ",
        description="敷地が道路に接する長さ(m)。建築基準法43条の接道義務(原則2m以上)の判定に使う。",
        category="道路条件",
    ),
    "nissei_taisho_kuiki": LegalInputDefinition(
        key="nissei_taisho_kuiki",
        label="日影規制の対象区域指定",
        description=(
            "特定行政庁(自治体)の条例による日影規制の対象区域指定の有無・"
            "区分(建築基準法別表第四の号)。国の法令だけでは判定できず、"
            "自治体ごとの条例確認が必要。"
        ),
        category="日影・斜線",
    ),
    "nissei_jikan": LegalInputDefinition(
        key="nissei_jikan",
        label="日影規制時間",
        description=(
            "対象区域で指定されている規制時間(例: 敷地境界線から5-10m測定線"
            "3時間・10m超2時間)。自治体条例による。"
        ),
        category="日影・斜線",
    ),
    "kitagawa_shasen_kubun": LegalInputDefinition(
        key="kitagawa_shasen_kubun",
        label="北側斜線制限の適用区分",
        description=(
            "low_rise(第一種/第二種低層住居専用地域・田園住居地域、立ち上がり"
            "5m)/mid_rise(第一種/第二種中高層住居専用地域、立ち上がり10m)/"
            "not_applicable(それ以外の用途地域、北側斜線制限の対象外)の"
            "いずれか。engine/north_slant_envelope.pyが使用する。用途地域の"
            "細分類(低層/中高層の区別)はland_use_category(residential/"
            "industrial/commercial)だけでは判定できないため、この専用キーで"
            "明示的に指定する(推測しない)。"
        ),
        category="日影・斜線",
    ),
}


def resolve_legal_input(key: str) -> str | None:
    """指定キーの法規条件の値を解決する(現状は環境変数のみ)。

    環境変数名はキーを大文字化したもの(例: land_use_category →
    LAND_USE_CATEGORY)。未設定ならNoneを返す——これは「判定不能」ではなく
    「入力が足りない」ことを示す。rule_engine.evaluate_legal_rule()がこれを
    検知し、AIエージェントがユーザーに聞き返せる形(missing_inputs)に
    変換する。
    """
    return os.environ.get(key.upper())


def get_legal_input_definition(key: str) -> LegalInputDefinition | None:
    return LEGAL_INPUT_DEFINITIONS.get(key)


def list_legal_inputs(used_by: dict[str, list[str]] | None = None) -> list[dict]:
    """全法規条件の定義+現在の解決状況(値・参照元rule_id)を返す。

    GET /engine/legal_inputs、engine_legal_inputs_toolのバッキング関数。
    used_byは{key: [rule_id, ...]}(rule_engine.load_legal_rules()から
    集計する。ここではrule_engineに依存しないよう呼び出し側から渡させる)。
    """
    used_by = used_by or {}

    return [
        {
            "key": definition.key,
            "label": definition.label,
            "description": definition.description,
            "category": definition.category,
            "value": resolve_legal_input(definition.key),
            "used_by_rule_ids": used_by.get(definition.key, []),
        }
        for definition in LEGAL_INPUT_DEFINITIONS.values()
    ]
