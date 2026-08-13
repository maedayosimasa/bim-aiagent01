"""法規チェック→引用条文添付→レポート生成→検証、の複数ステップグラフ。

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
  3. verify_report(2026-08-13追加、Claim Validatorパターン): generate_report
     が書いた文章中のPASS/FAIL/UNKNOWN件数表記を、ステップ1で確定した実際の
     件数と突き合わせる(Deterministic Validation、`_validate_report_claims`)。
     判定結果の再計算・上書きをLLMにさせないようREPORT_SYSTEM_PROMPTで指示
     していても、文章化の過程で件数を書き間違える(ハルシネーション)リスク
     は別に残るため、このステップで機械的に検証する。あわせて、レポート
     本文が言及する条文番号(「第28条」等)が実際に取得したlegal_sourcesに
     存在するかも検証する(Evidence Validation、`_validate_citations`。
     2026-08-13追加——legal_sourcesは元々law_id(e-Govの数値ID)とraw_sentence
     のみで人間可読な条番号を持たず、LLMに条文引用の材料を与えられていな
     かった。`engine/rule_engine.py`の`_extract_article_label()`/`_law_titles()`
     でnode_idから条番号を、law_idから法令名を解決し、根拠として提示できる
     ようにした上でこの検証を追加した)。いずれかで不一致があれば1回だけ
     訂正を指示して再生成し、それでも一致しなければ静かに誤った文章のまま
     返すのではなく、本文に明示的な警告を付記する。抽出できなかった場合は
     「検証不能」として扱い、不一致とはみなさない(過検出よりも見逃しを
     許容する設計)。
"""

import re
from typing import TypedDict

from langchain_anthropic import ChatAnthropic
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage
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
- 「建築基準法第28条」のような具体的な条文番号を挙げる場合は、必ず与えられた
  「関連しそうな法令根拠」に含まれる条番号のみを使うこと。関連しそうな
  法令根拠が無い、またはそこに条番号が含まれない場合、一般知識で条番号を
  補って挙げてはいけない(条文の存在有無や趣旨には触れてよいが、具体的な
  条番号は与えられたデータに無ければ書かないこと)。
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
    verification_attempts: int
    verification_mismatches: list[str]


# 検証ノード(verify_report)が許容する生成試行回数。初回生成+1回の
# 訂正再生成までで、それでも一致しなければ警告を付記して確定する
# (無限ループにしない)。
_MAX_GENERATION_ATTEMPTS = 2


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
                "(参考、確定的な根拠ではない。ここに無い条番号を一般知識で"
                "補って挙げてはいけない):"
            )
            for source in check["legal_sources"][:5]:
                sentence = (source.get("raw_sentence") or "")[:80]
                law_label = source.get("law_title") or source.get("law_id")
                citation = f"{law_label}{source['article']}" if source.get("article") else law_label
                lines.append(f"    - {citation}: {sentence}")

        lines.append("")

    return "\n".join(lines)


def _build_generate_report_node(model_name: str):
    async def generate_report(state: LegalReportState) -> dict:
        model = ChatAnthropic(model=model_name)
        summary = _summarize_for_prompt(state["checks"])

        messages = [
            SystemMessage(content=REPORT_SYSTEM_PROMPT),
            HumanMessage(content=f"以下の判定結果からレポートを作成してください:\n\n{summary}"),
        ]

        # verify_reportから差し戻された再生成の場合、前回の文章と具体的な
        # 不一致点を提示して訂正を指示する(白紙から書き直させるとまた別の
        # 数値ミスを作りかねないため、差分の修正という形で依頼する)。
        previous_report = state.get("report")
        mismatches = state.get("verification_mismatches") or []
        if previous_report and mismatches:
            messages.append(AIMessage(content=previous_report))
            messages.append(HumanMessage(content=(
                "先ほどのレポートには、判定結果との食い違いがありました。"
                "以下の点を修正した上で、レポート全文を書き直してください:\n"
                + "\n".join(f"- {m}" for m in mismatches)
            )))

        response = await model.ainvoke(messages)
        usage_metadata = getattr(response, "usage_metadata", None) or {}

        prior_usage = state.get("usage") or {"input_tokens": 0, "output_tokens": 0}

        return {
            "report": message_text(response),
            "usage": {
                "input_tokens": prior_usage.get("input_tokens", 0)
                + (usage_metadata.get("input_tokens") or 0),
                "output_tokens": prior_usage.get("output_tokens", 0)
                + (usage_metadata.get("output_tokens") or 0),
            },
        }

    return generate_report


_COUNTS_PATTERN = re.compile(
    r"PASS\s*(\d+)\s*件.{0,20}?FAIL\s*(\d+)\s*件.{0,20}?UNKNOWN\s*(\d+)\s*件",
    re.DOTALL,
)


def _extract_reported_counts(report_text: str) -> list[dict]:
    """レポート本文中の「PASS n件 / FAIL n件 / UNKNOWN n件」表記を出現順に抽出する。

    書式がREPORT_SYSTEM_PROMPTの想定と異なりマッチしなかった場合は、そのチェックの
    主張は「検証不能」として扱う(_validate_report_claimsが不一致とはみなさない)。
    """
    return [
        {"pass": int(m.group(1)), "fail": int(m.group(2)), "unknown": int(m.group(3))}
        for m in _COUNTS_PATTERN.finditer(report_text)
    ]


def _actual_counts(check: dict) -> dict:
    counts = {"pass": 0, "fail": 0, "unknown": 0}
    for item in check["items"]:
        if item["status"] in counts:
            counts[item["status"]] += 1
    return counts


def _validate_report_claims(checks: list[dict], report_text: str) -> list[str]:
    """checksの実件数とreport_text中の記載件数を突き合わせ、不一致の説明を返す。

    「未判定」(missing_inputs)のチェックはPASS/FAIL/UNKNOWN文が書かれない
    (_summarize_for_promptの分岐)ため対象外。抽出できた件数の分だけ、
    checksの出現順とreport_text中の出現順が対応するとみなして位置合わせする
    (best-effort。ズレて誤検出するより、抽出できなければ検証をスキップする
    方を優先する設計)。
    """
    checkable = [c for c in checks if not c.get("missing_inputs")]
    reported = _extract_reported_counts(report_text)

    mismatches: list[str] = []
    for check, claim in zip(checkable, reported):
        actual = _actual_counts(check)
        if claim != actual:
            mismatches.append(
                f"「{check['title']}」の件数表記が実際の判定結果と一致しません"
                f"(本文の記載: PASS {claim['pass']}件/FAIL {claim['fail']}件/"
                f"UNKNOWN {claim['unknown']}件、実際: PASS {actual['pass']}件/"
                f"FAIL {actual['fail']}件/UNKNOWN {actual['unknown']}件)。"
            )
    return mismatches


_ARTICLE_MENTION_PATTERN = re.compile(r"第(\d+)条(?:の(\d+))?")


def _cited_articles(report_text: str) -> set[str]:
    """レポート本文中の「第◯条」表記を正規化した集合として抽出する。"""
    cited = set()
    for m in _ARTICLE_MENTION_PATTERN.finditer(report_text):
        main, sub = m.group(1), m.group(2)
        cited.add(f"第{main}条の{sub}" if sub else f"第{main}条")
    return cited


def _validate_citations(checks: list[dict], report_text: str) -> list[str]:
    """レポート本文が言及する条番号が、実際に取得したlegal_sourcesに含まれる
    かを確認する(Evidence Validation)。

    engine/rule_engine.pyのlegal_sourcesは正規表現ベースの候補(ノイズを含む)
    でしかないため、LLMがそこに存在しない条番号を一般知識から書いてしまう
    (ハルシネーション引用)リスクが別に残る。checks全体のlegal_sourcesの
    和集合に対して照合する(チェックごとの見出しとの対応付けは行わない、
    位置ズレで誤検出するより見逃す方を優先する設計は_validate_report_claims
    と同じ)。
    """
    cited = _cited_articles(report_text)
    if not cited:
        return []

    known_articles: set[str] = set()
    for check in checks:
        for source in check.get("legal_sources", []):
            if source.get("article"):
                known_articles.add(source["article"])

    unfounded = sorted(cited - known_articles)
    return [
        f"レポート中で言及されている「{article}」は、実際に取得した法令根拠"
        "(legal_sources)には含まれていません。一般知識に基づく記述の可能性"
        "があります(ハルシネーション引用の疑い)。"
        for article in unfounded
    ]


def verify_report(state: LegalReportState) -> dict:
    mismatches = _validate_report_claims(state["checks"], state["report"])
    mismatches += _validate_citations(state["checks"], state["report"])
    attempts = state.get("verification_attempts", 0) + 1
    return {"verification_mismatches": mismatches, "verification_attempts": attempts}


def _route_after_verification(state: LegalReportState) -> str:
    mismatches = state.get("verification_mismatches") or []
    if not mismatches:
        return "ok"
    if state.get("verification_attempts", 0) < _MAX_GENERATION_ATTEMPTS:
        return "retry"
    return "warn"


def append_verification_warning(state: LegalReportState) -> dict:
    mismatches = state.get("verification_mismatches") or []
    warning = (
        "\n\n---\n⚠️ **自動検証の警告**: 以下の件数表記が、システムが計算した実際の"
        "判定結果と一致しない可能性があります。件数については「チェック項目別詳細」"
        "の表を正としてご確認ください。\n" + "\n".join(f"- {m}" for m in mismatches)
    )
    return {"report": state["report"] + warning}


def build_report_graph(model_name: str = "claude-opus-5"):
    graph = StateGraph(LegalReportState)
    graph.add_node("run_checks", _run_checks)
    graph.add_node("generate_report", _build_generate_report_node(model_name))
    graph.add_node("verify_report", verify_report)
    graph.add_node("append_verification_warning", append_verification_warning)

    graph.add_edge(START, "run_checks")
    graph.add_edge("run_checks", "generate_report")
    graph.add_edge("generate_report", "verify_report")
    graph.add_conditional_edges(
        "verify_report",
        _route_after_verification,
        {"ok": END, "retry": "generate_report", "warn": "append_verification_warning"},
    )
    graph.add_edge("append_verification_warning", END)

    return graph.compile()
