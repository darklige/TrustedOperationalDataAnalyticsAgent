from __future__ import annotations

import json
from dataclasses import dataclass
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
