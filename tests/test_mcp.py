"""MCP contract tests against an actual stdio subprocess."""

from __future__ import annotations

import sys
from pathlib import Path

import duckdb
import pytest
from mcp import Client, StdioServerParameters


@pytest.fixture
def sample_db(tmp_path: Path) -> Path:
    db_path = tmp_path / "sample.duckdb"
    connection = duckdb.connect(str(db_path))
    try:
        connection.execute("CREATE TABLE trips (trip_id INTEGER, trip_duration_minutes DOUBLE)")
        connection.execute("INSERT INTO trips VALUES (1, 12.5), (2, 20.0)")
        connection.execute("CREATE TABLE zones (location_id INTEGER, zone VARCHAR)")
        connection.execute("INSERT INTO zones VALUES (1, 'Airport')")
    finally:
        connection.close()
    return db_path


@pytest.mark.asyncio
async def test_stdio_mcp_lists_and_calls_same_registry(sample_db: Path) -> None:
    root = Path(__file__).resolve().parents[1]
    params = StdioServerParameters(
        command=sys.executable,
        args=["-m", "trust_agent.mcp_server", "--db", str(sample_db)],
        cwd=root,
    )
    async with Client(params) as client:
        listed = await client.list_tools()
        assert {tool.name for tool in listed.tools} == {
            "describe_data", "get_metric", "run_sql", "load_skill"
        }
        assert all(tool.annotations and tool.annotations.read_only_hint for tool in listed.tools)

        schema = await client.call_tool("describe_data", {})
        assert not schema.is_error
        assert {"trips", "zones"}.issubset(schema.structured_content["schema"])

        metric = await client.call_tool("get_metric", {"name": "trip_count"})
        assert not metric.is_error
        assert metric.structured_content["name"] == "trip_count"

        rows = await client.call_tool("run_sql", {"sql": "SELECT COUNT(*) AS n FROM trips"})
        assert not rows.is_error
        assert rows.structured_content["rows"] == [[2]]

        denied = await client.call_tool("run_sql", {"sql": "SELECT * FROM read_text('/etc/passwd')"})
        assert denied.is_error
