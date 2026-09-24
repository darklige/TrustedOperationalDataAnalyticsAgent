#!/usr/bin/env python3
"""Validate the frozen 92-case evaluation suite against the production SQL boundary."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import duckdb

from trust_agent.eval import load_cases, score_prediction
from trust_agent.sql import QueryService

ROOT = Path(__file__).resolve().parents[1]
EVALS = ROOT / "evals"


def read_raw(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()
            if line.strip()]


def main() -> int:
    manifest = EVALS.parent / "data/source_manifest.json"
    manifest_digest = hashlib.sha256(manifest.read_bytes()).hexdigest()
    with duckdb.connect(str(ROOT / "data/nyc_taxi.duckdb"), read_only=True,
                        config={"enable_external_access": "false"}) as database:
        metadata = dict(database.execute("SELECT key, value FROM dataset_metadata").fetchall())
    if metadata.get("snapshot") != "nyc-tlc-yellow-2025-01-02-v1":
        raise ValueError("database snapshot differs from frozen eval suite")
    if metadata.get("source_manifest_sha256") != manifest_digest:
        raise ValueError("database was built from a different source manifest")

    original = read_raw(EVALS / "gold_cases.jsonl")
    expanded = read_raw(EVALS / "expanded_cases.jsonl")
    dev = read_raw(EVALS / "dev_cases.jsonl")
    heldout = read_raw(EVALS / "heldout_cases.jsonl")
    if len(original) != 12 or len(expanded) != 80 or len(dev) != 72 or len(heldout) != 20:
        raise ValueError("suite size or split changed without review")
    if dev[:12] != original:
        raise ValueError("the initial 12 cases were modified in the dev suite")
    if dev[12:] != [item for item in expanded if item["split"] == "dev"]:
        raise ValueError("dev split differs from expanded source")
    if heldout != [item for item in expanded if item["split"] == "heldout"]:
        raise ValueError("heldout split differs from expanded source")
    if len({item["question"] for item in original + expanded}) != 92:
        raise ValueError("duplicate question text")
    for item in expanded:
        if item["dataset_snapshot"] != "nyc-tlc-yellow-2025-01-02-v1":
            raise ValueError(f"wrong dataset snapshot on {item['id']}")

    initial_cases = load_cases(EVALS / "gold_cases.jsonl")
    cases = load_cases(EVALS / "expanded_cases.jsonl")
    load_cases(EVALS / "dev_cases.jsonl")
    load_cases(EVALS / "heldout_cases.jsonl")
    service = QueryService(ROOT / "data/nyc_taxi.duckdb", ("trips", "zones"))
    checked = 0
    for case in initial_cases + cases:
        if not case.is_numeric:
            continue
        correct, errors = score_prediction(case, case.gold_sql, service)
        if not correct:
            raise ValueError(f"{case.id} reference SQL failed: {errors}")
        checked += 1
    if checked != 79:
        raise ValueError(f"expected 79 total numeric gold cases, got {checked}")
    print("verified: 12 original unchanged; 80 new cases; 72 dev / 20 heldout")
    print("production QueryService: 79/79 numeric gold SQL results match (70 new)")
    print("13 behavioral cases require human rubric review; this verifier did not run a model")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
