from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

from trust_agent.domain import ProviderEvent
from trust_agent.eval import EvalCase, load_cases, score_prediction, score_trace, summarize_trials
from trust_agent.eval.runner import run_agent_trials, run_baseline_trials, run_single_turn_baseline
from trust_agent.eval.scoring import TrialScore, trace_assertions

SUITE = Path(__file__).resolve().parents[1] / "evals" / "gold_cases.jsonl"


class FakeQueryService:
    def __init__(self, rows):
        self.rows = rows
        self.calls = []

    def query(self, sql, row_limit=1000):
        self.calls.append((sql, row_limit))
        if sql == "BAD":
            raise ValueError("blocked by policy")
        return {"rows": self.rows, "truncated": False}


class FakeProvider:
    def __init__(self, text):
        self.text = text
        self.calls = 0

    async def stream(self, messages, tools, instructions):
        self.calls += 1
        assert tools == []
        assert messages[0]["role"] == "user"
        yield ProviderEvent("text_delta", {"text": self.text[:7]})
        yield ProviderEvent("text_delta", {"text": self.text[7:]})
        yield ProviderEvent("completed", {"output": [], "usage": {
            "input_tokens": 30, "output_tokens": 12,
        }})


def case() -> EvalCase:
    return EvalCase("T01", "aggregation", "How many?", "SELECT count(*) FROM trips",
                    [["2025-01", 10], ["2025-02", 12]], None)


def trace(sql="SELECT count(*) FROM trips", *, answer="January [query_id:q1]"):
    return [
        {"seq": 1, "run_id": "r1", "kind": "run_started", "data": {}},
        {"seq": 2, "run_id": "r1", "kind": "tool_call_ready", "data": {"call_id": "c1"}},
        {"seq": 3, "run_id": "r1", "kind": "tool_started", "data": {"call_id": "c1"}},
        {"seq": 4, "run_id": "r1", "kind": "tool_finished", "data": {"call_id": "c1",
         "name": "run_sql", "result": {"query_id": "q1", "sql": sql}}},
        {"seq": 5, "run_id": "r1", "kind": "model_completed", "data": {
            "usage": {"input_tokens": 30, "output_tokens": 10}}},
        {"seq": 6, "run_id": "r1", "kind": "run_completed", "data": {"answer": answer}},
    ]


def test_frozen_suite_loads_and_rejects_duplicate(tmp_path):
    cases = load_cases(SUITE)
    assert len(cases) == 12
    assert sum(item.is_numeric for item in cases) == 9
    duplicate = tmp_path / "duplicate.jsonl"
    duplicate.write_text(json.dumps({"id": "x", "category": "a", "question": "q",
                                     "expected_behavior": "review"}) + "\n" +
                         json.dumps({"id": "x", "category": "a", "question": "q2",
                                     "expected_behavior": "review"}))
    with pytest.raises(ValueError, match="duplicate case id"):
        load_cases(duplicate)


def test_result_grader_accepts_row_reordering_and_extra_column():
    service = FakeQueryService([[99, "2025-02", 12], [88, "2025-01", 10]])
    correct, errors = score_prediction(case(), "SELECT ...", service)
    assert correct and not errors
    assert service.calls == [("SELECT ...", 1000)]


def test_result_grader_rejects_wrong_count_and_failed_sql():
    service = FakeQueryService([["2025-01", 10], ["2025-02", 11]])
    assert score_prediction(case(), "SELECT ...", service)[0] is False
    assert score_prediction(case(), "BAD", service)[0] is False


def test_trace_causality_and_evidence():
    service = FakeQueryService([["2025-01", 10], ["2025-02", 12]])
    scored = score_trace(case(), trace(), service)
    assert scored.status == "pass"
    assert scored.input_tokens == 30 and scored.output_tokens == 10
    assert scored.tool_calls == 1
    uncited = score_trace(case(), trace(answer="January without a source"), service)
    assert uncited.status == "fail"
    assert any("query_id" in error for error in uncited.errors)
    malformed = trace()
    malformed[1], malformed[2] = malformed[2], malformed[1]
    assert any("without ready" in error for error in trace_assertions(malformed, numeric=True))


def test_behavioral_case_is_not_auto_passed():
    behavioral = next(item for item in load_cases(SUITE) if item.id == "Q10")
    events = [
        {"seq": 1, "run_id": "r2", "kind": "run_started", "data": {}},
        {"seq": 2, "run_id": "r2", "kind": "run_completed", "data": {
            "answer": "I cannot verify March with this dataset"}},
    ]
    scored = score_trace(behavioral, events, FakeQueryService([]))
    assert scored.status == "needs_review"
    report = summarize_trials([scored])
    assert report["all"]["passed"] == 0
    assert report["all"]["needs_review"] == 1
    assert report["all"]["result_accuracy"] is None


def test_numeric_required_claim_needs_manual_review():
    with_claim = EvalCase("T02", "metric_semantics", "What is the rate?", "SELECT 22.89",
                          [[22.89]], None, ("cash tips are unavailable",))
    events = trace(sql="SELECT 22.89")
    score = score_trace(with_claim, events, FakeQueryService([[22.89]]))
    assert score.sql_correct is True
    assert score.status == "needs_review"


@pytest.mark.asyncio
async def test_single_turn_baseline_and_repeats():
    provider = FakeProvider(json.dumps({"sql": "SELECT ...", "answer": "10 and 12"}))
    service = FakeQueryService([["2025-01", 10], ["2025-02", 12]])
    score, prediction = await run_single_turn_baseline(case(), provider, service)
    assert score.status == "pass"
    assert prediction["sql"] == "SELECT ..."
    assert score.input_tokens == 30
    scores, predictions = await run_baseline_trials([case()], provider, service, repeats=3)
    assert provider.calls == 4
    assert len(scores) == len(predictions) == 3
    assert [item.trial for item in scores] == [1, 2, 3]


@pytest.mark.asyncio
async def test_baseline_malformed_response_is_scored_failure():
    provider = FakeProvider("not JSON")
    score, prediction = await run_single_turn_baseline(case(), provider,
                                                        FakeQueryService([]))
    assert score.status == "fail"
    assert prediction["error"] is not None


@pytest.mark.asyncio
async def test_agent_trials_repeat_and_use_persisted_trace():
    class Store:
        def events(self, run_id):
            events = trace()
            for event in events:
                event["run_id"] = run_id
            return events

    class Runner:
        def __init__(self):
            self.store = Store()
            self.calls = 0

        async def run(self, question):
            self.calls += 1
            assert question == "How many?"
            return type("State", (), {"run_id": f"run-{self.calls}"})()

    runner = Runner()
    scores = await run_agent_trials([case()], runner,
                                    FakeQueryService([["2025-01", 10], ["2025-02", 12]]),
                                    repeats=2)
    assert runner.calls == 2
    assert [item.run_id for item in scores] == ["run-1", "run-2"]
    assert all(item.status == "pass" for item in scores)
    assert all(item.latency_ms is not None for item in scores)


def test_repeated_report_has_latency_variance():
    scores = [TrialScore("x", "aggregation", 1, "pass", sql_correct=True,
                         trace_ok=True, latency_ms=100),
              TrialScore("x", "aggregation", 2, "fail", sql_correct=False,
                         trace_ok=False, latency_ms=200)]
    report = summarize_trials(scores)["all"]
    assert report["result_accuracy"] == 0.5
    assert report["mean_latency_ms"] == 150
    assert report["latency_stddev_ms"] == 50
    assert report["trace_pass_rate"] == 0.5


def test_agent_cli_dispatches_requested_trials(monkeypatch):
    from trust_agent.eval import __main__ as cli

    captured = {}

    async def fake_run(args):
        captured.update(vars(args))

    monkeypatch.setattr(cli, "_run_agent", fake_run)
    monkeypatch.setattr(sys, "argv", ["eval", "agent", "--model", "test-model",
                                  "--repeats", "2", "--limit", "3", "--out", "out.json"])
    cli.main()
    assert captured["model"] == "test-model"
    assert captured["repeats"] == 2
    assert captured["limit"] == 3
    assert captured["out"] == "out.json"
