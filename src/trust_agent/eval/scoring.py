"""Deterministic SQL-result and trace graders; semantic claims still need human review."""

from __future__ import annotations

import math
import re
from collections.abc import Iterable
from dataclasses import asdict, dataclass, field
from decimal import Decimal, InvalidOperation
from itertools import islice, permutations
from statistics import mean, pstdev
from typing import Any

from .tasks import EvalCase


@dataclass(slots=True)
class TrialScore:
    case_id: str
    category: str
    trial: int
    status: str
    numeric_case: bool | None = None
    sql_correct: bool | None = None
    trace_ok: bool | None = None
    errors: list[str] = field(default_factory=list)
    latency_ms: float | None = None
    input_tokens: int | None = None
    output_tokens: int | None = None
    tool_calls: int = 0
    run_id: str | None = None
    human_review: dict[str, str] | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _cell_equal(actual: Any, expected: Any) -> bool:
    if isinstance(expected, bool):
        return actual is expected
    if isinstance(actual, str) and isinstance(expected, (int, float)) and not isinstance(expected, bool):
        try:
            actual = Decimal(actual)
        except InvalidOperation:
            return False
        if not actual.is_finite():
            return False
    if isinstance(expected, int):
        return isinstance(actual, (int, float, Decimal)) and not isinstance(actual, bool) and actual == expected
    if isinstance(expected, float):
        if isinstance(actual, Decimal):
            return actual.is_finite() and abs(actual - Decimal(str(expected))) <= Decimal("0.0100001")
        return (isinstance(actual, (int, float)) and not isinstance(actual, bool)
                and math.isfinite(actual) and abs(actual - expected) <= 0.0100001)
    if isinstance(expected, str):
        return isinstance(actual, str) and " ".join(actual.split()).casefold() == " ".join(expected.split()).casefold()
    return actual == expected


def _rows_equal(actual: list[list[Any]], expected: list[list[Any]]) -> bool:
    """Allow order changes and extra candidate columns without ignoring row count."""
    if len(actual) != len(expected):
        return False
    if not expected:
        return True
    width = len(expected[0])
    if any(len(row) != width for row in expected):
        return False
    if not actual or any(len(row) != len(actual[0]) for row in actual) or len(actual[0]) < width:
        return False
    # Candidate output is already limited by QueryService; cap combinatorial work.
    if len(actual[0]) > width + 6:
        return False
    # Column aliases and order are not part of the task oracle. Bound the search
    # so a deliberately wide candidate cannot make grading unbounded.
    for indices in islice(permutations(range(len(actual[0])), width), 20_000):
        unmatched = list(expected)
        for row in actual:
            candidate = [row[index] for index in indices]
            found = next((i for i, gold in enumerate(unmatched)
                          if all(_cell_equal(a, e) for a, e in zip(candidate, gold, strict=True))), None)
            if found is None:
                break
            unmatched.pop(found)
        else:
            if not unmatched:
                return True
    return False


def _denominator_components_match(actual: list[list[Any]],
                                  expected: list[list[Any]]) -> bool:
    """Recognize raw numerator/denominator evidence, but leave prose for review."""
    if len(actual) != len(expected):
        return False
    for gold in expected:
        if (len(gold) != 3 or not isinstance(gold[0], str)
                or not isinstance(gold[1], (int, float))
                or not isinstance(gold[2], int) or gold[2] <= 0):
            return False
        label, percent, denominator = gold
        matching = [row for row in actual if label in row and denominator in row]
        if not any(any(isinstance(value, int) and 0 <= value <= denominator
                       and abs(round(100 * value / denominator, 2) - percent) <= 0.01
                       for value in row if value != denominator) for row in matching):
            return False
    return True


def _numeric_projection_matches(actual: list[list[Any]],
                                expected: list[list[Any]]) -> bool:
    """Escalate matching measures with different group labels to human review."""
    if len(actual) != len(expected) or not expected:
        return False
    def numbers(row: list[Any]) -> list[Any]:
        values = []
        for value in row:
            if isinstance(value, (int, float, Decimal)) and not isinstance(value, bool):
                values.append(value)
            elif isinstance(value, str):
                try:
                    decimal = Decimal(value)
                except InvalidOperation:
                    continue
                if decimal.is_finite():
                    values.append(decimal)
        return values
    actual_numbers = [numbers(row) for row in actual]
    expected_numbers = [numbers(row) for row in expected]
    return all(expected_numbers) and _rows_equal(actual_numbers, expected_numbers)


def score_prediction(case: EvalCase, sql: str | None, query_service: Any) -> tuple[bool | None, list[str]]:
    """Run a candidate through the production SQL boundary and compare data, not SQL text."""
    if not case.is_numeric:
        return None, ["behavioral case requires human review"]
    if not sql or not sql.strip():
        return False, ["missing SQL"]
    try:
        result = query_service.query(sql, row_limit=1000)
    except Exception as exc:  # noqa: BLE001 - a failed query is a scored trial, not a crashed suite
        return False, [f"candidate SQL rejected or failed: {type(exc).__name__}: {exc}"]
    if result.get("truncated"):
        return False, ["candidate result was truncated"]
    rows = result.get("rows")
    if not isinstance(rows, list) or not _rows_equal(rows, case.expected_rows or []):
        expected = case.expected_rows or []
        if (case.category == "denominator" and isinstance(rows, list)
                and _denominator_components_match(rows, expected)):
            return None, ["query returns numerator and denominator; review the answer's percentage"]
        if (isinstance(rows, list) and expected and len(rows) > len(expected)
                and _rows_equal(rows[:len(expected)], expected)):
            return None, ["gold rows are a prefix of a larger result; review the final answer"]
        if isinstance(rows, list) and _numeric_projection_matches(rows, expected):
            return None, ["numeric measures match but group labels differ; review the labels"]
        return False, ["candidate result differs from frozen gold result"]
    return True, []


def _answer_citations(answer: str) -> set[str]:
    return set(re.findall(r"\[query_id:([A-Za-z0-9_-]+)\]", answer))


def _answer_mentions_gold_numbers(answer: str, expected: list[list[Any]]) -> bool:
    """Find evidence for a possible derived answer; this never grants an auto-pass."""
    numbers = [value for row in expected for value in row
               if isinstance(value, (int, float)) and not isinstance(value, bool)]
    if not numbers:
        return False
    normalized = answer.replace(",", "")
    found = re.findall(r"(?<![\w.])-?\d+(?:\.\d+)?(?![\w.])", normalized)
    for value in numbers:
        if isinstance(value, int):
            matched = any(Decimal(token) == value for token in found)
        else:
            matched = any(abs(Decimal(token) - Decimal(str(value))) <= Decimal("0.0100001")
                          for token in found)
        if not matched:
            return False
    return True


def trace_assertions(events: Iterable[dict[str, Any]], *, numeric: bool) -> list[str]:
    """Check event causality and evidence references without judging prose semantics."""
    items = list(events)
    errors: list[str] = []
    if not items:
        return ["empty trace"]
    sequence = [event.get("seq") for event in items]
    if any(not isinstance(value, int) for value in sequence) or sequence != sorted(set(sequence)):
        errors.append("event seq must be strictly increasing")
    kinds = [event.get("kind") for event in items]
    if not kinds or kinds[0] != "run_started":
        errors.append("trace must begin with run_started")
    if kinds.count("run_completed") != 1 or kinds[-1] != "run_completed":
        errors.append("trace must end with exactly one run_completed")
    ready: set[str] = set()
    started: set[str] = set()
    query_ids: set[str] = set()
    for event in items:
        kind = event.get("kind")
        data = event.get("data") or {}
        call_id = data.get("call_id")
        if kind == "tool_call_ready":
            if not call_id or call_id in ready:
                errors.append("tool_call_ready needs a unique call_id")
            ready.add(call_id)
        elif kind == "tool_started":
            if call_id not in ready:
                errors.append(f"tool_started without ready: {call_id}")
            started.add(call_id)
        elif kind in {"tool_finished", "tool_failed"}:
            if call_id not in started:
                errors.append(f"tool result without started: {call_id}")
            if kind == "tool_finished" and data.get("name") == "run_sql":
                query_id = (data.get("result") or {}).get("query_id")
                if query_id:
                    query_ids.add(query_id)
    completions = [event for event in items if event.get("kind") == "run_completed"]
    if completions and numeric:
        answer = (completions[0].get("data") or {}).get("answer", "")
        if not _answer_citations(answer) & query_ids:
            errors.append("final answer cites no completed SQL query_id")
    return errors


def score_trace(case: EvalCase, events: Iterable[dict[str, Any]], query_service: Any,
                *, trial: int = 1) -> TrialScore:
    """Score a stored run; use a cited successful SQL tool result for numeric tasks."""
    items = list(events)
    trace_errors = trace_assertions(items, numeric=case.is_numeric)
    completion = next((e for e in reversed(items) if e.get("kind") == "run_completed"), None)
    answer = ((completion or {}).get("data") or {}).get("answer", "")
    citations = _answer_citations(answer)
    candidates = [
        (e.get("data") or {}).get("result", {}).get("sql")
        for e in items if e.get("kind") == "tool_finished"
        and (e.get("data") or {}).get("name") == "run_sql"
        and (e.get("data") or {}).get("result", {}).get("query_id") in citations
    ]
    sql_correct: bool | None = None
    sql_errors: list[str] = []
    if case.is_numeric:
        scores = [score_prediction(case, sql, query_service) for sql in candidates]
        sql_correct = (True if any(correct is True for correct, _ in scores) else
                       None if any(correct is None for correct, _ in scores) else False)
        if not scores:
            sql_correct = False
            sql_errors.append("no cited SQL result to compare")
        elif sql_correct is None:
            sql_errors.extend(next(notes for correct, notes in scores if correct is None))
        elif sql_correct is False:
            if candidates and _answer_mentions_gold_numbers(answer, case.expected_rows or []):
                sql_correct = None
                sql_errors.append("answer contains gold numbers but cited SQL needs derived/composite review")
            else:
                sql_errors.extend(scores[-1][1])
    model_events = [e for e in items if e.get("kind") in {"model_completed", "model_failed"}]
    input_tokens = sum(int((e.get("data") or {}).get("usage", {}).get("input_tokens", 0))
                       for e in model_events)
    output_tokens = sum(int((e.get("data") or {}).get("usage", {}).get("output_tokens", 0))
                        for e in model_events)
    # Model duration is diagnostic only; end-to-end latency should be injected by run harness.
    errors = trace_errors + sql_errors
    if case.is_numeric:
        # SQL evidence alone cannot establish that the final prose is correct.
        status = "fail" if trace_errors or sql_correct is False else "needs_review"
    else:
        status = "needs_review" if not trace_errors else "fail"
    return TrialScore(
        case_id=case.id, category=case.category, trial=trial, status=status,
        numeric_case=case.is_numeric,
        sql_correct=sql_correct, trace_ok=not trace_errors, errors=errors,
        input_tokens=input_tokens, output_tokens=output_tokens,
        tool_calls=sum(e.get("kind") == "tool_started" for e in items),
        run_id=items[0].get("run_id") if items else None,
    )


def summarize_trials(trials: Iterable[TrialScore]) -> dict[str, Any]:
    """Report measured rates and variance; never count behavior cases as auto-passes."""
    items = list(trials)
    groups: dict[str, list[TrialScore]] = {"all": items}
    for item in items:
        groups.setdefault(item.category, []).append(item)
    report: dict[str, Any] = {}
    for name, group in groups.items():
        auto = [item for item in group if item.sql_correct is not None]
        numeric = [item for item in group if item.numeric_case is True or
                   (item.numeric_case is None and item.sql_correct is not None)]
        latencies = [item.latency_ms for item in group if item.latency_ms is not None]
        report[name] = {
            "trials": len(group),
            "auto_scored": len(auto),
            "numeric_trials": len(numeric),
            "numeric_sql_true": sum(item.sql_correct is True for item in numeric),
            "numeric_sql_false": sum(item.sql_correct is False for item in numeric),
            "numeric_sql_undecided": sum(item.sql_correct is None for item in numeric),
            "behavioral_trials": len(group) - len(numeric),
            "numeric_oracle_coverage": len(auto) / len(numeric) if numeric else None,
            "human_reviewed": sum(item.human_review is not None for item in group),
            "passed": sum(item.status == "pass" for item in group),
            "failed": sum(item.status == "fail" for item in group),
            "needs_review": sum(item.status == "needs_review" for item in group),
            "task_completion_rate_on_decided": (
                sum(item.status == "pass" for item in group) / len(group)
                if group and all(item.status in {"pass", "fail"} for item in group) else None
            ),
            "result_accuracy": (sum(item.sql_correct is True for item in auto) / len(auto)
                                if auto else None),
            "trace_pass_rate": (
                sum(item.trace_ok is True for item in group if item.trace_ok is not None)
                / sum(item.trace_ok is not None for item in group)
                if any(item.trace_ok is not None for item in group) else None
            ),
            "mean_latency_ms": mean(latencies) if latencies else None,
            "latency_stddev_ms": pstdev(latencies) if latencies else None,
            "total_input_tokens": sum(item.input_tokens or 0 for item in group),
            "total_output_tokens": sum(item.output_tokens or 0 for item in group),
            "total_tool_calls": sum(item.tool_calls for item in group),
        }
    return report
