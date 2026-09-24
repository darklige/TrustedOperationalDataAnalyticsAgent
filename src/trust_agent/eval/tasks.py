from __future__ import annotations

import json
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any


@dataclass(frozen=True, slots=True)
class EvalCase:
    id: str
    category: str
    question: str
    gold_sql: str | None
    expected_rows: list[list[Any]] | None
    expected_behavior: str | None
    required_claims: tuple[str, ...] = ()
    review_expected_rows: tuple[list[list[Any]], ...] = ()

    @property
    def is_numeric(self) -> bool:
        return self.gold_sql is not None


def load_cases(path: str | Path) -> list[EvalCase]:
    """Load and validate a frozen JSONL suite; fail loudly on ambiguous gold data."""
    path = Path(path)
    cases: list[EvalCase] = []
    seen: set[str] = set()
    for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        try:
            raw = json.loads(line)
            if not isinstance(raw, dict):
                raise TypeError("case must be an object")
            case_id = raw["id"]
            category = raw["category"]
            question = raw["question"]
            if not all(isinstance(value, str) and value.strip()
                       for value in (case_id, category, question)):
                raise ValueError("id/category/question must be nonempty strings")
            if case_id in seen:
                raise ValueError(f"duplicate case id: {case_id}")
            gold_sql = raw.get("gold_sql")
            expected_rows = raw.get("expected_rows")
            expected_behavior = raw.get("expected_behavior")
            if gold_sql is not None:
                if not isinstance(gold_sql, str) or not gold_sql.strip():
                    raise ValueError("gold_sql must be nonempty")
                if not isinstance(expected_rows, list) or not all(
                    isinstance(row, list) for row in expected_rows
                ):
                    raise ValueError("numeric case requires list-of-lists expected_rows")
                if expected_behavior is not None:
                    raise ValueError("case cannot define both numeric and behavior gold")
            elif not isinstance(expected_behavior, str) or not expected_behavior.strip():
                raise ValueError("behavior case requires expected_behavior")
            claims = raw.get("required_claims", [])
            if not isinstance(claims, list) or any(not isinstance(x, str) for x in claims):
                raise ValueError("required_claims must be strings")
            cases.append(EvalCase(case_id, category, question, gold_sql,
                                  expected_rows, expected_behavior, tuple(claims)))
            seen.add(case_id)
        except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
            raise ValueError(f"{path}:{line_number}: invalid eval case: {exc}") from exc
    if not cases:
        raise ValueError(f"no eval cases in {path}")
    return cases


def apply_ambiguity_sidecar(cases: list[EvalCase], path: str | Path,
                            query_service: Any) -> list[EvalCase]:
    """Add documented alternative results as review-only evidence.

    The frozen case and gold SQL are not changed. An alternative result may
    downgrade an automatic false to needs_review, never award an auto-pass.
    """
    raw = json.loads(Path(path).read_text(encoding="utf-8"))
    if (not isinstance(raw, dict) or raw.get("version") != 1
            or raw.get("historical_assets_immutable") is not True):
        raise ValueError("unsupported or mutable ambiguity sidecar")
    entries = raw.get("ambiguities")
    if not isinstance(entries, list):
        raise TypeError("ambiguities must be a list")
    by_id = {case.id: case for case in cases}
    seen: set[str] = set()
    for entry in entries:
        case_id = entry.get("case_id") if isinstance(entry, dict) else None
        if case_id in seen or case_id not in by_id:
            raise ValueError(f"duplicate or unknown ambiguity case: {case_id}")
        seen.add(case_id)
        case = by_id[case_id]
        rows = entry.get("alternative_expected_rows")
        if (not case.is_numeric or entry.get("decision") != "needs_review"
                or not isinstance(entry.get("rationale"), str)
                or not entry["rationale"].strip()
                or not isinstance(entry.get("alternative_sql"), str)
                or not isinstance(rows, list)
                or not all(isinstance(row, list) for row in rows)):
            raise ValueError(f"invalid alternative for {case_id}")
        result = query_service.query(entry["alternative_sql"], row_limit=1000)
        if result.get("truncated") or result.get("rows") != rows:
            raise ValueError(f"alternative query result differs from sidecar: {case_id}")
        by_id[case_id] = replace(case, review_expected_rows=(*case.review_expected_rows, rows))
    return [by_id[case.id] for case in cases]
