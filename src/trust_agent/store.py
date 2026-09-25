from __future__ import annotations

import json
import sqlite3
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .domain import RunState


class EventStore:
    """Append-only events plus a disposable run snapshot for fast resumption."""

    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as db:
            db.executescript(
                """
                CREATE TABLE IF NOT EXISTS runs (
                    run_id TEXT PRIMARY KEY,
                    state_json TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS events (
                    seq INTEGER PRIMARY KEY AUTOINCREMENT,
                    run_id TEXT NOT NULL,
                    turn INTEGER NOT NULL,
                    kind TEXT NOT NULL,
                    data_json TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS events_run_seq ON events(run_id, seq);
                CREATE TABLE IF NOT EXISTS memories (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    content TEXT NOT NULL UNIQUE,
                    source_run_id TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS scoped_memories (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    source_run_id TEXT NOT NULL,
                    content TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    UNIQUE(source_run_id, content)
                );
                CREATE INDEX IF NOT EXISTS scoped_memories_run_id
                    ON scoped_memories(source_run_id, id);
                INSERT OR IGNORE INTO scoped_memories(source_run_id, content, created_at)
                    SELECT source_run_id, content, created_at FROM memories;
                """
            )

    def _connect(self) -> sqlite3.Connection:
        db = sqlite3.connect(self.path, timeout=30)
        db.row_factory = sqlite3.Row
        return db

    def save(self, state: RunState) -> None:
        with self._connect() as db:
            db.execute(
                "INSERT INTO runs VALUES (?, ?, ?) ON CONFLICT(run_id) DO UPDATE SET "
                "state_json=excluded.state_json, updated_at=excluded.updated_at",
                (state.run_id, json.dumps(state.to_dict(), ensure_ascii=False), _now()),
            )

    def get(self, run_id: str) -> RunState | None:
        with self._connect() as db:
            row = db.execute("SELECT state_json FROM runs WHERE run_id=?", (run_id,)).fetchone()
        return RunState.from_dict(json.loads(row[0])) if row else None

    def append(self, run_id: str, turn: int, kind: str, data: dict[str, Any]) -> dict[str, Any]:
        created_at = _now()
        with self._connect() as db:
            cursor = db.execute(
                "INSERT INTO events(run_id,turn,kind,data_json,created_at) VALUES(?,?,?,?,?)",
                (run_id, turn, kind, json.dumps(data, ensure_ascii=False, default=str), created_at),
            )
            seq = cursor.lastrowid
        return {"seq": seq, "run_id": run_id, "turn": turn, "kind": kind, "data": data,
                "created_at": created_at}

    def append_many(self, run_id: str, turn: int,
                    items: list[tuple[str, dict[str, Any]]]) -> list[dict[str, Any]]:
        """Persist related events in one transaction before notifying clients."""
        saved = []
        with self._connect() as db:
            for kind, data in items:
                created_at = _now()
                cursor = db.execute(
                    "INSERT INTO events(run_id,turn,kind,data_json,created_at) VALUES(?,?,?,?,?)",
                    (run_id, turn, kind, json.dumps(data, ensure_ascii=False, default=str),
                     created_at),
                )
                saved.append({"seq": cursor.lastrowid, "run_id": run_id, "turn": turn,
                              "kind": kind, "data": data, "created_at": created_at})
        return saved

    def events(self, run_id: str, after: int = 0) -> list[dict[str, Any]]:
        with self._connect() as db:
            rows = db.execute(
                "SELECT * FROM events WHERE run_id=? AND seq>? ORDER BY seq", (run_id, after)
            ).fetchall()
        return [{"seq": r["seq"], "run_id": r["run_id"], "turn": r["turn"],
                 "kind": r["kind"], "data": json.loads(r["data_json"]),
                 "created_at": r["created_at"]} for r in rows]

    def replay(self, run_id: str) -> RunState | None:
        """Rebuild the conversation and terminal state from append-only events."""
        events = self.events(run_id)
        if not events:
            return None
        state = RunState(run_id)
        by_turn: dict[int, list[dict[str, Any]]] = {}
        for event in events:
            by_turn.setdefault(event["turn"], []).append(event)
        for event in events:
            kind, data = event["kind"], event["data"]
            if kind == "run_started":
                state.history.append({"role": "user", "content": data["question"]})
                state.status = "running"
                state.answer = ""
                state.stop_reason = None
            elif kind == "run_resumed":
                state.status = "running"
            elif kind == "model_completed":
                output = data["output"]
                state.history.extend(output)
                turn_events = by_turn[event["turn"]]
                results = {e["data"]["call_id"]: e["data"].get("result", {
                    "error": e["data"].get("error"), "message": e["data"].get("message")})
                    for e in turn_events if e["kind"] in {"tool_finished", "tool_failed"}}
                views = {e["data"]["call_id"]: e["data"]["output"] for e in turn_events
                         if e["kind"] == "tool_result_view"}
                for item in output:
                    if item.get("type") == "function_call" and (
                            item.get("call_id") in results or item.get("call_id") in views):
                        state.history.append({"type": "function_call_output",
                                              "call_id": item["call_id"],
                                              "output": (views[item["call_id"]]
                                                         if item["call_id"] in views else
                                                         json.dumps(results[item["call_id"]],
                                                                    ensure_ascii=False,
                                                                    default=str)[:6_000])})
            elif kind == "answer_rejected":
                state.history.append({"role": "developer", "content":
                    data.get("guidance", "Final answer lacked verifiable query evidence; "
                             "cite query_id.")})
            elif kind == "context_compacted":
                state.summary = data["summary"]
                state.compacted_until = data["compacted_until"]
            elif kind == "text_committed":
                # Legacy traces could crash after the visible commit and before
                # run_completed. The new writer persists both in one transaction.
                state.answer = data["text"]
                state.status = "completed"
                state.stop_reason = data.get("stop_reason")
            elif kind == "run_completed":
                state.answer = data["answer"]
                state.status = "completed"
                state.stop_reason = data.get("stop_reason")
            elif kind == "run_cancelled":
                state.status = "cancelled"
                state.stop_reason = data.get("stop_reason", "cancelled")
            elif kind == "run_failed":
                state.status = ("budget_exceeded" if data.get("error_type") == "BudgetExceeded"
                                else "failed")
                state.stop_reason = data.get("stop_reason", state.status)
            state.turn = max(state.turn, event["turn"])
        return state

    def add_memory(self, content: str, source_run_id: str) -> None:
        with self._connect() as db:
            db.execute(
                "INSERT OR IGNORE INTO scoped_memories(source_run_id,content,created_at) "
                "VALUES(?,?,?)",
                (source_run_id, content, _now()),
            )

    def memories(self, limit: int = 10, source_run_id: str | None = None) -> list[str]:
        with self._connect() as db:
            if source_run_id is None:
                rows = db.execute("SELECT content FROM scoped_memories ORDER BY id DESC LIMIT ?",
                                  (limit,)).fetchall()
            else:
                rows = db.execute("SELECT content FROM scoped_memories "
                                  "WHERE source_run_id=? ORDER BY id DESC LIMIT ?",
                                  (source_run_id, limit)).fetchall()
        return [r[0] for r in rows]


def _now() -> str:
    return datetime.now(UTC).isoformat()
