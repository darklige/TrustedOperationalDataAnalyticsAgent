"""Run repeated agent trials or a one-model-call Text-to-SQL baseline."""

from __future__ import annotations

import json
import re
import time
from collections.abc import Iterable
from typing import Any

from .scoring import TrialScore, score_prediction, score_trace
from .tasks import EvalCase

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
        error = f"{type(exc).__name__}: {exc}"

    sql_correct, score_errors = score_prediction(case, sql, query_service) if error is None else (
        False if case.is_numeric else None, [error]
    )
    errors = score_errors
    if case.is_numeric:
        status = "fail" if not sql_correct else (
            "needs_review" if case.required_claims else "pass"
        )
    else:
        status = "needs_review" if error is None else "fail"
    result = TrialScore(
        case_id=case.id, category=case.category, trial=trial, status=status,
        sql_correct=sql_correct, errors=errors,
        latency_ms=round((time.monotonic() - started) * 1000, 2),
        input_tokens=int(usage.get("input_tokens", 0)),
        output_tokens=int(usage.get("output_tokens", 0)),
        tool_calls=0,
    )
    prediction = {"case_id": case.id, "trial": trial, "sql": sql, "answer": answer,
                  "usage": usage, "error": error}
    return result, prediction


async def run_baseline_trials(cases: Iterable[EvalCase], provider: Any, query_service: Any,
                              *, repeats: int = 3) -> tuple[list[TrialScore], list[dict[str, Any]]]:
    if repeats < 1:
        raise ValueError("repeats must be at least 1")
    scores: list[TrialScore] = []
    predictions: list[dict[str, Any]] = []
    for case in cases:
        for trial in range(1, repeats + 1):
            score, prediction = await run_single_turn_baseline(case, provider, query_service,
                                                                trial=trial)
            scores.append(score)
            predictions.append(prediction)
    return scores, predictions


async def run_agent_trials(cases: Iterable[EvalCase], runner: Any, query_service: Any,
                           *, repeats: int = 3) -> list[TrialScore]:
    """Execute a real AgentRunner repeatedly and grade its persisted events."""
    if repeats < 1:
        raise ValueError("repeats must be at least 1")
    scores: list[TrialScore] = []
    for case in cases:
        for trial in range(1, repeats + 1):
            started = time.monotonic()
            state = await runner.run(case.question)
            events = runner.store.events(state.run_id)
            score = score_trace(case, events, query_service, trial=trial)
            score.latency_ms = round((time.monotonic() - started) * 1000, 2)
            scores.append(score)
    return scores
