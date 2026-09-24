import hashlib
import json
import runpy
from pathlib import Path

import pytest

from trust_agent.store import EventStore

_script = runpy.run_path(str(Path(__file__).resolve().parents[1] / "scripts" /
                             "export_review_queue.py"))
build_review_queue = _script["build_review_queue"]
render_markdown = _script["render_markdown"]
write_queue = _script["write_queue"]


def _fixture(tmp_path, *, agent=True):
    cases = tmp_path / "cases.jsonl"
    cases.write_text("\n".join(json.dumps(row) for row in [
        {"id": "Q01", "category": "join", "question": "Which borough?",
         "gold_sql": "SELECT borough, trips FROM gold", "expected_rows": [["Manhattan", 10]]},
        {"id": "Q02", "category": "out_of_scope", "question": "What about March?",
         "expected_behavior": "Say March is not covered",
         "required_claims": ["Do not claim March had zero rides"]},
    ]) + "\n", encoding="utf-8")
    report = tmp_path / "report.json"
    trials = [
        {"case_id": "Q01", "trial": 1, "run_id": "r1" if agent else None,
         "status": "needs_review", "trace_ok": True, "sql_correct": None,
         "errors": ["review top row"]},
        {"case_id": "Q02", "trial": 1, "run_id": "r2" if agent else None,
         "status": "pass", "trace_ok": True, "errors": []},
    ]
    report.write_text(json.dumps({"suite_sha256": hashlib.sha256(cases.read_bytes()).hexdigest(),
                                  "trials": trials}), encoding="utf-8")
    return cases, report


def test_agent_queue_includes_only_pending_trial_and_cited_query_evidence(tmp_path):
    cases, report = _fixture(tmp_path)
    store = EventStore(tmp_path / "traces.sqlite3")
    store.append("r1", 0, "run_started", {"question": "Which borough?"})
    store.append("r1", 1, "tool_finished", {"name": "run_sql", "call_id": "c1",
                 "result": {"query_id": "q1", "sql": "SELECT borough, trips FROM x",
                            "columns": ["borough", "trips"],
                            "rows": [["Manhattan", 10]], "row_count": 1,
                            "result_sha256": "abc", "api_key": "should-not-export"}})
    store.append("r1", 1, "run_completed",
                 {"answer": "Manhattan had 10. [query_id:q1]"})
    queue = build_review_queue(cases, report, state_db=store.path)
    assert len(queue) == 1
    item = queue[0]
    assert item["answer"] == "Manhattan had 10. [query_id:q1]"
    assert item["gold_rows"] == [["Manhattan", 10]]
    assert item["queries"][0]["cited_in_answer"] is True
    assert item["queries"][0]["rows"] == [["Manhattan", 10]]
    assert "should-not-export" not in json.dumps(item)
    assert item["review_template"] == {"case_id": "Q01", "trial": 1, "run_id": "r1",
                                       "decision": None, "reviewer": "", "reason": ""}
    markdown = render_markdown(queue)
    assert "Manhattan had 10" in markdown
    assert "Q02" not in markdown
    output = tmp_path / "queue.jsonl"
    write_queue(output, queue, format="jsonl")
    assert json.loads(output.read_text(encoding="utf-8")) == item


def test_baseline_queue_links_answer_and_sql_without_inventing_query_result(tmp_path):
    cases, report = _fixture(tmp_path, agent=False)
    predictions = tmp_path / "predictions.jsonl"
    predictions.write_text("\n".join(json.dumps(row) for row in [
        {"case_id": "Q01", "trial": 1, "answer": "Use this SQL", "sql": "SELECT 1"},
        {"case_id": "Q02", "trial": 1, "answer": "Not covered", "sql": None},
    ]) + "\n", encoding="utf-8")
    item = build_review_queue(cases, report, predictions_path=predictions)[0]
    assert item["answer"] == "Use this SQL"
    assert item["queries"] == [{"query_id": None, "cited_in_answer": False,
                                "sql": "SELECT 1", "result_unavailable": True}]
    assert item["run_id"] is None
    assert item["review_template"]["decision"] is None


def test_queue_without_evidence_source_marks_missing_answer(tmp_path):
    cases, report = _fixture(tmp_path)
    item = build_review_queue(cases, report)[0]
    assert item["answer"] is None
    assert item["evidence_available"] is False
    assert "--state-db" in item["evidence_note"]


def test_suite_hash_duplicate_and_unknown_identities_are_rejected(tmp_path):
    cases, report = _fixture(tmp_path)
    body = json.loads(report.read_text(encoding="utf-8"))
    body["suite_sha256"] = "wrong"
    report.write_text(json.dumps(body), encoding="utf-8")
    with pytest.raises(ValueError, match="suite_sha256"):
        build_review_queue(cases, report)
    body["suite_sha256"] = hashlib.sha256(cases.read_bytes()).hexdigest()
    body["trials"].append(dict(body["trials"][0]))
    report.write_text(json.dumps(body), encoding="utf-8")
    with pytest.raises(ValueError, match="duplicate report trial"):
        build_review_queue(cases, report)
    body["trials"].pop()
    body["trials"][0]["case_id"] = "missing"
    report.write_text(json.dumps(body), encoding="utf-8")
    with pytest.raises(ValueError, match="unknown case"):
        build_review_queue(cases, report)


def test_missing_and_duplicate_prediction_identities_are_rejected(tmp_path):
    cases, report = _fixture(tmp_path, agent=False)
    predictions = tmp_path / "predictions.jsonl"
    row = {"case_id": "Q01", "trial": 1, "answer": "one", "sql": "SELECT 1"}
    predictions.write_text(json.dumps(row) + "\n", encoding="utf-8")
    with pytest.raises(ValueError, match="prediction identities differ"):
        build_review_queue(cases, report, predictions_path=predictions)
    predictions.write_text(json.dumps(row) + "\n" + json.dumps(row) + "\n",
                           encoding="utf-8")
    with pytest.raises(ValueError, match="duplicate prediction"):
        build_review_queue(cases, report, predictions_path=predictions)


def test_missing_agent_trace_is_rejected(tmp_path):
    cases, report = _fixture(tmp_path)
    store = EventStore(tmp_path / "empty.sqlite3")
    with pytest.raises(ValueError, match="missing trace"):
        build_review_queue(cases, report, state_db=store.path)


def test_report_suite_size_detects_missing_trial_case(tmp_path):
    cases, report = _fixture(tmp_path)
    body = json.loads(report.read_text(encoding="utf-8"))
    body["suite_size"] = 2
    body["trials"].pop()
    report.write_text(json.dumps(body), encoding="utf-8")
    with pytest.raises(ValueError, match="suite_size"):
        build_review_queue(cases, report)
