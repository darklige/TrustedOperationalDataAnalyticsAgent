from __future__ import annotations

import asyncio
import hashlib
import json
from pathlib import Path
from typing import Any

from .sql import QueryService


class ToolError(Exception):
    pass


class ToolRegistry:
    def __init__(self, query_service: QueryService, project_root: str | Path):
        self.query_service = query_service
        self.project_root = Path(project_root)
        manifest_path = self.project_root / "data" / "source_manifest.json"
        self.dataset_version = "unverified"
        if manifest_path.exists() and isinstance(query_service, QueryService):
            import duckdb

            manifest_hash = hashlib.sha256(manifest_path.read_bytes()).hexdigest()
            with duckdb.connect(str(query_service.db_path), read_only=True,
                                config={"enable_external_access": "false"}) as db:
                try:
                    row = db.execute("SELECT value FROM dataset_metadata WHERE "
                                     "key='source_manifest_sha256'").fetchone()
                except duckdb.Error:
                    row = None
            if row is not None:
                if row[0] != manifest_hash:
                    raise ValueError("DuckDB snapshot does not match frozen source manifest")
                self.dataset_version = json.loads(manifest_path.read_text()).get("snapshot", "unknown")
        self._metrics = {
            "trip_count": "Trips after cleaning. COUNT(*) over trips; always specify date window.",
            "trip_duration_minutes": "Date_diff in minutes between pickup and dropoff; exclude nonpositive durations.",
            "median_trip_duration": "MEDIAN(trip_duration_minutes); compare like-for-like routes and hours.",
            "airport_trip": "A trip whose pickup or dropoff zone has service_zone='EWR' or zone contains 'Airport'.",
        }
        self._skill_files = {
            "diagnose-metric-shift": self.project_root / "skills" / "diagnose-metric-shift" / "SKILL.md",
        }

    def specs(self) -> list[dict[str, Any]]:
        def spec(name: str, description: str, properties: dict, required: list[str]) -> dict:
            return {"type": "function", "name": name, "description": description,
                    "parameters": {"type": "object", "properties": properties,
                                   "required": required, "additionalProperties": False}, "strict": True}

        return [
            spec("describe_data", "Get allowed table schemas and dataset caveats before SQL.", {}, []),
            spec("get_metric", "Read one canonical metric definition."
                 " Use this before calculating a named metric.",
                 {"name": {"type": "string", "description": "Metric name"}}, ["name"]),
            spec("run_sql", "Run one read-only SQL query on allowed tables. Returns capped rows."
                 " Include a date filter for trips whenever possible.",
                 {"sql": {"type": "string", "description": "One DuckDB SELECT statement"}}, ["sql"]),
            spec("load_skill", "Load a task recipe when investigating a metric shift.",
                 {"name": {"type": "string", "enum": list(self._skill_files)}}, ["name"]),
        ]

    async def execute(self, name: str, raw_arguments: str) -> dict[str, Any]:
        try:
            arguments = json.loads(raw_arguments)
        except json.JSONDecodeError as exc:
            raise ToolError("invalid JSON arguments") from exc
        if not isinstance(arguments, dict):
            raise ToolError("arguments must be a JSON object")
        allowed = {"describe_data": set(), "get_metric": {"name"},
                   "run_sql": {"sql"}, "load_skill": {"name"}}
        if name not in allowed:
            raise ToolError(f"unknown tool: {name}")
        if set(arguments) != allowed[name]:
            raise ToolError(f"invalid arguments for {name}")

        if name == "describe_data":
            schema = await asyncio.to_thread(self.query_service.schema)
            return {"schema": schema, "caveat": "Observational data; do not claim causation."}
        if name == "get_metric":
            metric = self._metrics.get(arguments["name"])
            if not metric:
                return {"error": "unknown metric", "available": list(self._metrics)}
            return {"name": arguments["name"], "definition": metric}
        if name == "run_sql":
            result = await asyncio.to_thread(self.query_service.query, arguments["sql"])
            return result
        skill_path = self._skill_files.get(arguments["name"])
        if skill_path is None or not skill_path.exists():
            raise ToolError("skill not found")
        content = skill_path.read_text()[:12_000]
        return {"name": arguments["name"], "content": content,
                "sha256": hashlib.sha256(content.encode()).hexdigest(), "version": "1"}
