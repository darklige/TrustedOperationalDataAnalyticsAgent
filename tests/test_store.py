import pytest

from trust_agent.store import EventStore


def test_legacy_commit_without_completed_event_replays_as_terminal(tmp_path):
    store = EventStore(tmp_path / "state.db")
    store.append("run", 0, "run_started", {"question": "第一问"})
    store.append("run", 0, "text_committed", {
        "attempt_id": "one", "text": "已验证答案", "stop_reason": "query_evidence"})
    replayed = store.replay("run")
    assert replayed is not None
    assert replayed.status == "completed"
    assert replayed.answer == "已验证答案"
    assert replayed.stop_reason == "query_evidence"


def test_terminal_pair_rolls_back_together_on_write_error(tmp_path):
    store = EventStore(tmp_path / "state.db")
    recursive = {}
    recursive["self"] = recursive
    with pytest.raises(ValueError):
        store.append_many("run", 0, [
            ("text_committed", {"text": "answer"}),
            ("run_completed", recursive),
        ])
    assert store.events("run") == []
