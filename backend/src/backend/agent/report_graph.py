"""法規チェック→引用条文添付→レポート生成、の複数ステップグラフ。

agent/graph.py(build_agent)のReActエージェント——会話ループの中でLLMが
毎回どのツールを呼ぶか判断する——とは別物。こちらは手順が固定された
決定的なパイプラインで、LangGraphのStateGraphを直接組む(ノード=各ステップ、
エッジ=固定の実行順)。CLAUDE.md「⑨」で「将来複数ステップの解析フロー
(法規チェック→引用条文添付→レポート生成、等)をグラフとして組みたくなった
時点で拡張する」としていた拡張ポイントを実装したもの。

ステップ:
  1. run_checks: 登録されている全法規Rule(engine/legal_rules.json、
     engine/rule_engine.py)をPASS/FAIL/UNKNOWN判定する。引用条文の添付
     (legal_sources)はevaluate_legal_rule()自体が行う(Legal Knowledge
     Builder接続時のみ、未接続でも空リストになるだけで判定は継続する)。
     この段階はLLMを一切呼ばない、決定的な計算のみ。
  2. generate_report: 判定結果一覧を要約したテキストをLLM(Claude)に渡し、
     日本語のレポート文を生成する。LLMには数値の再計算・判定の上書きを
     させず、要約・文章化のみを担当させる(判定結果自体は既にステップ1で
     確定している)。
"""

from typing import TypedDict

from langchain_anthropic import ChatAnthropic
from langchain_core.messages import HumanMessage, SystemMessage
from langgraph.graph import END, START, StateGraph

from ..engine.rule_engine import evaluate_legal_rule, load_legal_rules
from .message_utils import message_text

REPORT_SYSTEM_PROMPT = """\
あなたは建築BIMの法規チェック結果を要約するレポート作成者です。
与えられたPASS/FAIL/UNKNOWNの判定結果一覧をもとに、日本語の簡潔なレポートを
作成してください。

厳守事項:
- 判定結果(PASS/FAIL/UNKNOWNの件数・対象・実測値)は与えられたデータを
  そのまま使うこと。自分で数値を計算し直したり、判定を覆したりしてはいけない。
- 各チェックはあくまで参考値であり、法的な適合を保証するものではないことを
  必ず明記すること(与えられたdisclaimerの内容を反映する)。
- 「関連しそうな法令根拠」は正規表現ベースの候補でありノイズを含むため、
  確定的な法的根拠として断定しないこと。
- チェック項目ごとに見出しを分け、FAILがある場合は該当箇所を具体的に列挙し、
  UNKNOWNがある場合はその理由(実測値が取得できない等)に触れること。
- 「未判定」と記されたチェックは、判定に必要な外部の法規条件(用途地域等)が
  未設定のため実行されなかったことを意味する。PASS/FAILとして扱わず、
  何が不足しているかを明記し、値を設定すれば判定できるようになる旨を書くこと。
- 最後に全体の総評を短くまとめること。
"""


class LegalReportState(TypedDict):
    checks: list[dict]
    report: str
    usage: dict


async def _run_checks(state: LegalReportState) -> dict:
    rules = load_legal_rules()
    checks = [await evaluate_legal_rule(rule) for rule in rules]
    return {"checks": checks}


def _summarize_for_prompt(checks: list[dict]) -> str:
    # レポート生成用LLMへのプロンプトが際限なく膨らまないよう、FAIL項目・
    # 法令根拠はそれぞれ上限件数までに絞って渡す(件数自体はレポートに含める)。
    lines: list[str] = []

    for check in checks:
        counts = {"pass": 0, "fail": 0, "unknown": 0, "not_applicable": 0}
        for item in check["items"]:
            counts[item["status"]] = counts.get(item["status"], 0) + 1

        lines.append(f"■{check['title']}(rule_id={check['rule_id']})")

        if check.get("missing_inputs"):
            missing_labels = "、".join(m["label"] for m in check["missing_inputs"])
            lines.append(
                f"  未判定: このチェックには外部の法規条件({missing_labels})が"
                "必要ですが未設定のため、今回は判定を実行していません。"
            )
            lines.append(f"  免責事項: {check['disclaimer']}")
            lines.append("")
            continue

        lines.append(
            f"  判定基準: 実測値 {check['comparator']} {check['threshold']}"
            f"{check['threshold_unit'] or ''}"
        )
        lines.append(
            f"  結果: PASS {counts['pass']}件 / FAIL {counts['fail']}件 / "
            f"UNKNOWN {counts['unknown']}件"
        )
        lines.append(f"  免責事項: {check['disclaimer']}")

        fails = [item for item in check["items"] if item["status"] == "fail"]
        for item in fails[:20]:
            target = item.get("target_name") or item["target_guid"]
            lines.append(
                f"    - FAIL: {target}"
                f"(実測値: {item['measured_value']}{item.get('unit') or ''})"
            )
        if len(fails) > 20:
            lines.append(f"    - 他{len(fails) - 20}件のFAILあり")

        if check["legal_sources"]:
            lines.append(
                f"  関連しそうな法令根拠 {len(check['legal_sources'])}件"
                "(参考、確定的な根拠ではない):"
            )
            for source in check["legal_sources"][:5]:
                sentence = (source.get("raw_sentence") or "")[:80]
                lines.append(f"    - {source.get('law_id')}: {sentence}")

        lines.append("")

    return "\n".join(lines)


def _build_generate_report_node(model_name: str):
    async def generate_report(state: LegalReportState) -> dict:
        model = ChatAnthropic(model=model_name)
        summary = _summarize_for_prompt(state["checks"])

        response = await model.ainvoke([
            SystemMessage(content=REPORT_SYSTEM_PROMPT),
            HumanMessage(content=f"以下の判定結果からレポートを作成してください:\n\n{summary}"),
        ])

        usage_metadata = getattr(response, "usage_metadata", None) or {}

        return {
            "report": message_text(response),
            "usage": {
                "input_tokens": usage_metadata.get("input_tokens") or 0,
                "output_tokens": usage_metadata.get("output_tokens") or 0,
            },
        }

    return generate_report


def build_report_graph(model_name: str = "claude-opus-5"):
    graph = StateGraph(LegalReportState)
    graph.add_node("run_checks", _run_checks)
    graph.add_node("generate_report", _build_generate_report_node(model_name))
    graph.add_edge(START, "run_checks")
    graph.add_edge("run_checks", "generate_report")
    graph.add_edge("generate_report", END)

    return graph.compile()
