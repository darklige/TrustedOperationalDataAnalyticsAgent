import json
import shutil
import subprocess
import threading
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from test_loop import FakeQuery, ScriptedProvider

from trust_agent.api import create_app
from trust_agent.domain import RunState
from trust_agent.loop import AgentRunner
from trust_agent.store import EventStore
from trust_agent.tools import ToolRegistry


def test_sse_replay_after_disconnect(tmp_path):
    store = EventStore(tmp_path / "state.db")
    runner = AgentRunner(ScriptedProvider(), ToolRegistry(FakeQuery(), tmp_path), store)
    with TestClient(create_app(runner)) as client:
        started = client.post("/runs", json={"question": "count trips"})
        assert started.status_code == 202
        run_id = started.json()["run_id"]
        first = client.get(f"/runs/{run_id}/events")
        assert first.status_code == 200
        lines = [line for line in first.text.splitlines() if line.startswith("id: ")]
        assert len(lines) > 3
        cursor = int(lines[2].split()[1])
        rest = client.get(f"/runs/{run_id}/events?after={cursor}")
        rest_ids = [int(line.split()[1]) for line in rest.text.splitlines()
                    if line.startswith("id: ")]
        assert rest_ids and all(seq > cursor for seq in rest_ids)
        assert client.get(f"/runs/{run_id}").json()["status"] == "completed"


def _sse_events(body: str) -> list[dict]:
    return [json.loads(line.removeprefix("data: ")) for line in body.splitlines()
            if line.startswith("data: ")]


def test_sse_replay_preserves_draft_commit_contract_and_last_event_id(tmp_path):
    store = EventStore(tmp_path / "state.db")
    runner = AgentRunner(ScriptedProvider(), ToolRegistry(FakeQuery(), tmp_path), store)
    with TestClient(create_app(runner)) as client:
        run_id = client.post("/runs", json={"question": "count trips"}).json()["run_id"]
        events = _sse_events(client.get(f"/runs/{run_id}/events").text)
        assert events
        assert [event["seq"] for event in events] == sorted(event["seq"] for event in events)
        draft = next(event for event in events if event["kind"] == "text_delta")
        commit = next(event for event in events if event["kind"] == "text_committed")
        completed = next(event for event in events if event["kind"] == "run_completed")
        assert draft["data"]["provisional"] is True
        assert draft["data"]["attempt_id"] == commit["data"]["attempt_id"]
        assert commit["data"]["text"] == completed["data"]["answer"]
        assert commit["data"]["attempt_id"] == completed["data"]["attempt_id"]
        cutoff = draft["seq"]
        replay = _sse_events(client.get(f"/runs/{run_id}/events?after={cutoff - 1}",
                                        headers={"Last-Event-ID": str(cutoff)}).text)
        assert replay and replay[0]["seq"] > cutoff
        assert [item["seq"] for item in replay] == [item["seq"] for item in events
                                                     if item["seq"] > cutoff]


def test_ui_routes_and_javascript_state_transitions():
    with TestClient(create_app()) as client:
        page = client.get("/ui")
        script = client.get("/ui/app.mjs")
    assert page.status_code == 200
    assert "/ui/app.mjs" in page.text
    assert script.status_code == 200
    assert script.headers["content-type"].startswith("text/javascript")
    node = shutil.which("node")
    if node is None:
        pytest.skip("Node.js is needed for the browser controller state tests")
    test_file = Path(__file__).with_name("test_web_ui.mjs")
    result = subprocess.run([node, "--test", str(test_file)], capture_output=True,
                            text=True, check=False, timeout=20)
    assert result.returncode == 0, result.stdout + result.stderr


def test_sse_waits_for_terminal_event_after_snapshot_is_completed(tmp_path):
    store = EventStore(tmp_path / "state.db")
    runner = AgentRunner(ScriptedProvider(), ToolRegistry(FakeQuery(), tmp_path), store)
    run_id = "terminal-race"
    state = RunState(run_id)
    state.status = "completed"
    state.answer = "verified"
    store.save(state)
    store.append(run_id, 0, "run_started", {"question": "test"})

    def append_terminal() -> None:
        time.sleep(0.05)
        store.append(run_id, 0, "text_committed", {
            "attempt_id": "a1", "text": "verified"})
        store.append(run_id, 0, "run_completed", {
            "attempt_id": "a1", "answer": "verified"})

    thread = threading.Thread(target=append_terminal)
    thread.start()
    with TestClient(create_app(runner)) as client:
        events = _sse_events(client.get(f"/runs/{run_id}/events").text)
    thread.join(timeout=1)
    assert [event["kind"] for event in events] == [
        "run_started", "text_committed", "run_completed"]


def test_sse_replay_does_not_stop_at_previous_episode_completion(tmp_path):
    store = EventStore(tmp_path / "state.db")
    runner = AgentRunner(ScriptedProvider(), ToolRegistry(FakeQuery(), tmp_path), store)
    run_id = "two-episodes"
    state = RunState(run_id)
    state.status = "running"
    store.save(state)
    store.append(run_id, 0, "run_started", {"question": "first"})
    store.append(run_id, 0, "run_completed", {"answer": "first answer"})
    store.append(run_id, 1, "run_started", {"question": "second"})

    def append_second_terminal() -> None:
        time.sleep(0.05)
        state.status = "completed"
        state.answer = "second answer"
        store.save(state)
        store.append(run_id, 1, "text_committed", {
            "attempt_id": "a2", "text": "second answer"})
        store.append(run_id, 1, "run_completed", {
            "attempt_id": "a2", "answer": "second answer"})

    thread = threading.Thread(target=append_second_terminal)
    thread.start()
    with TestClient(create_app(runner)) as client:
        events = _sse_events(client.get(f"/runs/{run_id}/events").text)
    thread.join(timeout=1)
    assert [event["kind"] for event in events] == [
        "run_started", "run_completed", "run_started", "text_committed", "run_completed"]
