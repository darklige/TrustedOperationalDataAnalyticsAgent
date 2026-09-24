from fastapi.testclient import TestClient
from test_loop import FakeQuery, ScriptedProvider

from trust_agent.api import create_app
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
