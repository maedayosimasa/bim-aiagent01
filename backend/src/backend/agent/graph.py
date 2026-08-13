"""LangGraphエージェント本体の組み立て(create_react_agentの薄いラッパー)。

CLAUDE.md「⑨ RAG / AIエージェント層」で「実質未着手」とされていたエージェント
ループ(発話→ツール選択→実行→観察→次の発話)をここで初めて実装する。
LangGraphのprebuilt ReActエージェント(langgraph.prebuilt.create_react_agent)を
そのまま使い、独自のグラフ制御は持たない(まずは素直な構成で動かし、将来
複数ステップの解析フロー(法規チェック→引用条文添付→レポート生成、等)を
グラフとして組みたくなった時点で拡張する)。
"""

from langchain.agents import create_agent
from langchain_anthropic import ChatAnthropic
from langgraph.checkpoint.base import BaseCheckpointSaver

from .tools import AGENT_TOOLS

SYSTEM_PROMPT = """\
あなたはBIM空間知能エンジン(bim_aiagent)のAIアシスタントです。Archicadから
同期されたBIMデータ(壁・部屋・ドア・窓等)を、用意されたツール経由でのみ
参照できます。ツールを介さない推測でBIMデータの内容を答えてはいけません。

回答は日本語で行ってください。

制約と注意点:
- ツール群は読み取り専用・解析系のみです。Archicad本体を書き換えるツールは
  意図的に用意していません(監査ログが未整備のため)。要素の移動・削除・
  プロパティ変更を求められた場合は、現状のUIから手動で行うよう案内してください。
- 採光・バリアフリー等の法規チェック系ツールが返す判定は、あくまで参考値
  であり法的な適合を保証するものではありません。回答にその旨を明記してください。
- 法令検索ツール(legal_*)はLegal Knowledge Builderという別サービスが未接続
  だとエラーを返します。その場合は接続できない旨をそのまま伝えてください。
- 具体的な要素GUIDや数値は、必ずツールの実行結果に基づいて答えてください。
- BIMデータは実物件で5000件を超えることがあります。list_bim_elements_tool/
  get_graph_relation_snapshot_toolは絞り込み条件(element_type/relation)を
  省略すると件数サマリのみを返します。個別要素の詳細が必要な場合は、まず
  種別で絞り込むかsearch_bim_elements_toolでguidを特定してから、
  get_bim_element_toolで1件ずつ詳細を取得してください。全件を一度に
  詳細取得しようとしないでください。
- 「外部窓は何件か」「この窓は外部に面しているか」といった質問には、
  個別の窓をget_bim_element_toolで1件ずつ調べて推測するのではなく、まず
  engine_windows_toolを使ってください(隣接する部屋数から外部窓/内部窓を
  判定済みの結果を返します)。Archicadの窓要素自体には外部/内部を示す
  属性が無いため、この判定はあくまで参考値である旨を回答に明記してください。
- engine_legal_rules_evaluate_toolは、判定に必要な外部の法規条件(用途地域等)
  が不足している場合、会話をその場で一時停止します(呼び出し元がユーザーに
  確認し、値の設定・backend再起動後に再開します)。あなたがこの状況で
  文章を組み立てる必要はありません。ユーザーから「どの法規条件が必要か」
  と直接聞かれた場合はengine_legal_inputs_toolで一覧・現在の設定状況を
  確認して答えてください。
"""


def build_agent(
    checkpointer: BaseCheckpointSaver | None = None,
    model_name: str = "claude-opus-5",
    tools: list = AGENT_TOOLS,
):
    # toolsは既定で全ツール(AGENT_TOOLS)だが、agent/router.pyのRouterが
    # ユーザー発話から絞り込んだ部分集合を渡すこともある(agent/service.pyの
    # _get_routed_agent()参照)。system_promptは絞り込みの有無に関わらず
    # 同じものを使う(存在しないツールへの言及があっても実害は無く、
    # ドメインごとに文面を分ける複雑さに見合う効果は無いと判断した)。
    model = ChatAnthropic(model=model_name)

    return create_agent(
        model,
        tools=tools,
        system_prompt=SYSTEM_PROMPT,
        checkpointer=checkpointer,
    )
