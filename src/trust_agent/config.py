from __future__ import annotations

import json
import os
from pathlib import Path

from .chat_provider import ChatCompletionsProvider
from .loop import AgentRunner
from .provider import ModelProvider, OpenAIResponsesProvider
from .sql import QueryService
from .store import EventStore
from .tools import ToolRegistry


def project_root() -> Path:
    return Path(__file__).resolve().parents[2]


def make_provider(model: str | None = None) -> ModelProvider:
    selected = os.getenv("TRUST_AGENT_PROVIDER", "responses")
    model = model or os.getenv("TRUST_AGENT_MODEL", "")
    base_url = os.getenv("OPENAI_BASE_URL")
    if selected == "responses":
        return OpenAIResponsesProvider(model, base_url=base_url)
    if selected == "chat_completions":
        extra_body_raw = os.getenv("TRUST_AGENT_CHAT_EXTRA_BODY", "")
        extra_body = json.loads(extra_body_raw) if extra_body_raw else None
        if extra_body is not None and not isinstance(extra_body, dict):
            raise ValueError("TRUST_AGENT_CHAT_EXTRA_BODY must be a JSON object")
        return ChatCompletionsProvider(
            model, base_url=base_url,
            max_output_tokens=int(os.getenv("TRUST_AGENT_MAX_OUTPUT_TOKENS", "1024")),
            extra_body=extra_body,
        )
    raise ValueError(f"unknown TRUST_AGENT_PROVIDER: {selected}")


def make_runner() -> AgentRunner:
    root = project_root()
    db_path = root / os.getenv("TRUST_AGENT_DB", "data/nyc_taxi.duckdb")
    state_path = root / os.getenv("TRUST_AGENT_STATE_DB", "runtime/agent.sqlite3")
    provider = make_provider()
    query = QueryService(db_path, allowed_tables={"trips", "zones"})
    return AgentRunner(provider, ToolRegistry(query, root), EventStore(state_path),
                       max_turns=int(os.getenv("TRUST_AGENT_MAX_TURNS", "8")),
                       context_char_budget=int(os.getenv("TRUST_AGENT_CONTEXT_CHAR_BUDGET", "45000")),
                       max_total_tokens=int(os.getenv("TRUST_AGENT_MAX_TOTAL_TOKENS", "100000")),
                       max_wall_seconds=float(os.getenv("TRUST_AGENT_MAX_WALL_SECONDS", "300")))
