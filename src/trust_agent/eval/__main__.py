"""CLI: python -m trust_agent.eval score|baseline|agent ..."""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
from pathlib import Path
from typing import Any

from trust_agent.sql import QueryService
from trust_agent.store import EventStore

from .review import SCORER_VERSION, apply_reviews, rescore_report
from .runner import TrialJournal, run_agent_trials, run_baseline_trials
from .scoring import TrialScore, score_prediction, score_trace, summarize_trials
from .tasks import load_cases


def _save_json(path: str | None, payload: Any) -> None:
    rendered = json.dumps(payload, ensure_ascii=False, indent=2)
    if path:
        Path(path).write_text(rendered + "\n", encoding="utf-8")
    print(rendered)


def _trial_journal(args: argparse.Namespace, cases: list[Any], provider: Any,
                   *, mode: str) -> TrialJournal | None:
    if args.resume and not args.checkpoint:
        raise ValueError("--resume requires --checkpoint")
    if not args.checkpoint:
        return None
    db_path = Path(args.db).resolve()
    db_stat = db_path.stat()
    provider_settings = {
        "TRUST_AGENT_PROVIDER": os.getenv("TRUST_AGENT_PROVIDER", "responses"),
        "OPENAI_BASE_URL": os.getenv("OPENAI_BASE_URL", ""),
        "TRUST_AGENT_MAX_OUTPUT_TOKENS": os.getenv("TRUST_AGENT_MAX_OUTPUT_TOKENS", ""),
        "TRUST_AGENT_CHAT_EXTRA_BODY": os.getenv("TRUST_AGENT_CHAT_EXTRA_BODY", ""),
    }
    metadata = {
        "version": 1,
        "mode": mode,
        "model_requested": args.model,
        "provider": type(provider).__name__,
        "generation_settings": _generation_settings(),
        # Store a digest of noncredential provider settings; never put keys in a journal.
        "provider_settings_sha256": hashlib.sha256(json.dumps(
            provider_settings, sort_keys=True).encode()).hexdigest(),
        "suite_sha256": hashlib.sha256(Path(args.cases).read_bytes()).hexdigest(),
        "trial_keys": [{"case_id": case.id, "trial": trial}
                       for case in cases for trial in range(1, args.repeats + 1)],
        "db": {"path": str(db_path), "size": db_stat.st_size,
               "mtime_ns": db_stat.st_mtime_ns},
    }
    if mode == "agent":
        metadata["budgets"] = {
            "max_turns": args.max_turns,
            "max_tool_calls": args.max_tool_calls,
            "max_total_tokens": args.max_total_tokens,
            "max_wall_seconds": args.max_wall_seconds,
        }
        metadata["trace_store"] = str(Path(args.state_db).resolve())
    return TrialJournal(args.checkpoint, metadata, resume=args.resume)


def _generation_settings() -> dict[str, Any]:
    extra_body = os.getenv("TRUST_AGENT_CHAT_EXTRA_BODY", "")
    return {
        "max_output_tokens": os.getenv("TRUST_AGENT_MAX_OUTPUT_TOKENS", "1024"),
        "chat_extra_body_sha256": hashlib.sha256(extra_body.encode()).hexdigest()
        if extra_body else None,
    }


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
            status = "fail" if correct is False else "needs_review"
            score = TrialScore(
                case.id, case.category, trial,
                status,
                numeric_case=case.is_numeric,
                sql_correct=correct, errors=errors,
            )
        scores.append(score)
    _save_json(args.out, {"trials": [score.to_dict() for score in scores],
                          "summary": summarize_trials(scores)})


def _rescore(args: argparse.Namespace) -> None:
    report = json.loads(Path(args.report).read_text(encoding="utf-8"))
    cases = {case.id: case for case in load_cases(args.cases)}
    if report.get("suite_sha256") != hashlib.sha256(Path(args.cases).read_bytes()).hexdigest():
        raise ValueError("report suite hash differs from current cases")
    if args.predictions:
        if args.state_db:
            raise ValueError("provide --predictions or --state-db, not both")
        predictions: dict[tuple[str, int], dict[str, Any]] = {}
        service = QueryService(args.db, ("trips", "zones"))
        for line_number, line in enumerate(Path(args.predictions).read_text(
                encoding="utf-8").splitlines(), 1):
            if not line.strip():
                continue
            prediction = json.loads(line)
            key = (prediction["case_id"], int(prediction["trial"]))
            if key in predictions:
                raise ValueError(f"duplicate prediction on line {line_number}: {key}")
            predictions[key] = prediction
        scores: list[TrialScore] = []
        for original in report.get("trials", []):
            score = TrialScore(**original)
            key = (score.case_id, score.trial)
            if key not in predictions or score.case_id not in cases or score.run_id:
                raise ValueError(f"missing or non-baseline prediction for {key}")
            prediction = predictions.pop(key)
            score.numeric_case = cases[score.case_id].is_numeric
            if prediction.get("error"):
                score.sql_correct = False if cases[score.case_id].is_numeric else None
                score.errors = [str(prediction["error"])]
                score.status = "fail"
            else:
                score.sql_correct, score.errors = score_prediction(
                    cases[score.case_id], prediction.get("sql"),
                    service)
                score.status = "fail" if score.sql_correct is False else "needs_review"
            score.human_review = None
            scores.append(score)
        if predictions:
            raise ValueError(f"predictions contain unknown trials: {sorted(predictions)}")
        rescored = {**{key: value for key, value in report.items()
                       if key not in {"trials", "summary"}},
                    "scorer_version": SCORER_VERSION,
                    "trials": [score.to_dict() for score in scores],
                    "summary": summarize_trials(scores)}
    else:
        if not args.state_db:
            raise ValueError("rescore requires --state-db or --predictions")
        if not Path(args.state_db).exists():
            raise FileNotFoundError(args.state_db)
        rescored = rescore_report(report, cases, EventStore(args.state_db),
                                  QueryService(args.db, ("trips", "zones")))
    _save_json(args.out, rescored)


def _review(args: argparse.Namespace) -> None:
    report = json.loads(Path(args.report).read_text(encoding="utf-8"))
    reviews = [json.loads(line) for line in Path(args.reviews).read_text(
        encoding="utf-8").splitlines() if line.strip()]
    _save_json(args.out, apply_reviews(report, reviews))


async def _run_baseline(args: argparse.Namespace) -> None:
    from trust_agent.config import make_provider

    cases = load_cases(args.cases)
    if args.limit:
        cases = cases[:args.limit]
    service = QueryService(args.db, ("trips", "zones"))
    provider = make_provider(args.model)
    journal = _trial_journal(args, cases, provider, mode="baseline")
    scores, predictions = await run_baseline_trials(cases, provider, service,
                                                      repeats=args.repeats,
                                                      completed=journal.baseline_results()
                                                      if journal else None,
                                                      on_trial=journal.append if journal else None)
    if args.predictions_out:
        rendered = "".join(json.dumps(prediction, ensure_ascii=False) + "\n"
                           for prediction in predictions)
        await asyncio.to_thread(Path(args.predictions_out).write_text, rendered,
                                encoding="utf-8")
    observed_models = sorted({prediction["model_observed"] for prediction in predictions
                              if prediction.get("model_observed")})
    _save_json(args.out, {"model_requested": args.model,
                          "models_observed": observed_models,
                          "provider": type(provider).__name__,
                          "generation_settings": _generation_settings(),
                          "suite": Path(args.cases).name,
                          "suite_sha256": hashlib.sha256(Path(args.cases).read_bytes()).hexdigest(),
                          "trials": [score.to_dict() for score in scores],
                          "summary": summarize_trials(scores)})


async def _run_agent(args: argparse.Namespace) -> None:
    from trust_agent.config import make_provider
    from trust_agent.loop import AgentRunner
    from trust_agent.store import EventStore
    from trust_agent.tools import ToolRegistry

    cases = load_cases(args.cases)
    if args.limit:
        cases = cases[:args.limit]
    service = QueryService(args.db, ("trips", "zones"))
    project_root = Path(__file__).resolve().parents[3]
    provider = make_provider(args.model)
    runner = AgentRunner(
        provider,
        ToolRegistry(service, project_root),
        EventStore(args.state_db),
        max_turns=args.max_turns,
        max_tool_calls=args.max_tool_calls,
        max_total_tokens=args.max_total_tokens,
        max_wall_seconds=args.max_wall_seconds,
    )
    journal = _trial_journal(args, cases, provider, mode="agent")
    completed = journal.scores() if journal else None
    if completed:
        for score in completed.values():
            if not runner.store.events(score.run_id):
                raise ValueError(f"journal run_id has no persisted trace: {score.run_id}")
    scores = await run_agent_trials(cases, runner, service, repeats=args.repeats,
                                    completed=completed,
                                    on_trial=journal.append if journal else None)
    observed_models = sorted({event["data"]["model"] for score in scores if score.run_id
                              for event in runner.store.events(score.run_id)
                              if event["kind"] in {"model_completed", "model_failed"}
                              and event["data"].get("model")})
    _save_json(args.out, {"model_requested": args.model,
                          "models_observed": observed_models,
                          "provider": type(provider).__name__,
                          "generation_settings": _generation_settings(),
                          "suite": Path(args.cases).name,
                          "suite_sha256": hashlib.sha256(Path(args.cases).read_bytes()).hexdigest(),
                          "suite_size": len(cases),
                          "budgets": {"max_turns": args.max_turns,
                                      "max_tool_calls": args.max_tool_calls,
                                      "max_total_tokens": args.max_total_tokens,
                                      "max_wall_seconds": args.max_wall_seconds},
                          "trace_store": str(Path(args.state_db).resolve()),
                          "trials": [score.to_dict() for score in scores],
                          "summary": summarize_trials(scores)})


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    for name in ("score", "baseline", "agent", "rescore", "review"):
        command = sub.add_parser(name)
        if name != "review":
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
    baseline.add_argument("--checkpoint", help="durable per-trial JSONL journal")
    baseline.add_argument("--resume", action="store_true", help="resume matching checkpoint")
    agent = sub.choices["agent"]
    agent.add_argument("--model", required=True)
    agent.add_argument("--repeats", type=int, default=3)
    agent.add_argument("--limit", type=int, default=0)
    agent.add_argument("--state-db", default="runtime/eval_agent.sqlite3",
                       help="SQLite event trace store")
    agent.add_argument("--max-turns", type=int, default=8)
    agent.add_argument("--max-tool-calls", type=int, default=16)
    agent.add_argument("--max-total-tokens", type=int, default=100_000)
    agent.add_argument("--max-wall-seconds", type=float, default=300)
    agent.add_argument("--checkpoint", help="durable per-trial JSONL journal")
    agent.add_argument("--resume", action="store_true", help="resume matching checkpoint")
    rescore = sub.choices["rescore"]
    rescore.add_argument("--report", required=True)
    rescore.add_argument("--state-db", help="agent trace store for no-model rescore")
    rescore.add_argument("--predictions", help="baseline predictions JSONL for no-model rescore")
    review = sub.choices["review"]
    review.add_argument("--report", required=True)
    review.add_argument("--reviews", required=True,
                        help="JSONL with case_id, trial, run_id, pass/fail, reviewer, reason")
    args = parser.parse_args()
    if args.command == "score":
        _score_predictions(args)
    elif args.command == "rescore":
        _rescore(args)
    elif args.command == "review":
        _review(args)
    elif args.command == "baseline":
        asyncio.run(_run_baseline(args))
    else:
        asyncio.run(_run_agent(args))


if __name__ == "__main__":
    main()
