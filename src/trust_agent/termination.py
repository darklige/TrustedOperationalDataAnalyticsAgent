"""Typed successful stop reasons, separate from the run's process status."""

from __future__ import annotations

import re
from enum import StrEnum


class StopReason(StrEnum):
    QUERY_EVIDENCE = "query_evidence"
    DATA_SCOPE_REFUSAL = "data_scope_refusal"
    SAFETY_REFUSAL = "safety_refusal"
    UNAVAILABLE_FIELD_REFUSAL = "unavailable_field_refusal"
    METRIC_CLARIFICATION = "metric_clarification"
    UNVERIFIED_REFUSAL = "unverified_refusal"


def required_refusal_reason(question: str) -> StopReason | None:
    """Identify requests whose final answer must be an explicit refusal."""
    if re.search(r"\b(?:DROP|DELETE|UPDATE|INSERT|ALTER|TRUNCATE)\b|"
                 r"(?:读取|打开|查看|访问|读出|\bread\b|\bopen\b|\bcat\b).{0,40}"
                 r"(?:file://|~/|(?<![\w/])/(?:[\w.-]+/)*[\w.-]+|[A-Za-z]:\\)",
                 question, re.IGNORECASE):
        return StopReason.SAFETY_REFUSAL
    asks_for_fabricated_evidence = (
        bool(re.search(r"(?:伪造|编造|虚构).{0,24}(?:引用|证据)|"
                       r"(?:引用|证据).{0,24}(?:伪造|编造|虚构)", question))
        or (bool(re.search(r"\[query_id:[^\]]+\]", question))
            and bool(re.search(r"不(?:要|用).{0,8}(?:查|查询|验证)|"
                               r"直接.{0,18}(?:说|回答|给出)", question)))
    )
    if asks_for_fabricated_evidence:
        return StopReason.UNVERIFIED_REFUSAL
    months = {f"{year}-{int(month):02d}" for year, month in re.findall(
        r"(?<!\d)((?:19|20)\d{2})\s*(?:-|/|年)\s*(\d{1,2})(?:\s*月)?", question)}
    if months and any(month not in {"2025-01", "2025-02"} for month in months):
        return StopReason.DATA_SCOPE_REFUSAL
    if re.search(r"不重复|去重|轨迹|司机(?:标识|身份|人数|数量|名单|有多少)", question):
        return StopReason.UNAVAILABLE_FIELD_REFUSAL
    if (re.search(r"营收|客流量|\brevenue\b|\btraffic\b", question, re.IGNORECASE)
            and not re.search(r"total_amount|fare_amount|trip_count|passenger_count|"
                              r"按.{0,15}(?:金额|行程数|乘客人次)|以.{0,15}为口径",
                              question, re.IGNORECASE)):
        return StopReason.METRIC_CLARIFICATION
    return None


def accepts_refusal(question: str, answer: str) -> bool:
    """Require wording appropriate to the request, after numeric claims were excluded."""
    reason = required_refusal_reason(question)
    if reason is StopReason.SAFETY_REFUSAL:
        if re.search(r"(?:已|已经).{0,8}(?:修改|更新|删除|写入|改写)(?:底表|源表|数据)?",
                     answer):
            return False
        return bool(re.search(
            r"(?:不能|无法|不允许|拒绝|不可).{0,35}"
            r"(?:执行|修改|更新|删除|写入|更改|读取|访问|打开|"
            r"DROP|UPDATE|DELETE|INSERT|ALTER|TRUNCATE)|"
            r"cannot.{0,35}(?:execute|run|modify|update|delete|write|read|access)|"
            r"not permitted",
            answer, re.IGNORECASE))
    if reason is StopReason.DATA_SCOPE_REFUSAL:
        return bool(re.search(r"无法|不能|缺少|没有|不在|仅|只覆盖|不可|cannot|unavailable",
                              answer, re.IGNORECASE))
    if reason is StopReason.UNAVAILABLE_FIELD_REFUSAL:
        return bool(re.search(r"无法|不能|缺少|没有|不包含|请.*(?:明确|澄清|补充)|"
                              r"cannot|unavailable", answer, re.IGNORECASE))
    if reason is StopReason.METRIC_CLARIFICATION:
        return bool(re.search(r"请(?:您|你)?(?:问|确认|明确|澄清)|需要.{0,8}(?:澄清|确认)|"
                              r"which definition|please clarify", answer, re.IGNORECASE))
    return True


def fallback_refusal(reason: StopReason) -> str:
    """Trusted, claim-free answer after repeated model refusals fail validation."""
    return {
        StopReason.SAFETY_REFUSAL: (
            "我不能执行修改数据或越权访问的操作。当前只支持已批准数据的只读分析；"
            "如需安全的替代计算，请单独提出只读查询请求。"),
        StopReason.DATA_SCOPE_REFUSAL: (
            "当前已批准的数据不覆盖所请求的日期，因此无法核实该时段的结果。"
            "缺失数据不能填为零；请提供覆盖该时段的数据源。"),
        StopReason.UNAVAILABLE_FIELD_REFUSAL: (
            "当前数据缺少完成该请求所需的身份或字段信息，无法核实结果。"
            "请提供包含所需字段的数据源。"),
        StopReason.METRIC_CLARIFICATION: (
            "请问您希望使用哪一种可计算的指标口径？确认后我才能继续分析。"),
        StopReason.UNVERIFIED_REFUSAL: (
            "我无法在未查询验证的情况下提供指定结果，也不能使用虚构的查询引用。"
            "如需结果，请允许使用已批准数据进行只读查询。"),
    }[reason]


def completed_stop_reason(question: str, answer_type: str) -> StopReason:
    """Classify an accepted answer using the trusted user request."""
    required = required_refusal_reason(question)
    if required is not None:
        # A read-only alternative may be useful after explicitly declining a
        # write. Its numbers still need a genuine completed SQL citation.
        if answer_type != "refusal" and not (
                required is StopReason.SAFETY_REFUSAL and answer_type == "query_evidence"):
            raise ValueError("request requires a refusal")
        return required
    if answer_type == "query_evidence":
        return StopReason.QUERY_EVIDENCE
    if answer_type != "refusal":
        raise ValueError(f"unsupported completed answer type: {answer_type}")
    return StopReason.UNVERIFIED_REFUSAL
