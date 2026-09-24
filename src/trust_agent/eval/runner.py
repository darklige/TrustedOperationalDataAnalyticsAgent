"""Run repeated agent trials or a one-model-call Text-to-SQL baseline."""

from __future__ import annotations

import json
import os
import re
import time
from collections.abc import Callable, Iterable
from pathlib import Path
from typing import Any

from trust_agent.domain import ProviderStreamError

from .scoring import TrialScore, score_prediction, score_trace
from .tasks import EvalCase


class TrialJournal:
    """Durable JSONL checkpoint, with an immutable invocation header.

    One trial is appended only after its score (and baseline prediction) exists.
    A crash before append may leave an orphan agent trace, but never a false
    completed trial. A torn final line is rejected rather than silently skipped.
    """

    def __init__(self, path: str | Path, metadata: dict[str, Any], *, resume: bool = False):
        self.path = Path(path)
        self.metadata = metadata
        self.records: dict[tuple[str, int], dict[str, Any]] = {}
        expected = [(item["case_id"], item["trial"]) for item in metadata["trial_keys"]]
        if len(expected) != len(set(expected)):
            raise ValueError("journal metadata contains duplicate trial keys")
        self._expected = set(expected)
        if resume:
            if not self.path.is_file():
                raise FileNotFoundError(self.path)
            self._load()
        else:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with self.path.open("x", encoding="utf-8") as stream:
                stream.write(json.dumps({"kind": "header", "metadata": metadata},
                                        ensure_ascii=False, sort_keys=True) + "\n")
                stream.flush()
                os.fsync(stream.fileno())

    def _load(self) -> None:
        with self.path.open(encoding="utf-8") as stream:
            for line_number, line in enumerate(stream, 1):
                if not line.endswith("\n"):
                    raise ValueError(f"unterminated journal line {line_number}")
                try:
                    item = json.loads(line)
                except json.JSONDecodeError as exc:
                    raise ValueError(f"invalid journal JSON on line {line_number}") from exc
                if line_number == 1:
                    if item != {"kind": "header", "metadata": self.metadata}:
                        raise ValueError("journal metadata differs from current suite/model/config")
                    continue
                if item.get("kind") != "trial" or not isinstance(item.get("score"), dict):
                    raise ValueError(f"invalid journal trial on line {line_number}")
                score = item["score"]
                key = (score.get("case_id"), score.get("trial"))
                if key not in self._expected or key in self.records:
                    raise ValueError(f"unexpected or duplicate journal trial on line {line_number}")
                if self.metadata["mode"] == "agent" and not score.get("run_id"):
                    raise ValueError(f"agent journal trial lacks run_id on line {line_number}")
                if self.metadata["mode"] == "baseline":
                    prediction = item.get("prediction")
                    if not isinstance(prediction, dict) or (prediction.get("case_id"),
                            prediction.get("trial")) != key:
                        raise ValueError(f"baseline journal prediction mismatch on line {line_number}")
                self.records[key] = item
        if not self.path.stat().st_size:
            raise ValueError("journal is empty")

    def append(self, score: TrialScore, prediction: dict[str, Any] | None = None) -> None:
        key = (score.case_id, score.trial)
        if key not in self._expected or key in self.records:
            raise ValueError(f"unexpected or duplicate journal trial: {key}")
        if self.metadata["mode"] == "agent" and not score.run_id:
            raise ValueError("agent journal trial lacks run_id")
        if self.metadata["mode"] == "baseline" and (prediction is None or
                (prediction.get("case_id"), prediction.get("trial")) != key):
            raise ValueError("baseline journal prediction mismatch")
        item = {"kind": "trial", "score": score.to_dict()}
        if prediction is not None:
            item["prediction"] = prediction
        with self.path.open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(item, ensure_ascii=False, sort_keys=True) + "\n")
            stream.flush()
            os.fsync(stream.fileno())
        self.records[key] = item

    def scores(self) -> dict[tuple[str, int], TrialScore]:
        return {key: TrialScore(**record["score"]) for key, record in self.records.items()}

    def baseline_results(self) -> dict[tuple[str, int], tuple[TrialScore, dict[str, Any]]]:
        return {key: (TrialScore(**record["score"]), record["prediction"])
                for key, record in self.records.items()}

BASELINE_INSTRUCTIONS = """You write a single read-only DuckDB SELECT query for NYC TLC taxi data.
Return exactly one JSON object with keys `sql` and `answer`. For answerable questions,
put one SQL query in `sql` and a short explanation of the calculation in `answer`.
For questions outside the dataset or impossible to infer, use null for `sql` and
explain the limitation in `answer`. Do not use tools or invent values.
Available tables: trips(pickup_at, dropoff_at, duration_minutes,
pickup_location_id, dropoff_location_id, passenger_count, trip_distance_miles,
fare_amount, total_amount, tip_amount, payment_type, cbd_congestion_fee,
source_month); zones(location_id, borough, zone, service_zone).
The dataset covers Jan-Feb 2025 only. Dates are NYC local civil time.
source_month values are '2025-01' and '2025-02', not English month names.
The trip table includes 1-240 minute trips, 0.1-100 mile trips and known zone IDs.
Trip counts are not unique passenger counts; cash tips are not captured.
"""


def _parse_baseline_text(text: str) -> tuple[str | None, str]:
    cleaned = text.strip()
    if cleaned.startswith("```"):
        cleaned = re.sub(r"^```(?:json)?\s*|\s*```$", "", cleaned).strip()
    payload = json.loads(cleaned)
    if not isinstance(payload, dict) or set(payload) != {"sql", "answer"}:
        raise ValueError("baseline response must be JSON with sql and answer")
    sql, answer = payload["sql"], payload["answer"]
    if sql is not None and (not isinstance(sql, str) or not sql.strip()):
        raise ValueError("sql must be null or a nonempty string")
    if not isinstance(answer, str):
        raise TypeError("answer must be a string")
    return sql, answer


async def run_single_turn_baseline(case: EvalCase, provider: Any, query_service: Any,
                                   *, trial: int = 1) -> tuple[TrialScore, dict[str, Any]]:
    """One model response, then one SQL execution through the production policy.

    The model does not see the SQL result and cannot repair its query. This is an
    intentional one-shot Text-to-SQL baseline, not a second agent implementation.
    """
    started = time.monotonic()
    text_parts: list[str] = []
    output: list[dict[str, Any]] = []
    usage: dict[str, Any] = {}
    actual_model: str | None = None
    completed = False
    error: str | None = None
    sql: str | None = None
    answer = ""
    try:
        async for event in provider.stream(
            [{"role": "user", "content": case.question}], [], BASELINE_INSTRUCTIONS
        ):
            if event.kind == "text_delta":
                text_parts.append(event.data["text"])
            elif event.kind == "tool_ready":
                raise ValueError("baseline provider attempted a tool call")
            elif event.kind == "completed":
                completed = True
                output = event.data.get("output", [])
                usage = event.data.get("usage", {})
                actual_model = event.data.get("model")
        if not completed:
            raise RuntimeError("baseline model stream ended without completion")
        raw_text = "".join(text_parts).strip()
        if not raw_text:
            raw_text = "".join(
                part.get("text", "") for item in output if item.get("type") == "message"
                for part in item.get("content", [])
            )
        sql, answer = _parse_baseline_text(raw_text)
    except Exception as exc:  # noqa: BLE001 - baseline failures are part of the trial report
        if isinstance(exc, ProviderStreamError):
            usage = exc.usage
            actual_model = exc.model
        error = f"{type(exc).__name__}: {exc}"

    sql_correct, score_errors = score_prediction(case, sql, query_service) if error is None else (
        False if case.is_numeric else None, [error]
    )
    errors = score_errors
    # The one-shot model never sees the SQL result. Correct SQL is not evidence
    # that the prose answer includes the right value or caveat.
    status = "fail" if error is not None or sql_correct is False else "needs_review"
    result = TrialScore(
        case_id=case.id, category=case.category, trial=trial, status=status,
        numeric_case=case.is_numeric,
        sql_correct=sql_correct, errors=errors,
        latency_ms=round((time.monotonic() - started) * 1000, 2),
        input_tokens=int(usage.get("input_tokens", 0)),
        output_tokens=int(usage.get("output_tokens", 0)),
        tool_calls=0,
    )
    prediction = {"case_id": case.id, "trial": trial, "sql": sql, "answer": answer,
                  "usage": usage, "model_observed": actual_model, "error": error,
                  "latency_ms": result.latency_ms}
    return result, prediction


async def run_baseline_trials(cases: Iterable[EvalCase], provider: Any, query_service: Any,
                              *, repeats: int = 3,
                              completed: dict[tuple[str, int], tuple[TrialScore, dict[str, Any]]] | None = None,
                              on_trial: Callable[[TrialScore, dict[str, Any]], None] | None = None,
                              ) -> tuple[list[TrialScore], list[dict[str, Any]]]:
    if repeats < 1:
        raise ValueError("repeats must be at least 1")
    scores: list[TrialScore] = []
    predictions: list[dict[str, Any]] = []
    completed = completed or {}
    for case in cases:
        for trial in range(1, repeats + 1):
            key = (case.id, trial)
            if key in completed:
                score, prediction = completed[key]
            else:
                score, prediction = await run_single_turn_baseline(case, provider, query_service,
                                                                    trial=trial)
                if on_trial is not None:
                    on_trial(score, prediction)
            scores.append(score)
            predictions.append(prediction)
    return scores, predictions


async def run_agent_trials(cases: Iterable[EvalCase], runner: Any, query_service: Any,
                           *, repeats: int = 3,
                           completed: dict[tuple[str, int], TrialScore] | None = None,
                           on_trial: Callable[[TrialScore], None] | None = None,
                           ) -> list[TrialScore]:
    """Execute a real AgentRunner repeatedly and grade its persisted events."""
    if repeats < 1:
        raise ValueError("repeats must be at least 1")
    scores: list[TrialScore] = []
    completed = completed or {}
    for case in cases:
        for trial in range(1, repeats + 1):
            key = (case.id, trial)
            if key in completed:
                scores.append(completed[key])
                continue
            started = time.monotonic()
            state = await runner.run(case.question)
            events = runner.store.events(state.run_id)
            score = score_trace(case, events, query_service, trial=trial)
            score.latency_ms = round((time.monotonic() - started) * 1000, 2)
            if on_trial is not None:
                on_trial(score)
            scores.append(score)
    return scores
