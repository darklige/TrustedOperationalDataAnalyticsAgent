"""Safety and useful-query checks for the untrusted SQL boundary."""

from __future__ import annotations

import json
from pathlib import Path

import duckdb
import pytest

from trust_agent.sql import QueryExecutionError, QueryService, QueryTimeoutError, SQLPolicyError


@pytest.fixture
def service(tmp_path):
    path = tmp_path / "test.duckdb"
    connection = duckdb.connect(str(path))
    connection.execute("CREATE TABLE trips (id INTEGER, zone_id INTEGER, fare DOUBLE, taken_at TIMESTAMP)")
    connection.execute(
        "INSERT INTO trips VALUES (1, 10, 12.5, '2025-01-01 10:00:00'), "
        "(2, 10, 20.0, '2025-01-02 12:00:00'), "
        "(3, 20, 9.5, '2025-02-01 18:00:00')"
    )
    connection.execute("CREATE TABLE zones (zone_id INTEGER, name VARCHAR)")
    connection.execute("INSERT INTO zones VALUES (10, 'A'), (20, 'B')")
    connection.execute("CREATE TABLE private_data (secret VARCHAR)")
    connection.execute("INSERT INTO private_data VALUES ('do not disclose')")
    connection.execute("CREATE VIEW private_view AS SELECT * FROM private_data")
    connection.close()
    return QueryService(path, ["trips", "zones"])


def test_schema_shows_only_allowed_physical_tables(service):
    schema = service.schema()
    assert set(schema) == {"trips", "zones"}
    assert schema["trips"][0] == {"name": "id", "type": "INTEGER"}


def test_analytical_cte_and_join(service):
    result = service.query(
        """WITH totals AS (
               SELECT zone_id, COUNT(*) AS n, ROUND(AVG(fare), 2) AS avg_fare
               FROM trips GROUP BY zone_id
           )
           SELECT z.name, t.n, t.avg_fare
           FROM totals t JOIN zones z ON t.zone_id = z.zone_id
           ORDER BY z.name"""
    )
    assert result["columns"] == ["name", "n", "avg_fare"]
    assert result["rows"] == [["A", 2, 16.25], ["B", 1, 9.5]]
    assert result["truncated"] is False


def test_row_limit_and_truncation(service):
    result = service.query("SELECT id FROM trips ORDER BY id", row_limit=2)
    assert result["rows"] == [[1], [2]]
    assert result["row_count"] == 2
    assert result["truncated"] is True


@pytest.mark.parametrize(
    "sql",
    [
        "DELETE FROM trips",
        "CREATE TABLE stolen AS SELECT * FROM trips",
        "SELECT * FROM trips; DELETE FROM zones",
        "SELECT * FROM private_data",
        "SELECT * FROM private_view",
        "SELECT * FROM main.trips",
        "SELECT * FROM read_csv('/etc/passwd')",
        "SELECT * FROM 'https://example.com/data.parquet'",
        "SELECT current_setting('enable_external_access')",
        "SELECT * FROM duckdb_settings()",
        "SELECT CASE WHEN id = 1 THEN read_text('/etc/passwd') ELSE '' END FROM trips",
        "PRAGMA enable_external_access=true",
        (
            "SELECT * FROM (WITH private_data AS (SELECT * FROM trips) "
            "SELECT * FROM private_data) q, private_data"
        ),
    ],
)
def test_policy_rejects_unsafe_sql(service, sql):
    with pytest.raises(SQLPolicyError):
        service.query(sql)


def test_function_allowlist_keeps_date_analysis(service):
    result = service.query(
        "SELECT DATE_TRUNC('month', taken_at) AS month, COUNT(*) AS n "
        "FROM trips GROUP BY 1 ORDER BY 1"
    )
    assert result["rows"] == [["2025-01-01 00:00:00", 2], ["2025-02-01 00:00:00", 1]]


def test_case_and_boolean_expressions_are_allowed(service):
    result = service.query(
        "SELECT SUM(CASE WHEN fare > 10 AND zone_id = 10 THEN 1 ELSE 0 END) AS n "
        "FROM trips WHERE NOT (zone_id = 20) OR fare > 15"
    )
    assert result["rows"] == [[2]]


def test_invalid_limits_fail_before_spawning(service):
    with pytest.raises(ValueError):
        service.query("SELECT 1", row_limit=0)
    with pytest.raises(ValueError):
        service.query("SELECT 1", timeout_s=0)


def test_timeout_stops_worker(service):
    with pytest.raises(QueryTimeoutError):
        service.query("SELECT 1 FROM trips", timeout_s=0.001)


def test_database_errors_are_distinct_from_policy_errors(service):
    with pytest.raises(QueryExecutionError, match="missing_column"):
        service.query("SELECT missing_column FROM trips")


def test_allowed_relation_must_be_physical(tmp_path):
    path = tmp_path / "view.duckdb"
    connection = duckdb.connect(str(path))
    connection.execute("CREATE TABLE source (n INTEGER)")
    connection.execute("CREATE VIEW borrowed AS SELECT * FROM source")
    connection.close()
    service = QueryService(path, ["borrowed"])
    with pytest.raises(QueryExecutionError, match="physical table"):
        service.schema()


def test_fixed_gold_sql_runs_through_policy():
    root = Path(__file__).resolve().parents[1]
    db_path = root / "data" / "nyc_taxi.duckdb"
    if not db_path.is_file():
        pytest.skip("Download and prepare the fixed TLC snapshot for this integration check")
    service = QueryService(db_path, ["trips", "zones", "dataset_metadata"])
    cases = [json.loads(line) for line in (root / "evals" / "gold_cases.jsonl").read_text().splitlines()]
    checked = 0
    for case in cases:
        if "gold_sql" not in case:
            continue
        result = service.query(case["gold_sql"], timeout_s=30)
        assert result["rows"] == case["expected_rows"], case["id"]
        checked += 1
    assert checked == 9
