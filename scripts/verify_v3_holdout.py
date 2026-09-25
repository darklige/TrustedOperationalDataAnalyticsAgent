#!/usr/bin/env python3
"""Verify frozen v3 gold data without sending any case to an Agent model."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import duckdb

from trust_agent.eval.tasks import load_cases
from trust_agent.sql import QueryService

ROOT = Path(__file__).resolve().parents[1]
SUITE = ROOT / "evals/frozen_heldout_v3.jsonl"
DATABASE = ROOT / "data/nyc_taxi.duckdb"
SOURCE_MANIFEST = ROOT / "data/source_manifest.json"
SNAPSHOT = "nyc-tlc-yellow-2025-01-02-v1"
SUITE_SHA256 = "fb91b7ebeb639c0b8e25c1fcf562419f3c3c2abd9e88d28a0608c8cb908b7212"
SOURCE_MANIFEST_SHA256 = "48fbd0a206fe5f9a9b3fe756f1d739dd805b01985526e958c9e5a4897620225d"


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def verify() -> tuple[int, int]:
    if digest(SUITE) != SUITE_SHA256:
        raise ValueError("v3 holdout changed after freeze; create another version instead")
    if digest(SOURCE_MANIFEST) != SOURCE_MANIFEST_SHA256:
        raise ValueError("source manifest changed after v3 holdout freeze")

    cases = load_cases(SUITE)
    if len(cases) != 20 or [case.id for case in cases] != [f"H{n}" for n in range(301, 321)]:
        raise ValueError("expected exactly H301–H320 in frozen order")
    raw = [json.loads(line) for line in SUITE.read_text(encoding="utf-8").splitlines()]
    if any(item.get("split") != "heldout_v3" or item.get("dataset_snapshot") != SNAPSHOT
           for item in raw):
        raise ValueError("wrong split or dataset snapshot in v3 holdout")
    if len({case.question for case in cases}) != len(cases):
        raise ValueError("duplicate v3 question")

    # A new suite must not reuse IDs or verbatim prompts from earlier assets.
    other_paths = [p for p in (ROOT / "evals").glob("*.jsonl") if p != SUITE]
    older = [case for path in other_paths for case in load_cases(path)]
    older_ids = {case.id for case in older}
    older_questions = {case.question.strip() for case in older}
    if any(case.id in older_ids or case.question.strip() in older_questions for case in cases):
        raise ValueError("v3 reuses an existing case ID or exact prompt")

    numeric = [case for case in cases if case.is_numeric]
    behavior = [case for case in cases if not case.is_numeric]
    if len(numeric) != 12 or len(behavior) != 8:
        raise ValueError("expected 12 numeric and 8 behavioral cases")
    if any(not case.expected_rows or not case.required_claims for case in numeric):
        raise ValueError("numeric cases require nonempty gold and scope claims")
    if len({case.category for case in behavior}) != 8:
        raise ValueError("behavioral cases must cover eight distinct rubrics")

    with duckdb.connect(str(DATABASE), read_only=True,
                        config={"enable_external_access": "false"}) as connection:
        metadata = dict(connection.execute("SELECT key, value FROM dataset_metadata").fetchall())
    if metadata.get("snapshot") != SNAPSHOT:
        raise ValueError("database snapshot does not match v3 holdout")
    if metadata.get("source_manifest_sha256") != SOURCE_MANIFEST_SHA256:
        raise ValueError("database manifest does not match v3 holdout")

    service = QueryService(DATABASE, ("trips", "zones"))
    for case in numeric:
        assert case.gold_sql is not None
        result = service.query(case.gold_sql, timeout_s=30)
        if result["truncated"] or result["row_count"] != len(result["rows"]):
            raise ValueError(f"{case.id}: gold result was truncated")
        if result["rows"] != case.expected_rows:
            raise ValueError(f"{case.id}: production QueryService result differs from frozen gold")
    return len(numeric), len(behavior)


def main() -> int:
    numeric, behavior = verify()
    print(f"v3 holdout SHA-256: {SUITE_SHA256}")
    print(f"verified {numeric}/{numeric} numeric gold results through production QueryService")
    print(f"verified {behavior} distinct behavioral rubrics; no Agent model was run")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
