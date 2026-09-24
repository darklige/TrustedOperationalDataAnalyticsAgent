"""Export pending human eval decisions with the evidence needed to make them.

Example:
  python scripts/export_review_queue.py --cases evals/gold_cases.jsonl \
    --report runtime/agent_report.json --state-db runtime/agent.sqlite3 \
    --out runtime/review_queue.md --format markdown

The nested ``review_template`` is deliberately incomplete. After reviewing a
trial, copy and fill that object into a separate JSONL file for
``python -m trust_agent.eval review``. This script never makes a decision.
"""

from __future__ import annotations

import argparse
import hashlib
import html
import json
import os
import re
import tempfile
from pathlib import Path
from typing import Any

from trust_agent.eval.tasks import EvalCase, load_cases
from trust_agent.store import EventStore


def _read_json(path: str | Path) -> dict[str, Any]:
    value = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise TypeError(f"expected a JSON object: {path}")
    return value


def _trial_key(raw: dict[str, Any], *, label: str) -> tuple[str, int]:
    case_id, trial = raw.get("case_id"), raw.get("trial")
    if not isinstance(case_id, str) or not case_id or type(trial) is not int or trial < 1:
        raise ValueError(f"{label} needs a nonempty case_id and positive integer trial")
    return case_id, trial


def _load_predictions(path: str | Path) -> dict[tuple[str, int], dict[str, Any]]:
    result: dict[tuple[str, int], dict[str, Any]] = {}
    for number, line in enumerate(Path(path).read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        raw = json.loads(line)
        if not isinstance(raw, dict):
            raise TypeError(f"prediction line {number} is not an object")
        key = _trial_key(raw, label=f"prediction line {number}")
        if key in result:
            raise ValueError(f"duplicate prediction identity: {key}")
        result[key] = raw
    return result


def _agent_evidence(store: EventStore, run_id: str) -> dict[str, Any]:
    events = store.events(run_id)
    if not events:
        raise ValueError(f"missing trace for run_id {run_id}")
    if any(event.get("run_id") != run_id for event in events):
        raise ValueError(f"trace run_id mismatch for {run_id}")
    completions = [event for event in events if event["kind"] == "run_completed"]
    if len(completions) != 1:
        raise ValueError(f"trace must contain one final answer for run_id {run_id}")
    answer = completions[0]["data"].get("answer")
    if not isinstance(answer, str):
        raise TypeError(f"trace final answer is missing for run_id {run_id}")
    cited = set(re.findall(r"\[query_id:([A-Za-z0-9_-]+)\]", answer))
    queries = []
    for event in events:
        if event["kind"] != "tool_finished" or event["data"].get("name") != "run_sql":
            continue
        data = event["data"]
        result = data.get("result") or {}
        # Whitelist useful query evidence; unrelated tool output may contain
        # credentials or private metadata and is not needed for this review.
        queries.append({
            "query_id": result.get("query_id"),
            "cited_in_answer": result.get("query_id") in cited,
            "call_id": data.get("call_id"),
            "sql": result.get("sql"),
            "columns": result.get("columns"),
            "rows": result.get("rows"),
            "row_count": result.get("row_count"),
            "truncated": result.get("truncated"),
            "dataset_version": result.get("dataset_version"),
            "result_sha256": result.get("result_sha256"),
        })
    return {"answer": answer, "cited_query_ids": sorted(cited), "queries": queries,
            "prediction_error": None, "evidence_available": True}


def _baseline_evidence(prediction: dict[str, Any]) -> dict[str, Any]:
    answer = prediction.get("answer")
    sql = prediction.get("sql")
    if answer is not None and not isinstance(answer, str):
        raise ValueError("baseline prediction answer must be a string or null")
    if sql is not None and not isinstance(sql, str):
        raise ValueError("baseline prediction SQL must be a string or null")
    return {"answer": answer, "cited_query_ids": [],
            "queries": ([{"query_id": None, "cited_in_answer": False,
                          "sql": sql, "result_unavailable": True}] if sql else []),
            "prediction_error": prediction.get("error"), "evidence_available": True}


def build_review_queue(cases_path: str | Path, report_path: str | Path, *,
                       state_db: str | Path | None = None,
                       predictions_path: str | Path | None = None) -> list[dict[str, Any]]:
    """Validate identities and return only still-pending decisions."""
    if state_db and predictions_path:
        raise ValueError("provide --state-db or --predictions, not both")
    cases_file = Path(cases_path)
    cases: dict[str, EvalCase] = {case.id: case for case in load_cases(cases_file)}
    report = _read_json(report_path)
    suite_sha = hashlib.sha256(cases_file.read_bytes()).hexdigest()
    if report.get("suite_sha256") != suite_sha:
        raise ValueError("report suite_sha256 differs from the supplied cases file")
    trials = report.get("trials")
    if not isinstance(trials, list):
        raise TypeError("report trials must be a list")
    identities: set[tuple[str, int]] = set()
    run_ids: set[str] = set()
    for index, score in enumerate(trials, 1):
        if not isinstance(score, dict):
            raise TypeError(f"report trial {index} is not an object")
        key = _trial_key(score, label=f"report trial {index}")
        if key in identities:
            raise ValueError(f"duplicate report trial identity: {key}")
        if key[0] not in cases:
            raise ValueError(f"report references unknown case: {key[0]}")
        identities.add(key)
        run_id = score.get("run_id")
        if run_id is not None:
            if not isinstance(run_id, str) or not run_id:
                raise ValueError(f"invalid run_id for {key}")
            if run_id in run_ids:
                raise ValueError(f"duplicate report run_id: {run_id}")
            run_ids.add(run_id)
    suite_size = report.get("suite_size")
    if suite_size is not None and (type(suite_size) is not int or suite_size < 0 or
                                   suite_size != len({case_id for case_id, _ in identities})):
        raise ValueError("report suite_size differs from distinct trial cases")
    if state_db:
        if not Path(state_db).is_file():
            raise FileNotFoundError(state_db)
        if any(not score.get("run_id") for score in trials):
            raise ValueError("--state-db requires run_id on every report trial")
    if predictions_path and any(score.get("run_id") for score in trials):
        raise ValueError("--predictions is for baseline trials without run_id")
    predictions = _load_predictions(predictions_path) if predictions_path else None
    if predictions is not None:
        missing, extra = identities - predictions.keys(), predictions.keys() - identities
        if missing or extra:
            raise ValueError(f"prediction identities differ from report: missing={sorted(missing)}, "
                             f"extra={sorted(extra)}")
    store = EventStore(state_db) if state_db else None

    queue = []
    for score in trials:
        if score.get("status") != "needs_review":
            continue
        key = _trial_key(score, label="review trial")
        case = cases[key[0]]
        run_id = score.get("run_id")
        if store is not None:
            evidence = _agent_evidence(store, run_id)
        elif predictions is not None:
            evidence = _baseline_evidence(predictions[key])
        else:
            evidence = {"answer": None, "cited_query_ids": [], "queries": [],
                        "prediction_error": None, "evidence_available": False,
                        "evidence_note": "Supply --state-db or --predictions to review the final answer."}
        queue.append({
            "case_id": case.id, "trial": key[1], "run_id": run_id,
            "category": case.category, "question": case.question,
            "gold_rows": case.expected_rows, "gold_sql": case.gold_sql,
            "expected_behavior": case.expected_behavior,
            "required_claims": list(case.required_claims),
            "status": score["status"], "sql_correct": score.get("sql_correct"),
            "trace_ok": score.get("trace_ok"), "errors": score.get("errors", []),
            **evidence,
            "review_template": {"case_id": case.id, "trial": key[1],
                                "run_id": run_id, "decision": None,
                                "reviewer": "", "reason": ""},
        })
    return queue


def _pre(value: Any) -> str:
    rendered = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False, indent=2)
    return f"<pre>{html.escape(rendered)}</pre>"


def render_markdown(queue: list[dict[str, Any]]) -> str:
    lines = ["# Human review queue", "", f"Pending trials: {len(queue)}", "",
             ("Review each answer against the frozen gold and query evidence. The template "
              "is intentionally undecided; fill it only after manual review."), ""]
    for item in queue:
        lines += [f"## {html.escape(item['case_id'])} · trial {item['trial']}", "",
                  f"- Run ID: `{html.escape(str(item['run_id']))}`",
                  f"- Status: `{html.escape(item['status'])}`",
                  f"- SQL correct: `{item['sql_correct']}`; trace OK: `{item['trace_ok']}`",
                  f"- Scorer notes: {html.escape('; '.join(item['errors']) or 'none')}", "",
                  "### Question", "", _pre(item["question"]), ""]
        if item["gold_rows"] is not None:
            lines += ["### Frozen gold rows", "", _pre(item["gold_rows"]), "",
                      "### Gold SQL", "", _pre(item["gold_sql"]), ""]
        else:
            lines += ["### Expected behavior", "", _pre(item["expected_behavior"]), "",
                      "### Required claims", "", _pre(item["required_claims"]), ""]
        lines += ["### Model final answer", "", _pre(item["answer"]), ""]
        if not item["evidence_available"]:
            lines += [html.escape(item["evidence_note"]), ""]
        if item["prediction_error"]:
            lines += ["### Prediction error", "", _pre(item["prediction_error"]), ""]
        if item["queries"]:
            lines += ["### SQL and query evidence", "", _pre(item["queries"]), ""]
        lines += ["### Review template", "", _pre(item["review_template"]), ""]
    return "\n".join(lines).rstrip() + "\n"


def write_queue(path: str | Path, queue: list[dict[str, Any]], *, format: str) -> None:
    if format == "markdown":
        rendered = render_markdown(queue)
    elif format == "jsonl":
        rendered = "".join(json.dumps(item, ensure_ascii=False) + "\n" for item in queue)
    else:
        raise ValueError("format must be markdown or jsonl")
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix=f".{target.name}.", dir=target.parent)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            stream.write(rendered)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, target)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cases", required=True, help="frozen eval suite JSONL")
    parser.add_argument("--report", required=True, help="scored report JSON")
    source = parser.add_mutually_exclusive_group()
    source.add_argument("--state-db", help="Agent SQLite trace store")
    source.add_argument("--predictions", help="baseline predictions JSONL")
    parser.add_argument("--out", required=True, help="queue output path")
    parser.add_argument("--format", choices=("markdown", "jsonl"), default="markdown")
    args = parser.parse_args()
    queue = build_review_queue(args.cases, args.report, state_db=args.state_db,
                               predictions_path=args.predictions)
    write_queue(args.out, queue, format=args.format)
    print(f"Exported {len(queue)} pending trials to {args.out}")


if __name__ == "__main__":
    main()
