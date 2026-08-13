from backend.agent.router import AGENT_TOOLS, BIM_TOOLS, LEGAL_TOOLS, route_tools


def _names(tools):
    return {t.name for t in tools}


def test_route_tools_bim_only_message_returns_bim_subset():
    result = route_tools("この部屋に隣接するドアと窓を教えて")

    assert _names(result) == _names(BIM_TOOLS)
    assert _names(result) < _names(AGENT_TOOLS)  # 全ツールより狭い


def test_route_tools_legal_only_message_returns_legal_subset():
    result = route_tools("採光の法規チェックはPASSしていますか?根拠となる条文も教えて")

    assert _names(result) == _names(LEGAL_TOOLS)
    assert _names(result) < _names(AGENT_TOOLS)


def test_route_tools_mixed_message_returns_union():
    result = route_tools("部屋の採光チェックをして")  # BIM系("部屋")+LEGAL系("チェック")

    assert _names(result) == _names(BIM_TOOLS) | _names(LEGAL_TOOLS)


def test_route_tools_falls_back_to_all_tools_when_ambiguous():
    result = route_tools("こんにちは、何ができますか?")

    assert result is AGENT_TOOLS


def test_route_tools_no_duplicate_tool_names():
    result = route_tools("部屋の採光チェックをして")

    names = [t.name for t in result]
    assert len(names) == len(set(names))
