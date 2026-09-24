#!/usr/bin/env python3
"""Download a frozen NYC TLC snapshot and build a small analytical DuckDB.

Usage: python scripts/prepare_data.py [--verify-only] [--force]
Requires: duckdb>=1.5,<2 for building; verification uses the standard library.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"
RAW = DATA / "raw"
MANIFEST = DATA / "source_manifest.json"
DB = DATA / "nyc_taxi.duckdb"


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def matches(path: Path, item: dict) -> bool:
    return (
        path.is_file()
        and path.stat().st_size == item["bytes"]
        and sha256_file(path) == item["sha256"]
    )


def fetch(item: dict) -> None:
    destination = RAW / item["name"]
    if matches(destination, item):
        print(f"verified {item['name']}")
        return
    if destination.exists():
        raise RuntimeError(f"existing file failed frozen hash check: {destination}")
    temporary = destination.with_suffix(destination.suffix + ".part")
    temporary.unlink(missing_ok=True)
    print(f"downloading {item['name']} ({item['bytes']:,} bytes)", flush=True)
    try:
        request = urllib.request.Request(
            item["url"], headers={"User-Agent": "trustworthy-data-agent/1.0"}
        )
        with urllib.request.urlopen(request, timeout=90) as response, temporary.open("wb") as output:
            for block in iter(lambda: response.read(1024 * 1024), b""):
                output.write(block)
        if not matches(temporary, item):
            raise RuntimeError(f"publisher content changed or download incomplete: {item['name']}")
        temporary.replace(destination)
    finally:
        temporary.unlink(missing_ok=True)


def sql_literal(path: Path) -> str:
    return "'" + str(path.resolve()).replace("'", "''") + "'"


def build_database(manifest: dict, *, force: bool) -> None:
    try:
        import duckdb
    except ImportError as exc:
        raise RuntimeError("install DuckDB first: python -m pip install 'duckdb>=1.5,<2'") from exc

    if DB.exists() and not force:
        try:
            existing = duckdb.connect(str(DB), read_only=True)
            stored_hash = existing.execute(
                "SELECT value FROM dataset_metadata WHERE key = 'source_manifest_sha256'"
            ).fetchone()
            stored_snapshot = existing.execute(
                "SELECT value FROM dataset_metadata WHERE key = 'snapshot'"
            ).fetchone()
        except duckdb.Error as exc:
            raise RuntimeError("existing database has no valid version metadata; use --force") from exc
        finally:
            if "existing" in locals():
                existing.close()
        if stored_hash != (sha256_file(MANIFEST),) or stored_snapshot != (manifest["snapshot"],):
            raise RuntimeError("existing database version differs from source manifest; use --force")
        print(f"database version verified: {DB} (use --force to rebuild)")
        return

    temporary = DB.with_suffix(".duckdb.part")
    temporary.unlink(missing_ok=True)
    RAW.mkdir(parents=True, exist_ok=True)
    months = [item for item in manifest["files"] if "month" in item]
    zones = RAW / "taxi_zone_lookup.csv"
    if len(months) != 2 or not all(matches(RAW / item["name"], item) for item in manifest["files"]):
        raise RuntimeError("all three source files must match the manifest before building")

    connection = duckdb.connect(str(temporary))
    try:
        connection.execute("SET memory_limit = '2GB'")
        connection.execute("SET threads = 4")
        connection.execute("SET preserve_insertion_order = false")
        connection.execute(
            f"CREATE TABLE zones AS SELECT CAST(LocationID AS INTEGER) AS location_id, "
            f"Borough AS borough, Zone AS zone, service_zone FROM read_csv_auto({sql_literal(zones)})"
        )
        month_selects = []
        for item in months:
            start = item["month"] + "-01"
            end = "2025-02-01" if item["month"] == "2025-01" else "2025-03-01"
            month_selects.append(
                "SELECT *, '" + item["month"] + "' AS source_month FROM read_parquet("
                + sql_literal(RAW / item["name"])
                + ") WHERE tpep_pickup_datetime >= TIMESTAMP '" + start
                + "' AND tpep_pickup_datetime < TIMESTAMP '" + end + "'"
            )
        source_sql = " UNION ALL ".join(month_selects)
        connection.execute(
            "CREATE TABLE trips AS "
            "WITH source AS (" + source_sql + "), "
            "typed AS (SELECT "
            "  tpep_pickup_datetime AS pickup_at, "
            "  tpep_dropoff_datetime AS dropoff_at, "
            "  date_diff('second', tpep_pickup_datetime, tpep_dropoff_datetime) / 60.0 AS duration_minutes, "
            "  CAST(PULocationID AS INTEGER) AS pickup_location_id, "
            "  CAST(DOLocationID AS INTEGER) AS dropoff_location_id, "
            "  CAST(passenger_count AS BIGINT) AS passenger_count, "
            "  CAST(trip_distance AS DOUBLE) AS trip_distance_miles, "
            "  CAST(fare_amount AS DOUBLE) AS fare_amount, "
            "  CAST(total_amount AS DOUBLE) AS total_amount, "
            "  CAST(tip_amount AS DOUBLE) AS tip_amount, "
            "  CAST(payment_type AS INTEGER) AS payment_type, "
            "  CAST(cbd_congestion_fee AS DOUBLE) AS cbd_congestion_fee, "
            "  source_month "
            "FROM source) "
            "SELECT * FROM typed "
            "WHERE duration_minutes BETWEEN 1 AND 240 "
            "AND trip_distance_miles BETWEEN 0.1 AND 100 "
            "AND pickup_location_id IN (SELECT location_id FROM zones) "
            "AND dropoff_location_id IN (SELECT location_id FROM zones)"
        )
        connection.execute(
            "CREATE TABLE dataset_metadata(key VARCHAR PRIMARY KEY, value VARCHAR)"
        )
        metadata = {
            "snapshot": manifest["snapshot"],
            "retrieved_utc": manifest["retrieved_utc"],
            "cleaning_version": "v1: month partition; 1-240 min; 0.1-100 mi; known zones",
            "source_manifest_sha256": sha256_file(MANIFEST),
            "trip_count": str(connection.execute("SELECT count(*) FROM trips").fetchone()[0]),
            "zone_count": str(connection.execute("SELECT count(*) FROM zones").fetchone()[0]),
        }
        connection.executemany("INSERT INTO dataset_metadata VALUES (?, ?)", list(metadata.items()))
        connection.execute("CHECKPOINT")
        print(f"built {temporary}: {metadata['trip_count']} trips; {metadata['zone_count']} zones")
    except BaseException:
        connection.close()
        temporary.unlink(missing_ok=True)
        raise
    else:
        connection.close()
        if DB.exists():
            DB.unlink()
        temporary.replace(DB)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--verify-only", action="store_true", help="check frozen source files without downloading or building")
    parser.add_argument("--force", action="store_true", help="rebuild the DuckDB database")
    args = parser.parse_args()
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    RAW.mkdir(parents=True, exist_ok=True)
    if args.verify_only:
        failed = [item["name"] for item in manifest["files"] if not matches(RAW / item["name"], item)]
        if failed:
            print("missing or changed: " + ", ".join(failed), file=sys.stderr)
            return 1
        print("all frozen sources verified")
        return 0
    for item in manifest["files"]:
        fetch(item)
    build_database(manifest, force=args.force)
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, RuntimeError) as error:
        print(f"data preparation failed: {error}", file=sys.stderr)
        raise SystemExit(1) from error
