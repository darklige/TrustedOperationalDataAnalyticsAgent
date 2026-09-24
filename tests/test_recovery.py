import json
import sqlite3

import pytest

from trust_agent.domain import ProviderEvent, RunState
from trust_agent.loop import AgentRunner
from trust_agent.store import EventStore
from trust_agent.tools import ToolRegistry


class RecordingQuery:
    def __init__(self):
        self.calls = []

    def query(self, sql):
        self.calls.append(sql)
        return {"columns": ["count"], "rows": [[3]], "row_count": 1,
                "truncated": False, "sql": sql}

    def schema(self):
        return {"trips": ["count"]}


class FinishFromEvidence:
    async def summarize(self, items):
        return "summary"

    async def stream(self, messages, tools, instructions):
        result = next(json.loads(item["output"]) for item in messages
                      if item.get("type") == "function_call_output")
        answer = f"3 trips [query_id:{result['query_id']}]"
        yield ProviderEvent("text_delta", {"text": answer})
        yield ProviderEvent("completed", {"output": [{"type": "message", "role": "assistant",
            "content": [{"type": "output_text", "text": answer}]}], "usage": {}})


class CannotVerify:
    async def summarize(self, items):
        return "summary"

    async def stream(self, messages, tools, instructions):
        answer = "无法核实"
        yield ProviderEvent("text_delta", {"text": answer})
        yield ProviderEvent("completed", {"output": [{"type": "message", "role": "assistant",
            "content": [{"type": "output_text", "text": answer}]}], "usage": {}})


@pytest.mark.asyncio
async def test_recovery_uses_event_log_and_does_not_rerun_persisted_tool(tmp_path):
    store = EventStore(tmp_path / "events.sqlite3")
    run_id = "interrupted"
    question = "How many trips?"
    call = {"type": "function_call", "call_id": "call-1", "name": "run_sql",
            "arguments": json.dumps({"sql": "SELECT COUNT(*) FROM trips"})}
    store.save(RunState(run_id, history=[{"role": "user", "content": question}], turn=1))
    store.append(run_id, 0, "run_started", {"question": question})
    store.append(run_id, 1, "loop_started", {})
    store.append(run_id, 1, "tool_call_ready", call)
    store.append(run_id, 1, "model_completed", {"output": [call], "usage": {}})
    result = {"columns": ["count"], "rows": [[3]], "row_count": 1,
              "truncated": False, "sql": "SELECT COUNT(*) FROM trips",
              "query_id": "persisted-id"}
    store.append(run_id, 1, "tool_started", {"call_id": "call-1", "name": "run_sql"})
    store.append(run_id, 1, "tool_finished", {"call_id": "call-1", "name": "run_sql",
                                              "result": result})

    query = RecordingQuery()
    runner = AgentRunner(FinishFromEvidence(), ToolRegistry(query, tmp_path), store)
    state = await runner.run(question, run_id=run_id)

    assert state.status == "completed"
    assert query.calls == []
    assert len([item for item in state.history
                if item.get("type") == "function_call_output"]) == 1
    assert [item["content"] for item in state.history if item.get("role") == "user"] == [question]
    assert store.replay(run_id).history == state.history


@pytest.mark.asyncio
async def test_recovery_finishes_only_missing_tool_result(tmp_path):
    store = EventStore(tmp_path / "events.sqlite3")
    run_id = "missing-result"
    question = "How many trips?"
    call = {"type": "function_call", "call_id": "call-1", "name": "run_sql",
            "arguments": json.dumps({"sql": "SELECT COUNT(*) FROM trips"})}
    store.append(run_id, 0, "run_started", {"question": question})
    store.append(run_id, 1, "loop_started", {})
    store.append(run_id, 1, "tool_call_ready", call)
    store.append(run_id, 1, "model_completed", {"output": [call], "usage": {}})
    store.append(run_id, 1, "tool_started", {"call_id": "call-1", "name": "run_sql"})

    query = RecordingQuery()
    runner = AgentRunner(FinishFromEvidence(), ToolRegistry(query, tmp_path), store)
    state = await runner.run(question, run_id=run_id)

    assert state.status == "completed"
    assert query.calls == ["SELECT COUNT(*) FROM trips"]
    assert len([event for event in store.events(run_id)
                if event["kind"] == "tool_finished"]) == 1


def test_replay_second_started_run_is_running_until_terminal_event(tmp_path):
    store = EventStore(tmp_path / "events.sqlite3")
    store.append("conversation", 0, "run_started", {"question": "first"})
    store.append("conversation", 1, "run_completed", {"answer": "first answer"})
    store.append("conversation", 1, "run_started", {"question": "second"})
    replayed = store.replay("conversation")
    assert replayed.status == "running"
    assert replayed.answer == ""


@pytest.mark.asyncio
async def test_new_episode_closes_abandoned_tool_and_clears_old_answer(tmp_path):
    store = EventStore(tmp_path / "events.sqlite3")
    call = {"type": "function_call", "call_id": "old-call", "name": "run_sql",
            "arguments": json.dumps({"sql": "SELECT COUNT(*) FROM trips"})}
    store.append("conversation", 0, "run_started", {"question": "first"})
    store.append("conversation", 1, "model_completed", {"output": [call], "usage": {}})
    store.append("conversation", 1, "run_failed", {"error_type": "TimeoutError"})
    query = RecordingQuery()
    runner = AgentRunner(CannotVerify(), ToolRegistry(query, tmp_path), store)
    seen = []

    async def on_event(event):
        if event["kind"] == "run_started":
            current = store.get("conversation")
            seen.append((current.status, current.answer))

    state = await runner.run("second", run_id="conversation", callback=on_event)
    assert state.status == "completed"
    assert seen == [("running", "")]
    assert query.calls == []
    assert store.replay("conversation").history == state.history


@pytest.mark.asyncio
async def test_start_callback_failure_clears_active_run(tmp_path):
    store = EventStore(tmp_path / "events.sqlite3")
    runner = AgentRunner(FinishFromEvidence(), ToolRegistry(RecordingQuery(), tmp_path), store)

    async def broken_callback(event):
        raise RuntimeError("UI disconnected")

    with pytest.raises(RuntimeError, match="UI disconnected"):
        await runner.run("question", run_id="callback-failed", callback=broken_callback)
    assert "callback-failed" not in runner.active


def test_legacy_memory_migrates_without_cross_run_visibility(tmp_path):
    path = tmp_path / "old.sqlite3"
    with sqlite3.connect(path) as db:
        db.execute("CREATE TABLE memories(id INTEGER PRIMARY KEY AUTOINCREMENT, "
                   "content TEXT NOT NULL UNIQUE, source_run_id TEXT NOT NULL, "
                   "created_at TEXT NOT NULL)")
        db.execute("INSERT INTO memories(content,source_run_id,created_at) VALUES(?,?,?)",
                   ("only first run", "first", "2025-01-01"))
    store = EventStore(path)
    assert store.memories(source_run_id="first") == ["only first run"]
    assert store.memories(source_run_id="second") == []
    store.add_memory("only first run", "second")
    assert store.memories(source_run_id="second") == ["only first run"]
