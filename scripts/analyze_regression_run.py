"""Summarize process completion, citation integrity, usage and latency from real traces.

This does not grade the semantic quality of final prose. Behavioral and numeric
answers still require the frozen rubric and, where necessary, human review.
"""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from statistics import mean

from trust_agent.store import EventStore


def analyze(report: dict, store: EventStore) -> dict:
    trials = []
    first_event_latencies = []
    first_text_latencies = []
    for score in report["trials"]:
        events = store.events(score["run_id"])
        completions = [event for event in events if event["kind"] == "run_completed"]
        completion = completions[-1]["data"] if completions else {}
        answer = completion.get("answer", "")
        citations = set(re.findall(r"\[query_id:([A-Za-z0-9_-]+)\]", answer))
        query_ids = {event["data"]["result"]["query_id"] for event in events
                     if event["kind"] == "tool_finished"
                     and event["data"].get("name") == "run_sql"
                     and event["data"].get("result", {}).get("query_id")}
        for event in events:
            if event["kind"] == "model_completed" and event["data"].get("first_event_ms") is not None:
                first_event_latencies.append(event["data"]["first_event_ms"])
            if event["kind"] == "model_completed" and event["data"].get("first_text_ms") is not None:
                first_text_latencies.append(event["data"]["first_text_ms"])
        trials.append({
            "case_id": score["case_id"],
            "run_completed": len(completions) == 1 and bool(events)
                             and events[-1]["kind"] == "run_completed",
            "numeric_case": score.get("numeric_case"),
            "score_status": score["status"],
            "trace_ok": score["trace_ok"],
            "answer_type": completion.get("answer_type"),
            "cited_query_ids": len(citations),
            "citation_integrity": citations <= query_ids,
            "policy_refusal": any(event["kind"] == "policy_refusal" for event in events),
            "text_discarded": sum(event["kind"] == "text_discarded" for event in events),
            "context_layered": sum(event["kind"] == "context_layered" for event in events),
            "context_compacted": sum(event["kind"] == "context_compacted" for event in events),
            "input_tokens": score["input_tokens"] or 0,
            "output_tokens": score["output_tokens"] or 0,
            "latency_ms": score["latency_ms"],
        })
    n = len(trials)
    latencies = [t["latency_ms"] for t in trials if t["latency_ms"] is not None]
    numeric_completed = [t for t in trials if t["numeric_case"] and t["run_completed"]]
    return {
        "suite_sha256": report.get("suite_sha256"),
        "source_sha256": report.get("source_sha256"),
        "scorer_version": report.get("scorer_version"),
        "model_observed": report.get("models_observed"),
        "trials": trials,
        "summary": {
            "trials": n,
            "run_completion_rate": sum(t["run_completed"] for t in trials) / n if n else None,
            "trace_pass_rate": sum(t["trace_ok"] is True for t in trials) / n if n else None,
            "citation_integrity_rate_on_completed": (
                sum(t["citation_integrity"] for t in trials if t["run_completed"])
                / sum(t["run_completed"] for t in trials)
                if any(t["run_completed"] for t in trials) else None),
            "numeric_citation_coverage": (
                sum(t["cited_query_ids"] > 0 for t in numeric_completed) / len(numeric_completed)
                if numeric_completed else None),
            "policy_refusals": sum(t["policy_refusal"] for t in trials),
            "text_discarded": sum(t["text_discarded"] for t in trials),
            "context_layered": sum(t["context_layered"] for t in trials),
            "context_compacted": sum(t["context_compacted"] for t in trials),
            "total_input_tokens": sum(t["input_tokens"] for t in trials),
            "total_output_tokens": sum(t["output_tokens"] for t in trials),
            "mean_latency_ms": mean(latencies) if latencies else None,
            "mean_model_first_event_ms": (mean(first_event_latencies)
                                          if first_event_latencies else None),
            "mean_model_first_text_ms": (mean(first_text_latencies)
                                         if first_text_latencies else None),
            "task_completion_rate": None,
            "task_completion_note": "Final prose and behavioral rubrics require human review.",
        },
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--report", required=True)
    parser.add_argument("--state-db", required=True)
    parser.add_argument("--out")
    args = parser.parse_args()
    result = analyze(json.loads(Path(args.report).read_text(encoding="utf-8")),
                     EventStore(args.state_db))
    rendered = json.dumps(result, ensure_ascii=False, indent=2) + "\n"
    if args.out:
        Path(args.out).write_text(rendered, encoding="utf-8")
    print(rendered, end="")


if __name__ == "__main__":
    main()
