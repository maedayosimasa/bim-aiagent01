from backend.engine.evidence import EvidenceConfidence, tag


def test_tag_adds_evidence_confidence_field():
    items = [{"a": 1}, {"a": 2}]

    tagged = tag(items, EvidenceConfidence.DETERMINISTIC)

    assert tagged == [
        {"a": 1, "evidence_confidence": "deterministic"},
        {"a": 2, "evidence_confidence": "deterministic"},
    ]
    # 元のリスト/dictを書き換えない(呼び出し元の値を破壊しない)。
    assert items == [{"a": 1}, {"a": 2}]


def test_tag_empty_list_returns_empty_list():
    assert tag([], EvidenceConfidence.CANDIDATE) == []
