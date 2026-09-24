from __future__ import annotations

import hashlib
import json
from argparse import Namespace

import pytest

from trust_agent.domain import ProviderEvent
from trust_agent.eval.runner import TrialJournal, run_agent_trials, run_baseline_trials
from trust_agent.eval.scoring import TrialScore
from trust_agent.eval.tasks import EvalCase


def _metadata(mode: str = "baseline") -> dict:
    return {"mode": mode, "model_requested": "pinned-model", "suite_sha256": "abc",
            "provider_settings_sha256": "settings",
            "trial_keys": [{"case_id": "T1", "trial": i} for i in (1, 2)]}


def _case() -> EvalCase:
    return EvalCase("T1", "aggregation", "count trips", "SELECT count(*) FROM trips",
                    [[4]], None)


class _Query:
    def query(self, sql: str, row_limit: int = 1000) -> dict:
        return {"rows": [[4]], "truncated": False}


class _Provider:
    def __init__(self) -> None:
        self.calls = 0

    async def stream(self, messages, tools, instructions):
        self.calls += 1
        yield ProviderEvent("text_delta", {"text": json.dumps({
            "sql": "SELECT count(*) FROM trips", "answer": "4"})})
        yield ProviderEvent("completed", {"output": [], "usage": {}})


def test_journal_rejects_mismatched_metadata_duplicate_and_torn_line(tmp_path):
    path = tmp_path / "checkpoint.jsonl"
    journal = TrialJournal(path, _metadata())
    score = TrialScore("T1", "aggregation", 1, "pass")
    prediction = {"case_id": "T1", "trial": 1, "sql": "SELECT 4"}
    journal.append(score, prediction)
    with pytest.raises(ValueError, match="duplicate"):
        journal.append(score, prediction)
    with pytest.raises(ValueError, match="metadata differs"):
        TrialJournal(path, {**_metadata(), "model_requested": "other"}, resume=True)
    with pytest.raises(FileExistsError):
        TrialJournal(path, _metadata())
    path.write_text(path.read_text() + "{broken\n", encoding="utf-8")
    with pytest.raises(ValueError, match="invalid journal JSON"):
        TrialJournal(path, _metadata(), resume=True)


def test_journal_rejects_unterminated_final_record(tmp_path):
    path = tmp_path / "checkpoint.jsonl"
    journal = TrialJournal(path, _metadata())
    journal.append(TrialScore("T1", "aggregation", 1, "pass"),
                   {"case_id": "T1", "trial": 1})
    path.write_text(path.read_text(encoding="utf-8").rstrip("\n"), encoding="utf-8")
    with pytest.raises(ValueError, match="unterminated"):
        TrialJournal(path, _metadata(), resume=True)


@pytest.mark.asyncio
async def test_baseline_resume_skips_finished_trial_and_preserves_order(tmp_path):
    path = tmp_path / "baseline.jsonl"
    journal = TrialJournal(path, _metadata())
    provider = _Provider()
    saved: list[tuple[int, str]] = []

    def stop_after_first(score, prediction):
        journal.append(score, prediction)
        saved.append((score.trial, prediction["sql"]))
        raise RuntimeError("interrupted after durable append")

    with pytest.raises(RuntimeError, match="interrupted"):
        await run_baseline_trials([_case()], provider, _Query(), repeats=2,
                                  on_trial=stop_after_first)
    resumed = TrialJournal(path, _metadata(), resume=True)
    scores, predictions = await run_baseline_trials(
        [_case()], provider, _Query(), repeats=2,
        completed=resumed.baseline_results(), on_trial=resumed.append)
    assert provider.calls == 2
    assert len(resumed.records) == 2
    assert [score.trial for score in scores] == [1, 2]
    assert [prediction["trial"] for prediction in predictions] == [1, 2]
    assert saved == [(1, "SELECT count(*) FROM trips")]


@pytest.mark.asyncio
async def test_agent_resume_skips_finished_trace(tmp_path):
    class Store:
        def events(self, run_id):
            return [
                {"seq": 1, "run_id": run_id, "kind": "run_started", "data": {}},
                {"seq": 2, "run_id": run_id, "kind": "tool_call_ready",
                 "data": {"call_id": "c1"}},
                {"seq": 3, "run_id": run_id, "kind": "tool_started",
                 "data": {"call_id": "c1"}},
                {"seq": 4, "run_id": run_id, "kind": "tool_finished",
                 "data": {"call_id": "c1", "name": "run_sql", "result": {
                     "query_id": "q1", "sql": "SELECT count(*) FROM trips"}}},
                {"seq": 5, "run_id": run_id, "kind": "run_completed",
                 "data": {"answer": "4 [query_id:q1]"}},
            ]

    class Runner:
        def __init__(self):
            self.store = Store()
            self.calls = 0

        async def run(self, question):
            self.calls += 1
            return type("State", (), {"run_id": f"run-{self.calls}"})()

    runner = Runner()
    journal = TrialJournal(tmp_path / "agent.jsonl", _metadata("agent"))

    def stop_after_first(score):
        journal.append(score)
        raise RuntimeError("interrupted")

    with pytest.raises(RuntimeError, match="interrupted"):
        await run_agent_trials([_case()], runner, _Query(), repeats=2,
                               on_trial=stop_after_first)
    resumed = TrialJournal(journal.path, _metadata("agent"), resume=True)
    scores = await run_agent_trials([_case()], runner, _Query(), repeats=2,
                                    completed=resumed.scores(), on_trial=resumed.append)
    assert runner.calls == 2
    assert [score.trial for score in scores] == [1, 2]
    assert [score.run_id for score in scores] == ["run-1", "run-2"]


def test_agent_journal_requires_trace_reference(tmp_path):
    journal = TrialJournal(tmp_path / "agent.jsonl", _metadata("agent"))
    with pytest.raises(ValueError, match="lacks run_id"):
        journal.append(TrialScore("T1", "aggregation", 1, "pass"))


def test_baseline_rescore_uses_persisted_prediction_without_model(tmp_path, monkeypatch):
    from trust_agent.eval import __main__ as cli

    cases_path = tmp_path / "cases.jsonl"
    cases_path.write_text("frozen suite", encoding="utf-8")
    old = TrialScore("T1", "aggregation", 1, "pass", sql_correct=True,
                     latency_ms=123.4, input_tokens=9, output_tokens=5)
    report_path = tmp_path / "report.json"
    report_path.write_text(json.dumps({
        "suite_sha256": hashlib.sha256(cases_path.read_bytes()).hexdigest(),
        "model_requested": "pinned-model", "trials": [old.to_dict()],
        "summary": {}}, ensure_ascii=False), encoding="utf-8")
    predictions_path = tmp_path / "predictions.jsonl"
    predictions_path.write_text(json.dumps({"case_id": "T1", "trial": 1,
                                            "sql": "SELECT count(*) FROM trips",
                                            "answer": "4", "error": None}) + "\n")
    monkeypatch.setattr(cli, "load_cases", lambda _: [_case()])
    monkeypatch.setattr(cli, "QueryService", lambda *_: _Query())
    out = tmp_path / "rescored.json"
    cli._rescore(Namespace(report=str(report_path), predictions=str(predictions_path),
                           state_db=None, cases=str(cases_path), db="unused", out=str(out)))
    rescored = json.loads(out.read_text(encoding="utf-8"))
    trial = rescored["trials"][0]
    assert trial["sql_correct"] is True
    assert trial["status"] == "needs_review"
    assert trial["latency_ms"] == 123.4
    assert (trial["input_tokens"], trial["output_tokens"]) == (9, 5)
