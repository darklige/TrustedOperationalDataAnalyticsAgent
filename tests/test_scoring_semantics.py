from __future__ import annotations

from trust_agent.eval.scoring import (
    TrialScore,
    _rows_equal,
    score_prediction,
    score_trace,
    summarize_trials,
)
from trust_agent.eval.tasks import EvalCase


def test_decimal_strings_from_query_service_match_numeric_gold() -> None:
    gold = [["2025-01", 86.53, 2794953], ["2025-02", 87.61, 2634727]]
    actual = [["2025-01", 2794953, 2418413, "86.53"],
              ["2025-02", 2634727, 2308308, "87.61"]]
    assert _rows_equal(actual, gold)
    assert not _rows_equal([["2025-01", "NaN", 2794953]], gold[:1])


def test_task_completion_rate_waits_until_all_prose_is_reviewed() -> None:
    scores = [TrialScore("a", "aggregation", 1, "needs_review", numeric_case=True,
                         sql_correct=True),
              TrialScore("b", "aggregation", 1, "fail", numeric_case=True,
                         sql_correct=False),
              TrialScore("c", "behavior", 1, "needs_review", numeric_case=False)]
    pending = summarize_trials(scores)["all"]
    assert pending["result_accuracy"] == 0.5
    assert pending["numeric_trials"] == 2
    assert pending["behavioral_trials"] == 1
    assert pending["numeric_oracle_coverage"] == 1.0
    assert pending["task_completion_rate_on_decided"] is None
    scores[0].status = "pass"
    scores[0].human_review = {"reviewer": "tester", "decision": "pass", "reason": "checked"}
    scores[2].status = "fail"
    scores[2].human_review = {"reviewer": "tester", "decision": "fail", "reason": "checked"}
    reviewed = summarize_trials(scores)["all"]
    assert reviewed["task_completion_rate_on_decided"] == 1 / 3


def test_matching_week_measures_with_new_labels_require_review() -> None:
    case = EvalCase("w", "trend", "compare weeks", "SELECT ...",
                    [["2025-02-03", 46884, 33.91],
                     ["2025-02-10", 46044, 36.04]], None)

    class Query:
        def query(self, sql: str, row_limit: int = 1000) -> dict:
            return {"rows": [["Week 1 (Feb 3-9)", "33.91", 46884],
                             ["Week 2 (Feb 10-16)", "36.04", 46044]],
                    "truncated": False}

    correct, notes = score_prediction(case, "SELECT ...", Query())
    assert correct is None
    assert "group labels" in notes[0]


def test_derived_answer_with_cited_raw_ratio_is_reviewed() -> None:
    case = EvalCase("r", "metric_semantics", "percent and sample size", "SELECT ...",
                    [[22.89, 2308273]], None)

    class Query:
        def query(self, sql: str, row_limit: int = 1000) -> dict:
            return {"rows": [[2308273, 0.228948379]], "truncated": False}

    events = [
        {"seq": 1, "run_id": "r1", "kind": "run_started", "data": {}},
        {"seq": 2, "run_id": "r1", "kind": "tool_call_ready", "data": {"call_id": "c1"}},
        {"seq": 3, "run_id": "r1", "kind": "tool_started", "data": {"call_id": "c1"}},
        {"seq": 4, "run_id": "r1", "kind": "tool_finished", "data": {
            "call_id": "c1", "name": "run_sql", "result": {"query_id": "q1", "sql": "SELECT ..."}}},
        {"seq": 5, "run_id": "r1", "kind": "run_completed", "data": {
            "answer": "22.89% across 2,308,273 trips [query_id:q1]"}},
    ]
    scored = score_trace(case, events, Query())
    assert scored.sql_correct is None
    assert scored.status == "needs_review"
    assert "derived/composite" in scored.errors[0]
    events[-1]["data"]["answer"] = "20% across 2,308,273 trips [query_id:q1]"
    assert score_trace(case, events, Query()).status == "fail"
