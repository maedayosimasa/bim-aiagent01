"""LangGraphエージェントが呼び出すツール群。

既存のREST API(main.py)やMCPツール(archicad_mcp/server.py)と同じ
Python関数をそのまま薄くラップするだけで、新しいロジックは持たない
(このプロジェクトの「別プロトコルを介さず同じ関数を直接呼ぶ」方針を踏襲)。

意図的に読み取り専用・解析系のみを公開し、Archicad本体への書き込み系ツール
(move_archicad_element/delete_archicad_elements/set_archicad_property_value等)
とsync_from_archicad(ローカルキャッシュ全削除を伴う)は含めていない。
CLAUDE.md「⑩ アクション/操作層」に記載の通り、これらの破壊的操作には
誰が/いつ/何を変更したかの監査ログがまだ無いため、AIエージェントが自律的に
呼べる状態にするのは監査ログ整備後に回す。

**(2026-08-11発見・同日修正)実データ(5708要素)でLLMのコンテキスト上限
(Claude Opus 5は100万トークン)を超過する事故が起きた。** list_bim_elements_tool
が全要素をproperties/geometry込みでダンプしており、実測で約224万トークン
(それだけで上限の2倍超)だった。get_engine_analysis_snapshot_tool/
analyze_bim_space_toolもグラフの生データ(座標等、フロントエンドのグラフ
可視化専用で、LLMの自然文回答には不要)を含み約53万トークン、
get_graph_relation_snapshot_toolも関係一覧の全件ダンプで約13万トークン
あった。以降、一覧・スナップショット系ツールは「既定は件数サマリのみ、
絞り込み条件を指定すれば上限件数まで詳細を返す」方針に統一している
(個別要素の詳細はget_bim_element_toolで1件ずつ取得する)。
"""

import json

import httpx
from langchain_core.tools import tool
from langgraph.types import interrupt

from ..archicad_mcp.server import (
    get_engine_analysis_snapshot,
    get_graph_relation_snapshot,
    list_elements,
)
from ..database import db
from ..engine.accessibility import analyze_accessibility
from ..engine.code_engine import check_accessible_door_width, check_daylighting
from ..engine.equipment import find_room_equipment
from ..engine.evacuation_engine import find_evacuation_routes
from ..engine.relation_builder import rebuild_connections
from ..engine.room_engine import analyze_room_adjacency
from ..engine.legal_inputs import list_legal_inputs
from ..engine.rule_engine import evaluate_legal_rule_by_id, load_legal_rules
from ..engine.site import get_road_boundaries, get_site_boundary
from ..engine.spatial import analyze_space
from ..engine.vector_store import search_elements
from ..engine.window_classifier import classify_windows
from ..legal_mcp import client as legal_client
from ..legal_mcp.client import LegalApiNotConfiguredError

# 一覧/スナップショット系ツールが実データ(5000件超)でLLMのコンテキスト
# 上限を超えないよう、返す件数の上限を明示的に固定する(呼び出し側のLLMが
# 大きな値を指定してきても、ここでクランプする)。
_ELEMENT_LIST_LIMIT = 200
_RELATION_LIST_LIMIT = 100
_SEARCH_RESULTS_LIMIT = 50
_ISSUE_LIMIT = 20


def _json(value) -> str:
    return json.dumps(value, ensure_ascii=False, default=str)


def _clamp(value: int, minimum: int, maximum: int) -> int:
    return max(minimum, min(value, maximum))


def _drop_graph_data_and_cap_issues(result: dict) -> dict:
    """analyze_space()/get_engine_analysis_snapshot()の結果からLLMに不要な
    生データを取り除く。graph_data(座標付きノード/エッジ)はフロントエンドの
    グラフ可視化専用でLLMには不要かつ巨大なため常に除外し、issues(警告)は
    実データで1000件を超えることがあるため件数のみ示し上位_ISSUE_LIMIT件に絞る。
    """
    trimmed = {key: value for key, value in result.items() if key != "graph_data"}
    issues = trimmed.get("issues")
    if isinstance(issues, list) and len(issues) > _ISSUE_LIMIT:
        trimmed["issues"] = issues[:_ISSUE_LIMIT]
        trimmed["issues_total"] = len(issues)
        trimmed["issues_truncated"] = True
    return trimmed


async def _legal_call(coro) -> str:
    try:
        return _json(await coro)
    except LegalApiNotConfiguredError as exc:
        return _json({"error": str(exc)})
    except httpx.HTTPError as exc:
        return _json({"error": f"Legal Knowledge BuilderのAPIに接続できませんでした: {exc}"})


@tool
def list_bim_elements_tool(element_type: str | None = None, limit: int = 50) -> str:
    """BIM要素の一覧・件数を確認する。

    データ量が大きくなりうる(実データで5000件を超えることがある)ため、
    element_typeを省略した場合は種別(Wall/Door/Room/Zone等)ごとの件数
    サマリのみを返す。element_typeを指定すると、その種別の要素をguid/
    type/nameのみで最大limit件(既定50、上限200)まで列挙する
    (properties/geometryは含まない — 個別要素の詳細はget_bim_element_tool
    でguidを指定して取得する)。
    """
    elements = list_elements()

    if element_type is None:
        counts: dict[str, int] = {}
        for element in elements:
            counts[element["type"]] = counts.get(element["type"], 0) + 1
        return _json({"total": len(elements), "by_type": counts})

    matched = [element for element in elements if element["type"] == element_type]
    capped_limit = _clamp(limit, 1, _ELEMENT_LIST_LIMIT)
    truncated = matched[:capped_limit]

    return _json({
        "matched_total": len(matched),
        "returned": len(truncated),
        "elements": [
            {"guid": element["guid"], "type": element["type"], "name": element["name"]}
            for element in truncated
        ],
    })


@tool
def get_bim_element_tool(guid: str) -> str:
    """指定guidの単一BIM要素の詳細(properties/geometryを含む全情報)を取得する。

    guidはlist_bim_elements_toolやsearch_bim_elements_toolの結果から得られる。
    """
    row = db.get_element(guid)
    if row is None:
        return _json({"error": f"要素が見つかりません: {guid}"})

    element = dict(row)
    element["properties"] = json.loads(element["properties"]) if element["properties"] else {}
    element["geometry"] = json.loads(element["geometry"]) if element["geometry"] else {}
    return _json(element)


@tool
def search_bim_elements_tool(query: str, n_results: int = 5) -> str:
    """自然文クエリでBIM要素を意味検索する(ChromaDBのベクトル検索)。

    「キッチンに近いドア」のような曖昧な問い合わせや、名前・種類が
    正確にわからない要素を探すときに使う。
    """
    capped = _clamp(n_results, 1, _SEARCH_RESULTS_LIMIT)
    return _json(search_elements(query, n_results=capped))


@tool
def analyze_bim_space_tool() -> str:
    """空間解析(隣接・接続グラフの再計算+要素数/警告のサマリ)を実行する。

    グラフの生データ(座標等、フロントエンド可視化専用)は返さない。
    警告(issues)は最大20件までに絞り、総件数を別途示す。
    """
    return _json(_drop_graph_data_and_cap_issues(analyze_space("default")))


@tool
def rebuild_relations_tool() -> str:
    """要素間の空間関係(隣接/接続)をローカルキャッシュ上で再計算する。

    Archicad本体には一切影響しない(SQLite上のconnectionsテーブルのみ更新)。
    """
    relations = rebuild_connections()
    return _json({"count": len(relations)})


@tool
def get_engine_analysis_snapshot_tool() -> str:
    """直近のanalyze_bim_space_tool実行結果のスナップショットを返す(検証用)。

    グラフの生データ(座標等)は返さない。警告(issues)は最大20件までに絞る。
    """
    snapshot = get_engine_analysis_snapshot()
    if snapshot is None:
        return _json(None)
    return _json(_drop_graph_data_and_cap_issues(snapshot))


@tool
def get_graph_relation_snapshot_tool(relation: str | None = None, limit: int = 50) -> str:
    """直近のrebuild_relations_tool実行結果(要素タイプ付きの関係一覧)を返す(検証用)。

    データ量が大きくなりうるため、relationを省略した場合は関係種別
    (adjacent/connects/near)ごとの件数サマリのみを返す。relationを指定
    すると、その種別の関係を最大limit件(既定50、上限100)まで列挙する。
    """
    rows = get_graph_relation_snapshot()

    if relation is None:
        counts: dict[str, int] = {}
        for row in rows:
            counts[row["relation"]] = counts.get(row["relation"], 0) + 1
        return _json({"total": len(rows), "by_relation": counts})

    matched = [row for row in rows if row["relation"] == relation]
    capped_limit = _clamp(limit, 1, _RELATION_LIST_LIMIT)
    truncated = matched[:capped_limit]

    return _json({
        "matched_total": len(matched),
        "returned": len(truncated),
        "relations": truncated,
    })


@tool
def engine_room_adjacency_tool() -> str:
    """部屋(Room/Zone)同士の隣接関係とドアによる接続関係を解析する(隣室解析)。"""
    return _json(analyze_room_adjacency())


@tool
def engine_evacuation_routes_tool() -> str:
    """単一階の避難経路を解析する(外部ドアまでの最短経路)。

    複数階をまたぐ避難経路(階段経由)は現状未対応。
    """
    return _json(find_evacuation_routes())


@tool
def engine_code_daylighting_tool() -> str:
    """採光有効面積比(窓面積/床面積)を部屋ごとに計算する。

    建築基準法の参考値(閾値1/7)であり、法的な適合を保証するものではない。
    """
    return _json(check_daylighting())


@tool
def engine_code_accessible_doors_tool() -> str:
    """ドア幅がバリアフリー参考値(0.8m)を満たすか部屋ごとに判定する。

    参考値であり、法的な適合を保証するものではない。
    """
    return _json(check_accessible_door_width())


@tool
def engine_legal_rules_list_tool() -> str:
    """法規チェックの定義一覧(採光・バリアフリードア幅等)を返す。

    各定義のrule_idはengine_legal_rules_evaluate_toolに渡す。
    """
    return _json([rule.model_dump() for rule in load_legal_rules()])


@tool
async def engine_legal_rules_evaluate_tool(rule_id: str) -> str:
    """指定rule_idの法規チェックをPASS/FAIL/UNKNOWN判定として実行する。

    Legal Knowledge Builder(法令検索サービス)が接続されていれば、
    関連しそうな法令根拠(legal_sources)も添付される(未接続でも判定は継続)。
    rule_idの一覧はengine_legal_rules_list_toolで取得できる。

    判定に必要な外部の法規条件(用途地域等)が不足している場合、このツールは
    グラフの実行を一時停止する(missing_inputsの内容を尋ねるためにLLMが自分で
    文章を組み立てる必要はない——呼び出し元が停止を検知し、ユーザーに
    確認した上で再開する)。値が設定され(backendの環境変数、再起動要)、
    再開されると判定を再実行し、その結果を返す。
    """
    try:
        result = await evaluate_legal_rule_by_id(rule_id)
    except ValueError as exc:
        return _json({"error": str(exc)})

    # (2026-08-13追加、Missing Input Interrupt/Resumeパターン)値は
    # legal_inputs.pyの制約によりbackendの環境変数からしか読めず、この
    # エージェントには書き換え権限が無い(=「backendを再起動する」という
    # 外部イベントを待つしかない)。会話の1ターンで完結させようとせず、
    # LangGraphのinterrupt()でグラフ自体を一時停止する(checkpointerに
    # 状態が永続化されるため、backend再起動を挟んでも再開できる)。
    while result.get("missing_inputs"):
        interrupt({
            "type": "missing_legal_inputs",
            "rule_id": rule_id,
            "missing_inputs": result["missing_inputs"],
            "message": (
                f"「{result['title']}」の判定には、外部の法規条件"
                f"({'、'.join(m['label'] for m in result['missing_inputs'])})"
                "が必要ですが未設定です。値が分かればbackendの環境変数に設定し"
                "backendを再起動した上で、再開してください。"
            ),
        })
        try:
            result = await evaluate_legal_rule_by_id(rule_id)
        except ValueError as exc:
            return _json({"error": str(exc)})

    return _json(result)


@tool
def engine_legal_inputs_tool() -> str:
    """BIMデータからは判定できない法規条件(用途地域・防火地域・道路条件等)
    の一覧と、現在値が設定されているかを返す。

    engine_legal_rules_evaluate_toolの結果にmissing_inputsが含まれる場合や、
    ユーザーから「どの法規条件が必要か」を聞かれた場合に使う。値は現状
    backendの環境変数(キーを大文字化したもの、例: land_use_category→
    LAND_USE_CATEGORY)からのみ読み込まれる——Archicadのカスタムプロパティ
    からの自動取り込みは未対応。ユーザーに値を尋ねた後は、
    「backendの環境変数<KEY>に設定し再起動してください」と案内すること
    (このエージェントはArchicad・backendの設定を書き換える権限を持たない)。
    """
    used_by: dict[str, list[str]] = {}
    for rule in load_legal_rules():
        for key in rule.required_inputs:
            used_by.setdefault(key, []).append(rule.rule_id)

    return _json(list_legal_inputs(used_by))


@tool
def engine_accessibility_tool() -> str:
    """部屋の動線トポロジーを解析し、行き止まりの部屋・ハブ部屋(廊下等)を報告する。"""
    return _json(analyze_accessibility())


@tool
def engine_equipment_tool() -> str:
    """家具・空調・水回り設備がどの部屋に収容されているかを解析する。"""
    return _json(find_room_equipment())


@tool
def engine_windows_tool() -> str:
    """窓(Window)が外部窓か内部窓かを、隣接する部屋数から推定する(参考値)。

    Archicadの窓要素自体には外部/内部を示す属性が無いため、1部屋にのみ
    隣接する窓を外部窓、2部屋以上に隣接する窓を内部窓とみなすヒューリス
    ティックで分類する(ドアの外部判定と同じ考え方)。「外部窓は何件か」
    「この部屋の窓は外部窓か」といった質問には、個別要素を1件ずつ
    get_bim_element_toolで調べるのではなく、まずこのツールを使うこと。
    """
    return _json(classify_windows())


@tool
def site_boundary_tool() -> str:
    """「敷地」という名前が付いたZoneを敷地境界線として検索する。見つからなければ空。"""
    return _json({"zones": get_site_boundary()})


@tool
def site_roads_tool() -> str:
    """「道路」という名前が付いたZoneを前面道路として検索する(幅員はポリゴンからの近似値)。"""
    return _json({"zones": get_road_boundaries()})


@tool
async def legal_search_tool(query: str, top_k: int = 5, law_id: str | None = None) -> str:
    """建築関連法(建築基準法・建築士法・都市計画法等)の条文を自然文検索する。

    Legal Knowledge Builder(別サービス)が起動・接続されていない場合はエラーを返す。
    """
    return await _legal_call(legal_client.search(query, top_k=top_k, law_id=law_id))


@tool
async def legal_article_tool(law_id: str, num: str) -> str:
    """指定した法令(law_id)・条番号(num)の条文本文を取得する。"""
    return await _legal_call(legal_client.get_article(law_id, num))


@tool
async def legal_rules_by_concept_tool(concept_id: str) -> str:
    """法概念(concept_id)に紐づく法令Rule(条文根拠の候補)を法令横断で取得する。"""
    return await _legal_call(legal_client.get_rules_by_concept(concept_id))


@tool
async def legal_graph_neighbors_tool(node_id: str, depth: int = 1) -> str:
    """法令知識グラフ上で起点ノード(node_id)から近傍ノードを多段階(depth)に辿る(GraphRAG)。"""
    return await _legal_call(legal_client.get_graph_neighbors(node_id, depth=depth))


AGENT_TOOLS = [
    list_bim_elements_tool,
    get_bim_element_tool,
    search_bim_elements_tool,
    analyze_bim_space_tool,
    rebuild_relations_tool,
    get_engine_analysis_snapshot_tool,
    get_graph_relation_snapshot_tool,
    engine_room_adjacency_tool,
    engine_evacuation_routes_tool,
    engine_code_daylighting_tool,
    engine_code_accessible_doors_tool,
    engine_legal_rules_list_tool,
    engine_legal_rules_evaluate_tool,
    engine_legal_inputs_tool,
    engine_accessibility_tool,
    engine_equipment_tool,
    engine_windows_tool,
    site_boundary_tool,
    site_roads_tool,
    legal_search_tool,
    legal_article_tool,
    legal_rules_by_concept_tool,
    legal_graph_neighbors_tool,
]
