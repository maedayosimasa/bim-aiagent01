"""agent/service.pyのツール呼び出しループとメッセージ抽出ロジックのテスト。

実際のAnthropic APIは呼ばず(課金・ネットワーク依存を避けるため)、
langchain_core.language_models.BaseChatModelを実装した固定応答の
フェイクモデルに差し替える(test_tapir.pyのInMemoryTransport+フェイク
サーバーや、test_main_legal_*.pyのmonkeypatchと同じ考え方)。
"""

import asyncio
import json

import anthropic
import httpx
import pytest
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage
from langchain_core.outputs import ChatGeneration, ChatResult

from backend.agent import graph as agent_graph
from backend.agent import report_graph as agent_report_graph
from backend.agent import service as agent_service


class _FakeToolCallingModel(BaseChatModel):
    """あらかじめ用意したAIMessage列を呼び出し順に返すだけのフェイクLLM。"""

    responses: list[AIMessage]
    calls: int = 0

    def bind_tools(self, tools, **kwargs):
        return self

    def _generate(self, messages, stop=None, run_manager=None, **kwargs):
        response = self.responses[self.calls]
        self.calls += 1
        return ChatResult(generations=[ChatGeneration(message=response)])

    @property
    def _llm_type(self):
        return "fake-tool-calling-model"


@pytest.fixture(autouse=True)
def _reset_agent_state():
    agent_service.set_checkpointer(None)
    agent_service.reset()
    yield
    agent_service.set_checkpointer(None)
    agent_service.reset()


def _install_fake_model(monkeypatch, responses):
    fake_model = _FakeToolCallingModel(responses=responses)
    monkeypatch.setattr(agent_graph, "ChatAnthropic", lambda model: fake_model)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")


def test_run_chat_calls_tool_and_returns_final_answer(monkeypatch, sample_elements):
    responses = [
        AIMessage(
            content="",
            tool_calls=[
                {"name": "list_bim_elements_tool", "args": {}, "id": "call_1", "type": "tool_call"}
            ],
        ),
        AIMessage(content="キャッシュには4件のBIM要素があります。"),
    ]
    _install_fake_model(monkeypatch, responses)

    result = asyncio.run(agent_service.run_chat("session-1", "何件BIM要素がある?"))

    assert result["session_id"] == "session-1"
    assert result["response"] == "キャッシュには4件のBIM要素があります。"
    assert len(result["tool_calls"]) == 1

    tool_call = result["tool_calls"][0]
    assert tool_call["name"] == "list_bim_elements_tool"
    assert tool_call["args"] == {}

    # 引数無しの呼び出しは件数サマリのみを返す(全件ダンプはしない、
    # 2026-08-11のコンテキスト上限超過事故を踏まえた仕様。詳細は
    # test_agent_tools.py参照)。
    summary = json.loads(tool_call["result"])
    assert summary["total"] == 4
    assert summary["by_type"] == {"Wall": 1, "Door": 1, "Room": 2}


def test_run_chat_without_tool_call(monkeypatch):
    responses = [AIMessage(content="こんにちは、何をお手伝いしましょうか?")]
    _install_fake_model(monkeypatch, responses)

    result = asyncio.run(agent_service.run_chat("session-2", "こんにちは"))

    assert result["response"] == "こんにちは、何をお手伝いしましょうか?"
    assert result["tool_calls"] == []


def test_run_chat_without_api_key_raises(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)

    with pytest.raises(agent_service.AgentNotConfiguredError):
        agent_service._get_agent()


class _PromptTooLongModel(BaseChatModel):
    """Claude APIの'prompt is too long'エラーを再現するフェイクLLM。

    2026-08-11に実データで発覚した事故(list_bim_elements_tool等が全件
    ダンプしていたため、一度巨大なツール結果がcheckpointerの会話履歴に
    書き込まれると、そのセッションは以後ずっと同じエラーを返し続ける)の
    回帰テスト用。
    """

    def bind_tools(self, tools, **kwargs):
        return self

    def _generate(self, messages, stop=None, run_manager=None, **kwargs):
        response = httpx.Response(
            400, request=httpx.Request("POST", "https://api.anthropic.com/v1/messages")
        )
        raise anthropic.BadRequestError(
            "prompt is too long: 2191208 tokens > 1000000 maximum",
            response=response,
            body=None,
        )

    @property
    def _llm_type(self):
        return "fake-prompt-too-long-model"


def test_run_chat_raises_conversation_too_long_on_prompt_too_long_error(monkeypatch):
    monkeypatch.setattr(agent_graph, "ChatAnthropic", lambda model: _PromptTooLongModel())
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")

    with pytest.raises(agent_service.ConversationTooLongError):
        asyncio.run(agent_service.run_chat("poisoned-session", "テスト"))


def _install_fake_report_model(monkeypatch, report_text):
    fake_model = _FakeToolCallingModel(responses=[AIMessage(content=report_text)])
    monkeypatch.setattr(agent_report_graph, "ChatAnthropic", lambda model: fake_model)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")


def test_run_legal_report_runs_checks_then_generates_report(monkeypatch, sample_elements):
    monkeypatch.setenv("LAND_USE_CATEGORY", "residential")
    fake_report_text = "## 採光有効面積比\nFAIL 2件\n\n## バリアフリー最小ドア幅\nUNKNOWN 1件\n\n総評: 参考値であり法的適合は保証しません。"
    _install_fake_report_model(monkeypatch, fake_report_text)

    result = asyncio.run(agent_service.run_legal_report())

    assert result["report"] == fake_report_text

    checks_by_id = {check["rule_id"]: check for check in result["checks"]}
    assert set(checks_by_id) == {
        "daylighting_ratio", "accessible_door_width", "effective_daylighting_ratio",
    }

    # sample_elementsのdoor001はproperties.archicad_details.widthを持たないため
    # 実測値が取得できず、判定はUNKNOWNになる(engine/code_engine.py参照)。
    door_check = checks_by_id["accessible_door_width"]
    assert len(door_check["items"]) == 1
    assert door_check["items"][0]["status"] == "unknown"
    assert door_check["legal_sources"] == []  # Legal Knowledge Builder未接続

    # sample_elementsにはWindow要素が無いため、有効採光面積比は各部屋とも
    # 0.0(未解決の窓は無いのでUNKNOWNではなく確定的にFAIL)になる。
    effective_daylighting_check = checks_by_id["effective_daylighting_ratio"]
    assert len(effective_daylighting_check["items"]) == 2  # room001, room002
    assert all(item["status"] == "fail" for item in effective_daylighting_check["items"])


def test_summarize_for_prompt_reports_missing_inputs_as_unjudged():
    checks = [
        {
            "title": "有効採光面積比",
            "rule_id": "effective_daylighting_ratio",
            "comparator": "gte",
            "threshold": 0.14285714285714285,
            "threshold_unit": "ratio",
            "disclaimer": "参考値です。",
            "legal_sources": [],
            "items": [],
            "missing_inputs": [
                {"key": "land_use_category", "label": "用途地域", "description": "..."},
            ],
        }
    ]

    summary = agent_report_graph._summarize_for_prompt(checks)

    assert "未判定" in summary
    assert "用途地域" in summary
    assert "判定基準" not in summary  # 未判定チェックはPASS/FAIL集計を出さない


def test_run_legal_report_without_api_key_raises(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)

    with pytest.raises(agent_service.AgentNotConfiguredError):
        asyncio.run(agent_service.run_legal_report())
