"""CLI: python -m trust_agent.eval score|baseline|agent ..."""

from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path
from typing import Any

from trust_agent.sql import QueryService

from .runner import run_agent_trials, run_baseline_trials
from .scoring import TrialScore, score_prediction, score_trace, summarize_trials
from .tasks import load_cases


def _save_json(path: str | None, payload: Any) -> None:
    rendered = json.dumps(payload, ensure_ascii=False, indent=2)
    if path:
        Path(path).write_text(rendered + "\n", encoding="utf-8")
    print(rendered)


def _score_predictions(args: argparse.Namespace) -> None:
    cases = {case.id: case for case in load_cases(args.cases)}
    service = QueryService(args.db, ("trips", "zones"))
    scores: list[TrialScore] = []
    for number, line in enumerate(Path(args.predictions).read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        prediction = json.loads(line)
        case_id = prediction["case_id"]
        if case_id not in cases:
            raise ValueError(f"prediction line {number}: unknown case_id {case_id}")
        case = cases[case_id]
        trial = int(prediction.get("trial", 1))
        if "events" in prediction:
            score = score_trace(case, prediction["events"], service, trial=trial)
        else:
            correct, errors = score_prediction(case, prediction.get("sql"), service)
            status = ("fail" if not correct else
                      "needs_review" if case.required_claims else "pass") if case.is_numeric else "needs_review"
            score = TrialScore(
                case.id, case.category, trial,
                status,
                sql_correct=correct, errors=errors,
            )
        scores.append(score)
    _save_json(args.out, {"trials": [score.to_dict() for score in scores],
                          "summary": summarize_trials(scores)})


async def _run_baseline(args: argparse.Namespace) -> None:
    from trust_agent.provider import OpenAIResponsesProvider

    cases = load_cases(args.cases)
    if args.limit:
        cases = cases[:args.limit]
    service = QueryService(args.db, ("trips", "zones"))
    provider = OpenAIResponsesProvider(model=args.model)
    scores, predictions = await run_baseline_trials(cases, provider, service,
                                                      repeats=args.repeats)
    if args.predictions_out:
        rendered = "".join(json.dumps(prediction, ensure_ascii=False) + "\n"
                           for prediction in predictions)
        await asyncio.to_thread(Path(args.predictions_out).write_text, rendered,
                                encoding="utf-8")
    _save_json(args.out, {"model": args.model,
                          "trials": [score.to_dict() for score in scores],
                          "summary": summarize_trials(scores)})


async def _run_agent(args: argparse.Namespace) -> None:
    from trust_agent.loop import AgentRunner
    from trust_agent.provider import OpenAIResponsesProvider
    from trust_agent.store import EventStore
    from trust_agent.tools import ToolRegistry

    cases = load_cases(args.cases)
    if args.limit:
        cases = cases[:args.limit]
    service = QueryService(args.db, ("trips", "zones"))
    project_root = Path(__file__).resolve().parents[3]
    runner = AgentRunner(
        OpenAIResponsesProvider(model=args.model),
        ToolRegistry(service, project_root),
        EventStore(args.state_db),
    )
    scores = await run_agent_trials(cases, runner, service, repeats=args.repeats)
    _save_json(args.out, {"model": args.model, "suite_size": len(cases),
                          "suite_status": "initial 12-case development suite",
                          "trace_store": str(Path(args.state_db).resolve()),
                          "trials": [score.to_dict() for score in scores],
                          "summary": summarize_trials(scores)})


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    for name in ("score", "baseline", "agent"):
        command = sub.add_parser(name)
        command.add_argument("--cases", default="evals/gold_cases.jsonl")
        command.add_argument("--db", default="data/nyc_taxi.duckdb")
        command.add_argument("--out", help="save JSON report")
    score = sub.choices["score"]
    score.add_argument("--predictions", required=True, help="JSONL with case_id, sql or events")
    baseline = sub.choices["baseline"]
    baseline.add_argument("--model", required=True)
    baseline.add_argument("--repeats", type=int, default=3)
    baseline.add_argument("--limit", type=int, default=0)
    baseline.add_argument("--predictions-out", help="save model outputs as JSONL")
    agent = sub.choices["agent"]
    agent.add_argument("--model", required=True)
    agent.add_argument("--repeats", type=int, default=3)
    agent.add_argument("--limit", type=int, default=0)
    agent.add_argument("--state-db", default="runtime/eval_agent.sqlite3",
                       help="SQLite event trace store")
    args = parser.parse_args()
    if args.command == "score":
        _score_predictions(args)
    elif args.command == "baseline":
        asyncio.run(_run_baseline(args))
    else:
        asyncio.run(_run_agent(args))


if __name__ == "__main__":
    main()
