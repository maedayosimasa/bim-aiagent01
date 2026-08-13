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
from langchain_core.messages import AIMessage, ToolMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from langgraph.checkpoint.memory import InMemorySaver

from backend.agent import graph as agent_graph
from backend.agent import report_graph as agent_report_graph
from backend.agent import service as agent_service
from backend.database import db as db_module


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


class _RecordingFakeToolCallingModel(_FakeToolCallingModel):
    """_FakeToolCallingModelに加え、呼び出しごとに受け取ったmessagesを記録する。"""

    received: list = []

    def _generate(self, messages, stop=None, run_manager=None, **kwargs):
        self.received.append(list(messages))
        return super()._generate(messages, stop=stop, run_manager=run_manager, **kwargs)


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


def test_run_chat_records_token_usage(monkeypatch, test_db):
    # ReActループ内でツール呼び出しを挟み2回LLMを呼ぶケース。両方のAIMessageの
    # usage_metadataが合算されて、session_id単位の1「作業」として記録される
    # ことを確認する(agent/pricing.pyの料金表でclaude-opus-5のコストも検証)。
    responses = [
        AIMessage(
            content="",
            tool_calls=[
                {"name": "list_bim_elements_tool", "args": {}, "id": "call_1", "type": "tool_call"}
            ],
            usage_metadata={"input_tokens": 100, "output_tokens": 20, "total_tokens": 120},
        ),
        AIMessage(
            content="0件です。",
            usage_metadata={"input_tokens": 150, "output_tokens": 30, "total_tokens": 180},
        ),
    ]
    _install_fake_model(monkeypatch, responses)

    asyncio.run(agent_service.run_chat("session-usage", "何件?"))

    jobs = db_module.get_token_usage_by_job()
    assert len(jobs) == 1
    job = jobs[0]
    assert job["kind"] == "chat"
    assert job["job_id"] == "session-usage"
    assert job["call_count"] == 1
    assert job["input_tokens"] == 250
    assert job["output_tokens"] == 50
    assert job["cost_usd"] == pytest.approx((250 * 5.00 + 50 * 25.00) / 1_000_000)


def test_run_chat_without_usage_metadata_records_nothing(monkeypatch, test_db):
    # フェイクモデルがusage_metadataを付与しない場合(古いLangChain挙動の
    # フォールバック確認)、0件の行を残さないことを確認する。
    responses = [AIMessage(content="こんにちは")]
    _install_fake_model(monkeypatch, responses)

    asyncio.run(agent_service.run_chat("session-no-usage", "こんにちは"))

    assert db_module.get_token_usage_by_job() == []


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


def _tool_call_response(rule_id):
    return AIMessage(
        content="",
        tool_calls=[{
            "name": "engine_legal_rules_evaluate_tool",
            "args": {"rule_id": rule_id},
            "id": "call_1", "type": "tool_call",
        }],
    )


def test_run_chat_interrupts_on_missing_legal_inputs_then_resumes(monkeypatch, sample_elements):
    # (2026-08-13追加、Missing Input Interrupt/Resumeパターン)
    # engine_legal_rules_evaluate_toolはbackendの環境変数からしか法規条件を
    # 読めない(legal_inputs.py)。値が未設定の間はLangGraphのinterrupt()で
    # 会話を一時停止し、値を設定→backend再起動を経て初めて再開できることを
    # 確認する回帰テスト。永続化(checkpointer)が無いと再開自体が成立しない
    # ため、テスト用にInMemorySaverを明示的に設定する。
    agent_service.set_checkpointer(InMemorySaver())
    monkeypatch.delenv("LAND_USE_CATEGORY", raising=False)
    _install_fake_model(monkeypatch, [
        _tool_call_response("effective_daylighting_ratio"),
        AIMessage(content="用途地域が設定されたので判定できました。"),
    ])

    result = asyncio.run(agent_service.run_chat("session-interrupt", "有効採光面積比をチェックして"))

    assert result["interrupted"] is True
    assert result["tool_calls"] == []
    assert result["interrupt"]["rule_id"] == "effective_daylighting_ratio"
    missing_keys = {m["key"] for m in result["interrupt"]["missing_inputs"]}
    assert missing_keys == {"land_use_category"}
    assert "用途地域" in result["response"]

    # 実際の運用では「値を設定してbackendを再起動する」が挟まる。テストでは
    # env varの変更で代替する(checkpointerに永続化された一時停止状態は
    # プロセス再起動を跨いでも残る設計だが、ここではプロセス内で検証する)。
    monkeypatch.setenv("LAND_USE_CATEGORY", "residential")
    resumed = asyncio.run(agent_service.resume_chat("session-interrupt"))

    assert resumed["interrupted"] is False
    assert resumed["interrupt"] is None
    assert resumed["response"] == "用途地域が設定されたので判定できました。"


def test_run_chat_interrupts_again_when_still_missing_after_resume(monkeypatch, sample_elements):
    agent_service.set_checkpointer(InMemorySaver())
    monkeypatch.delenv("LAND_USE_CATEGORY", raising=False)
    _install_fake_model(monkeypatch, [_tool_call_response("effective_daylighting_ratio")])

    result = asyncio.run(agent_service.run_chat("session-interrupt-2", "チェックして"))
    assert result["interrupted"] is True

    # LAND_USE_CATEGORYをまだ設定しないまま再開を指示すると、再びinterruptされる
    # (無限リトライではなく、都度ユーザーへフィードバックする)。
    resumed = asyncio.run(agent_service.resume_chat("session-interrupt-2"))

    assert resumed["interrupted"] is True
    assert resumed["interrupt"]["rule_id"] == "effective_daylighting_ratio"


def test_build_agent_clears_stale_tool_results_but_preserves_evidence_tools(monkeypatch):
    # (2026-08-13追加、Context 3層分離)ContextEditingMiddleware(agent/graph.py)
    # が、除外リストに無いツール結果(探索的なBIM検索等)は古くなると
    # [cleared]に置き換える一方、Rule Engineの確定的な判定結果
    # (engine_code_daylighting_tool等)は間引かれずに残ることを確認する。
    monkeypatch.setattr(agent_graph, "_CLEAR_TOOL_USES_TRIGGER_TOKENS", 1)
    monkeypatch.setattr(agent_graph, "_CLEAR_TOOL_USES_KEEP", 1)

    responses = [
        AIMessage(content="", tool_calls=[
            {"name": "list_bim_elements_tool", "args": {}, "id": "call_1", "type": "tool_call"}
        ]),
        AIMessage(content="", tool_calls=[
            {"name": "engine_code_daylighting_tool", "args": {}, "id": "call_2", "type": "tool_call"}
        ]),
        AIMessage(content="", tool_calls=[
            {"name": "list_bim_elements_tool", "args": {}, "id": "call_3", "type": "tool_call"}
        ]),
        AIMessage(content="完了しました。"),
    ]
    fake_model = _RecordingFakeToolCallingModel(responses=responses, received=[])
    monkeypatch.setattr(agent_graph, "ChatAnthropic", lambda model: fake_model)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")

    asyncio.run(agent_service.run_chat("session-context-edit", "採光チェックして"))

    # 4回目(最終)のモデル呼び出しに渡されたメッセージを検証する。
    last_call_messages = fake_model.received[-1]
    by_call_id = {
        m.tool_call_id: m for m in last_call_messages if isinstance(m, ToolMessage)
    }

    # list_bim_elements_tool(call_1、除外リストに無い)は古くなり間引かれる。
    assert by_call_id["call_1"].content == "[cleared]"
    # engine_code_daylighting_tool(call_2)は除外リストにあるため間引かれない。
    assert by_call_id["call_2"].content != "[cleared]"
    # 直近1件(keep=1)は無条件に残るため、call_3も間引かれない。
    assert by_call_id["call_3"].content != "[cleared]"


def test_get_history_shows_full_content_even_after_clearing(monkeypatch, test_db):
    # ContextEditingMiddlewareはモデルへのリクエスト直前のコピーだけを編集し、
    # checkpointerに永続化された会話履歴そのものは変更しない(get_history()・
    # frontendの表示には常に完全な履歴が残ることを確認する)。
    monkeypatch.setattr(agent_graph, "_CLEAR_TOOL_USES_TRIGGER_TOKENS", 1)
    monkeypatch.setattr(agent_graph, "_CLEAR_TOOL_USES_KEEP", 0)

    responses = [
        AIMessage(content="", tool_calls=[
            {"name": "list_bim_elements_tool", "args": {}, "id": "call_1", "type": "tool_call"}
        ]),
        AIMessage(content="", tool_calls=[
            {"name": "list_bim_elements_tool", "args": {}, "id": "call_2", "type": "tool_call"}
        ]),
        AIMessage(content="完了しました。"),
    ]
    _install_fake_model(monkeypatch, responses)
    agent_service.set_checkpointer(InMemorySaver())

    asyncio.run(agent_service.run_chat("session-history-intact", "要素数を教えて"))

    history = asyncio.run(agent_service.get_history("session-history-intact"))
    tool_entries = [h for h in history if h["role"] == "tool"]

    assert len(tool_entries) == 2
    assert all(entry["content"] != "[cleared]" for entry in tool_entries)


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
        "ventilation_ratio", "floor_area_ratio", "site_road_frontage",
        "evacuation_walking_distance", "building_coverage_ratio",
        "road_slant_compliance", "adjacent_boundary_slant_compliance",
        "north_slant_compliance", "height_district_compliance",
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

    # sample_elementsには敷地境界線Zoneが無いため、容積率は対象0件(判定不能)。
    assert checks_by_id["floor_area_ratio"]["items"] == []
    # yoseki_ritsu未設定のため、そもそもmissing_inputsになる。
    assert checks_by_id["floor_area_ratio"]["missing_inputs"] != []


def test_run_legal_report_records_token_usage(monkeypatch, sample_elements):
    monkeypatch.setenv("LAND_USE_CATEGORY", "residential")
    fake_model = _FakeToolCallingModel(
        responses=[
            AIMessage(
                content="レポート本文",
                usage_metadata={"input_tokens": 400, "output_tokens": 80, "total_tokens": 480},
            )
        ]
    )
    monkeypatch.setattr(agent_report_graph, "ChatAnthropic", lambda model: fake_model)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")

    asyncio.run(agent_service.run_legal_report())

    jobs = [j for j in db_module.get_token_usage_by_job() if j["kind"] == "legal_report"]
    assert len(jobs) == 1
    assert jobs[0]["input_tokens"] == 400
    assert jobs[0]["output_tokens"] == 80
    # run_legal_report()にはsession_idが無いため、job_idはid列から合成される。
    assert jobs[0]["job_id"].startswith("legal_report_")


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


def _make_check(rule_id, title, *, pass_count=0, fail_count=0, unknown_count=0, legal_sources=None):
    items = (
        [{"status": "pass", "target_guid": f"p{i}", "target_name": None,
          "measured_value": 1, "unit": None} for i in range(pass_count)]
        + [{"status": "fail", "target_guid": f"f{i}", "target_name": None,
            "measured_value": 1, "unit": None} for i in range(fail_count)]
        + [{"status": "unknown", "target_guid": f"u{i}", "target_name": None,
            "measured_value": None, "unit": None} for i in range(unknown_count)]
    )
    return {
        "rule_id": rule_id,
        "title": title,
        "comparator": "gte",
        "threshold": 0.1,
        "threshold_unit": None,
        "disclaimer": "参考値です。",
        "legal_sources": legal_sources or [],
        "items": items,
        "missing_inputs": [],
    }


def _install_fake_checks(monkeypatch, checks):
    async def _fake_evaluate(rule):
        return next(c for c in checks if c["rule_id"] == rule["rule_id"])

    monkeypatch.setattr(
        agent_report_graph, "load_legal_rules",
        lambda: [{"rule_id": c["rule_id"]} for c in checks],
    )
    monkeypatch.setattr(agent_report_graph, "evaluate_legal_rule", _fake_evaluate)


def test_validate_report_claims_accepts_matching_counts():
    checks = [_make_check("daylighting_ratio", "採光有効面積比", pass_count=2, fail_count=1)]
    report_text = "■採光有効面積比\n結果: PASS 2件 / FAIL 1件 / UNKNOWN 0件"

    assert agent_report_graph._validate_report_claims(checks, report_text) == []


def test_validate_report_claims_detects_mismatch():
    checks = [_make_check("daylighting_ratio", "採光有効面積比", pass_count=2, fail_count=1)]
    report_text = "■採光有効面積比\n結果: PASS 1件 / FAIL 1件 / UNKNOWN 0件"

    mismatches = agent_report_graph._validate_report_claims(checks, report_text)

    assert len(mismatches) == 1
    assert "採光有効面積比" in mismatches[0]


def test_validate_report_claims_skips_unparseable_text():
    # 想定と異なる書式で件数が抽出できない場合は「検証不能」として扱い、
    # 不一致とはみなさない(過検出よりも見逃しを許容する設計)。
    checks = [_make_check("daylighting_ratio", "採光有効面積比", pass_count=2, fail_count=1)]
    report_text = "採光は概ね良好です。"

    assert agent_report_graph._validate_report_claims(checks, report_text) == []


def test_validate_citations_accepts_article_present_in_legal_sources():
    checks = [_make_check(
        "daylighting_ratio", "採光有効面積比", pass_count=1,
        legal_sources=[{"law_title": "建築基準法", "article": "第28条", "raw_sentence": "..."}],
    )]
    report_text = "建築基準法第28条により、採光を確認しました。"

    assert agent_report_graph._validate_citations(checks, report_text) == []


def test_validate_citations_flags_article_not_in_legal_sources():
    # legal_sourcesに含まれない条番号への言及は、LLMの一般知識由来の
    # ハルシネーション引用の可能性がある(Evidence Validation)。
    checks = [_make_check("daylighting_ratio", "採光有効面積比", pass_count=1, legal_sources=[])]
    report_text = "建築基準法第28条により、採光を確認しました。"

    mismatches = agent_report_graph._validate_citations(checks, report_text)

    assert len(mismatches) == 1
    assert "第28条" in mismatches[0]


def test_validate_citations_no_mention_is_not_flagged():
    checks = [_make_check("daylighting_ratio", "採光有効面積比", pass_count=1, legal_sources=[])]
    report_text = "採光は概ね良好です。"

    assert agent_report_graph._validate_citations(checks, report_text) == []


def test_run_legal_report_appends_warning_for_unfounded_citation(monkeypatch, test_db):
    checks = [_make_check("daylighting_ratio", "採光有効面積比", pass_count=1, legal_sources=[])]
    _install_fake_checks(monkeypatch, checks)

    # PASS/FAIL/UNKNOWN件数は正しいが、legal_sourcesに存在しない条番号を
    # 挙げている(ハルシネーション引用)ケース。
    report_with_bad_citation = (
        "■採光有効面積比\n結果: PASS 1件 / FAIL 0件 / UNKNOWN 0件\n"
        "建築基準法第28条により適合しています。\n総評: 参考値です。"
    )
    fake_model = _FakeToolCallingModel(responses=[
        AIMessage(content=report_with_bad_citation),
        AIMessage(content=report_with_bad_citation),
    ])
    monkeypatch.setattr(agent_report_graph, "ChatAnthropic", lambda model: fake_model)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")

    result = asyncio.run(agent_service.run_legal_report())

    assert fake_model.calls == 2
    assert "自動検証の警告" in result["report"]
    assert "第28条" in result["report"]


def test_run_legal_report_retries_once_on_mismatch_then_succeeds(monkeypatch, test_db):
    checks = [_make_check("daylighting_ratio", "採光有効面積比", pass_count=2, fail_count=1)]
    _install_fake_checks(monkeypatch, checks)

    wrong_report = "■採光有効面積比\n結果: PASS 1件 / FAIL 1件 / UNKNOWN 0件\n総評: 参考値です。"
    correct_report = "■採光有効面積比\n結果: PASS 2件 / FAIL 1件 / UNKNOWN 0件\n総評: 参考値です。"
    fake_model = _FakeToolCallingModel(responses=[
        AIMessage(content=wrong_report, usage_metadata={"input_tokens": 100, "output_tokens": 20, "total_tokens": 120}),
        AIMessage(content=correct_report, usage_metadata={"input_tokens": 150, "output_tokens": 30, "total_tokens": 180}),
    ])
    monkeypatch.setattr(agent_report_graph, "ChatAnthropic", lambda model: fake_model)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")

    result = asyncio.run(agent_service.run_legal_report())

    assert result["report"] == correct_report
    assert fake_model.calls == 2
    assert "自動検証の警告" not in result["report"]


def test_run_legal_report_appends_warning_when_mismatch_persists(monkeypatch, test_db):
    checks = [_make_check("daylighting_ratio", "採光有効面積比", pass_count=2, fail_count=1)]
    _install_fake_checks(monkeypatch, checks)

    wrong_report = "■採光有効面積比\n結果: PASS 1件 / FAIL 1件 / UNKNOWN 0件\n総評: 参考値です。"
    fake_model = _FakeToolCallingModel(responses=[
        AIMessage(content=wrong_report),
        AIMessage(content=wrong_report),
    ])
    monkeypatch.setattr(agent_report_graph, "ChatAnthropic", lambda model: fake_model)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")

    result = asyncio.run(agent_service.run_legal_report())

    assert fake_model.calls == 2
    assert "自動検証の警告" in result["report"]
    assert result["report"].startswith(wrong_report)


