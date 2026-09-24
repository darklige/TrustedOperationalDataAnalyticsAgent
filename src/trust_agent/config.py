from __future__ import annotations

import os
from pathlib import Path

from .loop import AgentRunner
from .provider import OpenAIResponsesProvider
from .sql import QueryService
from .store import EventStore
from .tools import ToolRegistry


def project_root() -> Path:
    return Path(__file__).resolve().parents[2]


def make_runner() -> AgentRunner:
    root = project_root()
    db_path = root / os.getenv("TRUST_AGENT_DB", "data/nyc_taxi.duckdb")
    state_path = root / os.getenv("TRUST_AGENT_STATE_DB", "runtime/agent.sqlite3")
    provider = OpenAIResponsesProvider(os.getenv("TRUST_AGENT_MODEL", ""),
                                       base_url=os.getenv("OPENAI_BASE_URL"))
    query = QueryService(db_path, allowed_tables={"trips", "zones"})
    return AgentRunner(provider, ToolRegistry(query, root), EventStore(state_path),
                       max_turns=int(os.getenv("TRUST_AGENT_MAX_TURNS", "8")),
                       context_char_budget=int(os.getenv("TRUST_AGENT_CONTEXT_CHAR_BUDGET", "45000")),
                       max_total_tokens=int(os.getenv("TRUST_AGENT_MAX_TOTAL_TOKENS", "100000")),
                       max_wall_seconds=float(os.getenv("TRUST_AGENT_MAX_WALL_SECONDS", "300")))
