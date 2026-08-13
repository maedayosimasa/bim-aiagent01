"""BIMエージェントのセッション管理・実行インターフェース。

archicad_mcp/client.pyやlegal_mcp/client.pyと同じ「未設定でもクラッシュせず
状態を返す」方針を踏襲する: ANTHROPIC_API_KEYが未設定でもimport時には
例外にせず、実際にエージェントを呼び出す時点でAgentNotConfiguredErrorを送出する。

会話履歴はLangGraphのcheckpointer(AsyncSqliteSaver、main.pyのlifespanで
セットアップされSQLiteファイルに永続化される)にsession_id(LangGraphの
thread_id)単位で保存される。CLAUDE.md「⑨」に挙げられていた「会話/セッション
の永続化が無い」というギャップのうち、会話ターン単位の永続化はこれで解消する
(ただし解析結果(engine_analysis_results等)側の履歴化は別課題として残る)。
"""

import os

import anthropic
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langgraph.checkpoint.base import BaseCheckpointSaver

from ..database import db
from .graph import build_agent
from .message_utils import message_text as _message_text
from .pricing import calc_cost_usd
from .report_graph import build_report_graph

DEFAULT_MODEL = os.environ.get("ANTHROPIC_AGENT_MODEL", "claude-opus-5")

_checkpointer: BaseCheckpointSaver | None = None
_agent = None
_report_graph = None


class AgentNotConfiguredError(RuntimeError):
    """ANTHROPIC_API_KEYが未設定の場合に送出する。"""


class ConversationTooLongError(RuntimeError):
    """会話の累積トークン数がLLMのコンテキスト上限を超えた場合に送出する。

    (2026-08-11)list_bim_elements_tool等が全件ダンプしていたバグにより、
    一度でも巨大なツール結果が会話履歴(checkpointer)に書き込まれてしまうと、
    ツール自体を直してもそのセッションは以後ずっとエラーになり続けることが
    実データで発覚した——LangGraphは会話ターンごとに全メッセージ履歴を
    checkpointerに蓄積し、次のターンでも毎回全履歴をLLMへ送るため、一度
    肥大化した履歴は自然には縮まない。汎用のanthropic.APIErrorとして
    埋もれさせず、ユーザーに「新しい会話を開始してください」という具体的な
    次の行動を示すため専用の例外にしている。
    """


def _is_prompt_too_long_error(exc: anthropic.APIError) -> bool:
    return isinstance(exc, anthropic.BadRequestError) and "prompt is too long" in str(exc)


async def _ainvoke_with_context_guard(runnable, payload, config=None):
    try:
        if config is None:
            return await runnable.ainvoke(payload)
        return await runnable.ainvoke(payload, config=config)
    except anthropic.APIError as exc:
        if _is_prompt_too_long_error(exc):
            raise ConversationTooLongError(
                "この会話は長くなりすぎたため、これ以上続けられません"
                "(Claudeのコンテキスト上限を超えました)。"
                "「新しい会話を開始」から新しいセッションを始めてください。"
            ) from exc
        raise


def set_checkpointer(checkpointer: BaseCheckpointSaver | None) -> None:
    global _checkpointer, _agent
    _checkpointer = checkpointer
    _agent = None  # 次回呼び出し時に新しいcheckpointerで再構築させる


def reset() -> None:
    """テスト用: 構築済みのエージェント/レポートグラフのキャッシュを破棄する。"""
    global _agent, _report_graph
    _agent = None
    _report_graph = None


def is_configured() -> bool:
    return bool(os.environ.get("ANTHROPIC_API_KEY"))


def get_connection_info() -> dict:
    return {"configured": is_configured(), "model": DEFAULT_MODEL}


def _get_agent():
    global _agent
    if not is_configured():
        raise AgentNotConfiguredError(
            "ANTHROPIC_API_KEY が未設定です。バックエンドの環境変数に設定してください。"
        )
    if _agent is None:
        _agent = build_agent(checkpointer=_checkpointer, model_name=DEFAULT_MODEL)
    return _agent


def _get_report_graph():
    global _report_graph
    if not is_configured():
        raise AgentNotConfiguredError(
            "ANTHROPIC_API_KEY が未設定です。バックエンドの環境変数に設定してください。"
        )
    if _report_graph is None:
        _report_graph = build_report_graph(model_name=DEFAULT_MODEL)
    return _report_graph


def _tool_result_text(message: ToolMessage) -> str:
    return message.content if isinstance(message.content, str) else str(message.content)


def _extract_turn(messages: list) -> tuple[str, list[dict], dict]:
    # 直近のHumanMessage以降(=今回のターンで新しく増えた分)だけを対象にする。
    # checkpointer経由でmessagesには過去ターンの履歴も含まれているため。
    last_human_idx = None
    for i in range(len(messages) - 1, -1, -1):
        if isinstance(messages[i], HumanMessage):
            last_human_idx = i
            break
    turn_messages = messages[last_human_idx + 1:] if last_human_idx is not None else messages

    call_info_by_id: dict[str, dict] = {}
    for msg in turn_messages:
        if isinstance(msg, AIMessage):
            for call in msg.tool_calls or []:
                call_info_by_id[call["id"]] = {"name": call["name"], "args": call["args"]}

    response_text = ""
    tool_calls: list[dict] = []
    # ReActループ内でツール呼び出しを挟み複数回LLMを呼ぶことがあるため、
    # 今回のターンに含まれる全AIMessageのusage_metadataを合算する。
    input_tokens = 0
    output_tokens = 0

    for msg in turn_messages:
        if isinstance(msg, AIMessage):
            text = _message_text(msg)
            if text:
                response_text = text
            usage_metadata = getattr(msg, "usage_metadata", None) or {}
            input_tokens += usage_metadata.get("input_tokens") or 0
            output_tokens += usage_metadata.get("output_tokens") or 0
        elif isinstance(msg, ToolMessage):
            call_info = call_info_by_id.get(msg.tool_call_id, {})
            tool_calls.append({
                "name": call_info.get("name", msg.name),
                "args": call_info.get("args", {}),
                "result": _tool_result_text(msg),
            })

    usage = {"input_tokens": input_tokens, "output_tokens": output_tokens}

    return response_text, tool_calls, usage


def _record_token_usage(kind: str, session_id: str | None, usage: dict) -> None:
    input_tokens = usage.get("input_tokens") or 0
    output_tokens = usage.get("output_tokens") or 0

    if not input_tokens and not output_tokens:
        # AIMessageにusage_metadataが無かった(古いLangChainバージョン等)場合。
        # 0件の行を残しても意味が無いため記録しない。
        return

    cost_usd = calc_cost_usd(DEFAULT_MODEL, input_tokens, output_tokens)
    db.insert_token_usage(kind, session_id, DEFAULT_MODEL, input_tokens, output_tokens, cost_usd)


async def run_chat(session_id: str, message: str) -> dict:
    agent = _get_agent()
    config = {"configurable": {"thread_id": session_id}}

    result = await _ainvoke_with_context_guard(
        agent,
        {"messages": [HumanMessage(content=message)]},
        config=config,
    )

    response_text, tool_calls, usage = _extract_turn(result["messages"])
    _record_token_usage("chat", session_id, usage)

    return {
        "session_id": session_id,
        "response": response_text,
        "tool_calls": tool_calls,
    }


async def get_history(session_id: str) -> list[dict]:
    agent = _get_agent()
    config = {"configurable": {"thread_id": session_id}}

    state = await agent.aget_state(config)
    messages = state.values.get("messages", []) if state.values else []

    history = []

    for msg in messages:
        if isinstance(msg, HumanMessage):
            history.append({"role": "human", "content": msg.content})
        elif isinstance(msg, AIMessage):
            text = _message_text(msg)
            if text:
                history.append({"role": "ai", "content": text})
        elif isinstance(msg, ToolMessage):
            history.append({
                "role": "tool",
                "name": msg.name,
                "content": _tool_result_text(msg),
            })

    return history


async def run_legal_report() -> dict:
    """法規チェック→引用条文添付→レポート生成、の複数ステップグラフを実行する。

    会話(run_chat)とは別物で、session_id/checkpointerは使わない
    (毎回、登録済み全ルールを対象に最初から実行する単発のパイプライン)。
    詳細はreport_graph.pyのモジュールdocstring参照。
    """
    graph = _get_report_graph()

    result = await _ainvoke_with_context_guard(graph, {"checks": [], "report": ""})

    _record_token_usage("legal_report", None, result.get("usage") or {})

    return {"checks": result["checks"], "report": result["report"]}
