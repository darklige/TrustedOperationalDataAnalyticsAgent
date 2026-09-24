from __future__ import annotations

from trust_agent.cli import TerminalEventRenderer


async def test_terminal_shows_only_committed_attempt(capsys):
    render = TerminalEventRenderer()
    await render({"kind": "text_delta", "data": {
        "attempt_id": "attempt-1", "provisional": True, "text": "unsupported 300 万单"}})
    assert capsys.readouterr().out == ""

    await render({"kind": "tool_started", "data": {"name": "run_sql", "result": "hidden"}})
    progress = capsys.readouterr().out
    assert "[tool_started]" in progress
    assert "run_sql" in progress
    assert "hidden" not in progress

    await render({"kind": "text_discarded", "data": {
        "attempt_id": "attempt-1", "reason": "missing_evidence"}})
    await render({"kind": "text_delta", "data": {
        "attempt_id": "attempt-2", "provisional": True, "text": "有证据的结论"}})
    assert capsys.readouterr().out == ""
    await render({"kind": "text_committed", "data": {
        "attempt_id": "attempt-2", "text": "有证据的结论"}})
    assert capsys.readouterr().out == "有证据的结论"


async def test_terminal_uses_committed_text_and_discards_failed_buffer(capsys):
    render = TerminalEventRenderer()
    await render({"kind": "text_delta", "data": {
        "attempt_id": "a", "provisional": True, "text": "incomplete"}})
    await render({"kind": "run_failed", "data": {"error": "timeout"}})
    assert "incomplete" not in capsys.readouterr().out

    await render({"kind": "text_delta", "data": {
        "attempt_id": "b", "provisional": True, "text": "partial"}})
    await render({"kind": "text_committed", "data": {
        "attempt_id": "b", "text": "validated answer"}})
    assert capsys.readouterr().out == "validated answer"


async def test_terminal_supports_legacy_text_delta(capsys):
    render = TerminalEventRenderer()
    await render({"kind": "text_delta", "data": {"text": "legacy answer"}})
    assert capsys.readouterr().out == "legacy answer"
