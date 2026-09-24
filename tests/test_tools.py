import json
from pathlib import Path

import duckdb
import pytest
from test_loop import FakeQuery

from trust_agent.sql import QueryService
from trust_agent.tools import ToolRegistry


@pytest.mark.asyncio
async def test_standard_skill_is_loaded_on_demand():
    root = Path(__file__).resolve().parents[1]
    registry = ToolRegistry(FakeQuery(), root)
    result = await registry.execute("load_skill", json.dumps({"name": "diagnose-metric-shift"}))
    assert result["version"] == "1"
    assert result["sha256"]
    assert "name: diagnose-metric-shift" in result["content"]


def test_registry_rejects_mismatched_snapshot_manifest(tmp_path):
    root = Path(__file__).resolve().parents[1]
    db_path = tmp_path / "wrong.duckdb"
    with duckdb.connect(str(db_path)) as db:
        db.execute("CREATE TABLE trips(x INTEGER)")
        db.execute("CREATE TABLE zones(x INTEGER)")
        db.execute("CREATE TABLE dataset_metadata(key VARCHAR, value VARCHAR)")
        db.execute("INSERT INTO dataset_metadata VALUES ('source_manifest_sha256', 'wrong')")
    with pytest.raises(ValueError, match="does not match"):
        ToolRegistry(QueryService(db_path, {"trips", "zones"}), root)
