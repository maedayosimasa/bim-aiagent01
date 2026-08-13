"""ReActエージェント(graph.py)に渡すツール集合を、ユーザー発話のキーワードから
絞り込むRouter。

ユーザー提示のエージェント設計パターン(Router→並列{Spatial/Legal/BIM}→
Evidence Layer→Rule Engine→...)の検討結果を踏まえて導入した。ただし
現状のツール数(23件)・ドメイン数(BIM/法令の2つ)の規模では、ユーザーが
選んだ判定方式(キーワード/正規表現ヒューリスティック、追加のLLM呼び出し
無し)を踏まえ、ドメインごとに別々のStateGraphノード/サブグラフへ分岐させる
構成は過剰と判断した。代わりに「メッセージ本文のキーワードからツール集合を
絞り込み、絞り込んだ集合を束ねたReActエージェント(agent/graph.pyの
create_agent)を1つ呼ぶ」という薄い実装にしている——ツール選択の精度・
レイテンシ改善という実質的な効果は同じで、複数サブグラフ間の状態受け渡し
という複雑さを持ち込まずに済む。

**安全側の設計**: どちらのドメインにもキーワードが一致しない場合や、
判定に迷う場合は全ツール(AGENT_TOOLS)にフォールバックする(ツールを
誤って除外するくらいなら、多めに残す方が安全)。両ドメインにヒットした
場合も和集合を返す(排他的な単一ルーティングではない)。

**resume_chat()には適用しない**: LangGraphのinterrupt()で一時停止した
会話を再開する経路(service.pyのresume_chat、agent/tools.pyの
engine_legal_rules_evaluate_tool参照)は、常に全ツールセットのエージェント
(service.py の _get_agent())を使う。一時停止した特定のツール呼び出しが
再開先のToolNodeに確実に存在することを保証するため(ツール名で解決される
ため理論上はサブセットでも問題ないはずだが、確実性を優先した)。
"""

from .tools import (
    AGENT_TOOLS,
    analyze_bim_space_tool,
    engine_accessibility_tool,
    engine_code_accessible_doors_tool,
    engine_code_daylighting_tool,
    engine_equipment_tool,
    engine_evacuation_routes_tool,
    engine_legal_inputs_tool,
    engine_legal_rules_evaluate_tool,
    engine_legal_rules_list_tool,
    engine_room_adjacency_tool,
    engine_windows_tool,
    get_bim_element_tool,
    get_engine_analysis_snapshot_tool,
    get_graph_relation_snapshot_tool,
    legal_article_tool,
    legal_graph_neighbors_tool,
    legal_rules_by_concept_tool,
    legal_search_tool,
    list_bim_elements_tool,
    rebuild_relations_tool,
    search_bim_elements_tool,
    site_boundary_tool,
    site_roads_tool,
)

# BIM要素の検索・空間解析系ツール(法規判定を含まない)。
BIM_TOOLS = [
    list_bim_elements_tool,
    get_bim_element_tool,
    search_bim_elements_tool,
    analyze_bim_space_tool,
    rebuild_relations_tool,
    get_engine_analysis_snapshot_tool,
    get_graph_relation_snapshot_tool,
    engine_room_adjacency_tool,
    engine_evacuation_routes_tool,
    engine_accessibility_tool,
    engine_equipment_tool,
    engine_windows_tool,
    site_boundary_tool,
    site_roads_tool,
]

# 法令検索・法規チェック(Rule Engine)系ツール。
LEGAL_TOOLS = [
    engine_code_daylighting_tool,
    engine_code_accessible_doors_tool,
    engine_legal_rules_list_tool,
    engine_legal_rules_evaluate_tool,
    engine_legal_inputs_tool,
    legal_search_tool,
    legal_article_tool,
    legal_rules_by_concept_tool,
    legal_graph_neighbors_tool,
]

assert {t.name for t in BIM_TOOLS} | {t.name for t in LEGAL_TOOLS} == {t.name for t in AGENT_TOOLS}

# 過検出(無関係なドメインを含めてしまう)より見逃し(必要なドメインを
# 落としてしまう)の方が実害が大きいため、キーワードは広めに取っている。
_BIM_KEYWORDS = [
    "要素", "BIM", "壁", "部屋", "ルーム", "ドア", "窓", "避難", "経路", "動線",
    "隣接", "接続", "設備", "家具", "敷地", "道路", "階", "グラフ", "検索",
    "解析", "隣室", "ハブ", "行き止まり", "外部窓", "内部窓", "再計算", "配置",
    "Archicad", "アーキキャド", "ゾーン", "Zone", "Room",
]

_LEGAL_KEYWORDS = [
    "法令", "条文", "法規", "建築基準法", "法律", "採光", "バリアフリー",
    "用途地域", "防火", "容積率", "建蔽率", "高度地区", "地区計画", "接道",
    "日影", "判定", "PASS", "FAIL", "参考値", "根拠", "条例", "施行令",
    "ルール", "チェック", "適合", "違反", "concept",
]


def _matches(message: str, keywords: list[str]) -> bool:
    return any(keyword in message for keyword in keywords)


def route_tools(message: str) -> list:
    """メッセージ本文から関連しそうなツール集合を絞り込む。

    どちらのキーワードにも一致しない場合は全ツール(AGENT_TOOLS)を返す
    (安全側のフォールバック)。
    """
    matched: list = []
    if _matches(message, _BIM_KEYWORDS):
        matched.extend(BIM_TOOLS)
    if _matches(message, _LEGAL_KEYWORDS):
        matched.extend(LEGAL_TOOLS)

    if not matched:
        return AGENT_TOOLS

    seen: set[str] = set()
    result = []
    for t in matched:
        if t.name not in seen:
            seen.add(t.name)
            result.append(t)
    return result
