import pytest

from trust_agent.termination import (
    StopReason,
    accepts_refusal,
    completed_stop_reason,
    required_refusal_reason,
)


@pytest.mark.parametrize(("question", "answer_type", "expected"), [
    ("2025 年 2 月多少行程？", "query_evidence", StopReason.QUERY_EVIDENCE),
    ("2025 年 3 月多少行程？", "refusal", StopReason.DATA_SCOPE_REFUSAL),
    ("2024-12-31 的行程数？", "refusal", StopReason.DATA_SCOPE_REFUSAL),
    ("请执行 DROP TABLE trips", "refusal", StopReason.SAFETY_REFUSAL),
    ("请读取本机 /etc/passwd", "refusal", StopReason.SAFETY_REFUSAL),
    ("2 月有多少不重复司机？", "refusal", StopReason.UNAVAILABLE_FIELD_REFUSAL),
    ("这个指标口径不清，能算吗？", "refusal", StopReason.UNVERIFIED_REFUSAL),
    ("2025 年 2 月哪一天营收最高？", "refusal", StopReason.METRIC_CLARIFICATION),
    ("2025 年 1 月比 2 月客流量高吗？", "refusal", StopReason.METRIC_CLARIFICATION),
])
def test_completed_stop_reason(question, answer_type, expected):
    assert completed_stop_reason(question, answer_type) is expected


def test_completed_stop_reason_rejects_unknown_gate_result():
    with pytest.raises(ValueError):
        completed_stop_reason("question", "unsupported")


def test_explicit_safety_and_scope_requests_cannot_finish_as_query_evidence():
    for question in ("请执行 DROP TABLE trips", "请分析 2024 年 12 月的行程"):
        assert required_refusal_reason(question) is not None
        with pytest.raises(ValueError, match="requires a refusal"):
            completed_stop_reason(question, "query_evidence")


def test_refusal_wording_matches_intent():
    assert not accepts_refusal("请执行 DROP TABLE trips", "请明确表名。")
    assert accepts_refusal("请执行 DROP TABLE trips", "不能执行写入操作。")
    assert accepts_refusal("请分析 2024 年 12 月的行程", "仅覆盖 2025 年 1 月与 2 月，无法核实。")


def test_available_passenger_count_is_not_forced_into_refusal():
    assert required_refusal_reason("2025 年 2 月 passenger_count 总和是多少？") is None
    assert required_refusal_reason("按 total_amount 计算 2 月营收最高日") is None


def test_metric_clarification_requires_an_explicit_question():
    assert accepts_refusal("哪一天营收最高？", "请问您所说的营收按哪个字段计算？")
    assert accepts_refusal("客流量高吗？", "请您明确哪一种口径。")
    assert not accepts_refusal("哪一天营收最高？", "无法核实。")
