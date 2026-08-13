"""LangGraphエージェント本体の組み立て(create_react_agentの薄いラッパー)。

CLAUDE.md「⑨ RAG / AIエージェント層」で「実質未着手」とされていたエージェント
ループ(発話→ツール選択→実行→観察→次の発話)をここで初めて実装する。
LangGraphのprebuilt ReActエージェント(langgraph.prebuilt.create_react_agent)を
そのまま使い、独自のグラフ制御は持たない(まずは素直な構成で動かし、将来
複数ステップの解析フロー(法規チェック→引用条文添付→レポート生成、等)を
グラフとして組みたくなった時点で拡張する)。

**(2026-08-13追加)Context 3層分離(ユーザー提示のLayer1/2/3案の実質的解決)**:
ユーザー提示の「Layer1(会話)は圧縮対象、Layer2(Agent Working State)は
必要部分のみ保持、Layer3(Engine Evidence)は圧縮しない」という3層分離案を
検討した結果、messages(会話+ツール呼び出し履歴)とは別のevidence用state
フィールドを新設する自前実装ではなく、LangChain 1.x標準の
`ContextEditingMiddleware`(Anthropicの`clear_tool_uses_20250919`相当)を採用
した——これは`wrap_model_call`フックでモデルへのリクエスト直前にメッセージの
deepcopyを編集するだけで、checkpointerに永続化された会話履歴そのものは
変更しない(`get_history()`・frontendの表示には常に完全な履歴が残る)。
つまり「LLMへ次に送る分だけを間引く」という自前3層分離が本来やりたかった
ことと同じ効果を、実績のあるライブラリ機能で低リスクに実現できる。
`_EVIDENCE_TOOLS_EXCLUDED_FROM_CLEARING`(Rule Engineの確定的な判定結果=
Layer3相当)は経過ターン数に関わらず常にLLMのコンテキストに残し、それ以外の
探索的なBIM検索・法令検索結果(Layer2相当)は`_CLEAR_TOOL_USES_KEEP`件を
超えて古くなると`[cleared]`に置き換えられる。
"""

from langchain.agents import create_agent
from langchain.agents.middleware import ClearToolUsesEdit, ContextEditingMiddleware
from langchain_anthropic import ChatAnthropic
from langgraph.checkpoint.base import BaseCheckpointSaver

from .tools import AGENT_TOOLS

# 会話の累積トークン数(近似値)がこれを超えたら古いツール結果の間引きを
# 検討する。2026-08-11の事故(list_bim_elements_tool等の全件ダンプで実測
# 約224万トークン)はツール出力自体を絞ることで既に解消済みのため、ここでは
# 「長時間・多ターンの会話でじわじわ蓄積する」ケースを想定し、Claude Opus 5
# のコンテキスト上限(100万トークン)に対して十分な余裕を残す値にしている。
_CLEAR_TOOL_USES_TRIGGER_TOKENS = 300_000
# 直近何件のツール結果を(除外ツール以外でも)無条件に残すか。
_CLEAR_TOOL_USES_KEEP = 5
# Rule Engineの確定的な判定結果(PASS/FAIL/UNKNOWN+法令根拠)はLayer3
# (Engine Evidence)に相当し、古くなっても間引かれるとユーザーが会話の
# 途中でその判定結果を参照できなくなる。探索的なBIM検索・法令検索
# (list_bim_elements_tool等)は間引き対象のまま残す。
_EVIDENCE_TOOLS_EXCLUDED_FROM_CLEARING = (
    "engine_legal_rules_evaluate_tool",
    "engine_code_daylighting_tool",
    "engine_code_accessible_doors_tool",
)


def _build_middleware() -> list:
    return [
        ContextEditingMiddleware(
            edits=[
                ClearToolUsesEdit(
                    trigger=_CLEAR_TOOL_USES_TRIGGER_TOKENS,
                    keep=_CLEAR_TOOL_USES_KEEP,
                    exclude_tools=_EVIDENCE_TOOLS_EXCLUDED_FROM_CLEARING,
                ),
            ],
        ),
    ]

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
- 法規適合性(「〜に適合していますか」「PASSしていますか」等)を問われた
  場合、一部のツール結果だけで判断して答えてはいけません。まず
  engine_legal_rules_list_toolで登録済み全rule_idを確認し、質問に関連
  しそうなrule_id全てをengine_legal_rules_evaluate_toolで評価してから
  回答してください(例: 採光について聞かれた場合、daylighting_ratioだけ
  でなくeffective_daylighting_ratio(建築基準法施行令20条に基づくより
  正確な判定)も合わせて確認する)。BIMデータの検索・空間解析だけで
  法的な適合を判断したり、法令検索の結果だけでBIMの実測値を確認せずに
  判断したりしないでください——建築的な事実(BIMデータ・空間解析)と
  法的根拠・判定(法令検索・Rule Engine)の両方が揃って初めて適合性を
  判断できます。
- 「建築基準法第28条」のような具体的な条文番号は、必ずlegal_search_tool/
  legal_article_tool/legal_rules_by_concept_tool等のツール結果に実際に
  含まれるものだけを使ってください。ツール結果に無い条番号を一般知識で
  補って挙げてはいけません(条文の存在や趣旨に一般的に触れることは
  構いませんが、具体的な条番号の断定はツール結果に基づくこと)。
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
        middleware=_build_middleware(),
    )
