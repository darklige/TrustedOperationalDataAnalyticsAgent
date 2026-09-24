"""Regrade persisted traces and apply explicit human rubric decisions."""

from __future__ import annotations

from typing import Any

from trust_agent.store import EventStore

from .scoring import TrialScore, score_trace, summarize_trials
from .tasks import EvalCase

SCORER_VERSION = "2026-09-24-v7"


def rescore_report(report: dict[str, Any], cases: dict[str, EvalCase],
                   store: EventStore, query_service: Any) -> dict[str, Any]:
    scores: list[TrialScore] = []
    for original in report.get("trials", []):
        case_id, run_id = original["case_id"], original.get("run_id")
        if case_id not in cases or not run_id:
            raise ValueError(f"missing case or run_id for {case_id}")
        events = store.events(run_id)
        if not events:
            raise ValueError(f"no persisted events for {run_id}")
        score = score_trace(cases[case_id], events, query_service,
                            trial=int(original["trial"]))
        score.latency_ms = original.get("latency_ms")
        scores.append(score)
    return {**{key: value for key, value in report.items()
               if key not in {"trials", "summary"}},
            "scorer_version": SCORER_VERSION,
            "trials": [score.to_dict() for score in scores],
            "summary": summarize_trials(scores)}


def apply_reviews(report: dict[str, Any], reviews: list[dict[str, str]]) -> dict[str, Any]:
    scores = [TrialScore(**trial) for trial in report.get("trials", [])]
    by_key = {(score.case_id, score.trial, score.run_id): score for score in scores}
    if len(by_key) != len(scores):
        raise ValueError("report contains duplicate trial identities")
    seen: set[tuple[str, int, str | None]] = set()
    for review in reviews:
        key = (review["case_id"], int(review["trial"]), review.get("run_id"))
        if key in seen:
            raise ValueError(f"duplicate review for {key}")
        seen.add(key)
        score = by_key.get(key)
        if score is None:
            raise ValueError(f"review does not match a report trial: {key}")
        if score.status != "needs_review" or score.trace_ok is False:
            raise ValueError(f"trial is not eligible for human review: {key}")
        decision = review.get("decision")
        reviewer = review.get("reviewer", "").strip()
        reason = review.get("reason", "").strip()
        if decision not in {"pass", "fail"} or not reviewer or not reason:
            raise ValueError("review requires pass/fail, reviewer and nonempty reason")
        score.status = decision
        score.human_review = {"reviewer": reviewer, "decision": decision,
                              "reason": reason}
    return {**report, "trials": [score.to_dict() for score in scores],
            "summary": summarize_trials(scores)}
