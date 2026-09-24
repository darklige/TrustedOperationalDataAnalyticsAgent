import pytest

from trust_agent.eval.review import apply_reviews, rescore_report
from trust_agent.eval.scoring import TrialScore, summarize_trials
from trust_agent.eval.tasks import EvalCase
from trust_agent.store import EventStore


class RankingQuery:
    def query(self, sql, row_limit=1000):
        return {"rows": [["Manhattan", 10], ["Queens", 5]], "truncated": False}


def test_rescore_uses_persisted_trace_without_model_call(tmp_path):
    store = EventStore(tmp_path / "trace.db")
    run_id = "r1"
    store.append(run_id, 0, "run_started", {"question": "top borough"})
    store.append(run_id, 1, "tool_call_ready", {"call_id": "c1"})
    store.append(run_id, 1, "tool_started", {"call_id": "c1", "name": "run_sql"})
    store.append(run_id, 1, "tool_finished", {"call_id": "c1", "name": "run_sql",
                  "result": {"query_id": "q1", "sql": "SELECT ..."}})
    store.append(run_id, 1, "model_completed", {"usage": {"input_tokens": 10,
                                                             "output_tokens": 2}})
    store.append(run_id, 1, "run_completed", {"answer": "Manhattan 10 [query_id:q1]"})
    case = EvalCase("T01", "join", "top borough", "SELECT ...", [["Manhattan", 10]],
                    None, ())
    original = {"model_requested": "test", "trials": [{"case_id": "T01", "trial": 1,
                "run_id": run_id, "latency_ms": 25.0}], "summary": {}}
    result = rescore_report(original, {"T01": case}, store, RankingQuery())
    assert result["trials"][0]["status"] == "needs_review"
    assert result["trials"][0]["latency_ms"] == 25.0
    assert result["trials"][0]["input_tokens"] == 10
    assert result["model_requested"] == "test"


def test_human_review_requires_identity_and_cannot_override_failed_trace():
    score = TrialScore("T01", "join", 1, "needs_review", trace_ok=True,
                       run_id="r1")
    report = {"trials": [score.to_dict()], "summary": summarize_trials([score])}
    review = {"case_id": "T01", "trial": 1, "run_id": "r1", "decision": "pass",
              "reviewer": "analyst", "reason": "Answer names the correct top row and count."}
    checked = apply_reviews(report, [review])
    assert checked["trials"][0]["status"] == "pass"
    assert checked["summary"]["all"]["human_reviewed"] == 1
    assert checked["summary"]["all"]["task_completion_rate_on_decided"] == 1.0
    with pytest.raises(ValueError, match="duplicate review"):
        apply_reviews(report, [review, review])
    blocked = {"trials": [{**score.to_dict(), "trace_ok": False}], "summary": {}}
    with pytest.raises(ValueError, match="not eligible"):
        apply_reviews(blocked, [review])
