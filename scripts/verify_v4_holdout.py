#!/usr/bin/env python3
"""Verify frozen v4 holdout gold against the production SQL boundary.

This script does not invoke an Agent model. A changed suite or source manifest
must receive a new version rather than silently updating the frozen constants.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import duckdb

from trust_agent.eval.tasks import load_cases
from trust_agent.sql import QueryService

ROOT = Path(__file__).resolve().parents[1]
SUITE = ROOT / "evals/frozen_heldout_v4.jsonl"
DATABASE = ROOT / "data/nyc_taxi.duckdb"
SOURCE_MANIFEST = ROOT / "data/source_manifest.json"
SNAPSHOT = "nyc-tlc-yellow-2025-01-02-v1"
SUITE_SHA256 = "820519222ac9ba3eb871bc12a28f50302d6ebfb4b3ec90636d00f8b4d414afd2"
SOURCE_MANIFEST_SHA256 = "48fbd0a206fe5f9a9b3fe756f1d739dd805b01985526e958c9e5a4897620225d"


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def verify() -> tuple[int, int]:
    if digest(SUITE) != SUITE_SHA256:
        raise ValueError("v4 holdout changed after freeze; create another version instead")
    if digest(SOURCE_MANIFEST) != SOURCE_MANIFEST_SHA256:
        raise ValueError("source manifest changed after v4 holdout freeze")

    cases = load_cases(SUITE)
    if len(cases) != 20 or [case.id for case in cases] != [f"H{n}" for n in range(401, 421)]:
        raise ValueError("expected exactly H401–H420 in frozen order")
    raw = [json.loads(line) for line in SUITE.read_text(encoding="utf-8").splitlines()]
    if any(item.get("split") != "heldout_v4" or item.get("dataset_snapshot") != SNAPSHOT
           for item in raw):
        raise ValueError("wrong split or snapshot in v4 holdout")
    if len({case.question.strip() for case in cases}) != 20:
        raise ValueError("duplicate v4 question")

    older = [case for path in (ROOT / "evals").glob("*.jsonl") if path != SUITE
             for case in load_cases(path)]
    old_ids = {case.id for case in older}
    old_questions = {case.question.strip() for case in older}
    if any(case.id in old_ids or case.question.strip() in old_questions for case in cases):
        raise ValueError("v4 reuses an existing case ID or exact question")

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
        raise ValueError("database snapshot does not match v4 holdout")
    if metadata.get("source_manifest_sha256") != SOURCE_MANIFEST_SHA256:
        raise ValueError("database manifest does not match v4 holdout")

    service = QueryService(DATABASE, ("trips", "zones"))
    for case in numeric:
        assert case.gold_sql is not None
        result = service.query(case.gold_sql, timeout_s=30)
        if result["truncated"] or result["row_count"] != len(result["rows"]):
            raise ValueError(f"{case.id}: gold result was truncated")
        if result["rows"] != case.expected_rows:
            raise ValueError(f"{case.id}: production QueryService result differs from gold")
    return len(numeric), len(behavior)


def main() -> int:
    numeric, behavior = verify()
    print(f"v4 holdout SHA-256: {SUITE_SHA256}")
    print(f"verified {numeric}/{numeric} numeric gold results through production QueryService")
    print(f"verified {behavior} distinct behavioral rubrics; no Agent model was run")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
