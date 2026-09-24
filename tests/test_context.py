import json

import pytest

from trust_agent.context import ContextBuilder
from trust_agent.domain import RunState


class SummaryProvider:
    async def summarize(self, items):
        return "Earlier query established the January count."


@pytest.mark.asyncio
async def test_compaction_preserves_full_history_and_evidence_pointer():
    state = RunState("r1", history=[
        {"role": "user", "content": "Count January trips"},
        {"type": "function_call", "call_id": "c1", "name": "run_sql", "arguments": "{}"},
        {"type": "function_call_output", "call_id": "c1",
         "output": json.dumps({"query_id": "q123", "rows": [[100]]})},
        {"role": "user", "content": "Explain the number"},
    ])
    original = list(state.history)
    view = await ContextBuilder(char_budget=10, keep_recent=1).build(state, SummaryProvider())
    assert view.compacted
    assert state.history == original
    assert "q123" in state.summary
    assert view.messages[-1] == original[-1]


@pytest.mark.asyncio
async def test_compaction_counts_existing_summary_and_reduces_recent_window():
    state = RunState("r2", summary="Earlier summary " * 20,
                     history=[{"role": "user", "content": "x" * 350} for _ in range(20)] +
                             [{"role": "user", "content": "current question"}])
    original = list(state.history)
    view = await ContextBuilder(char_budget=2_000, keep_recent=10).build(
        state, SummaryProvider()
    )
    assert view.compacted
    assert view.chars <= 2_000
    assert view.messages[-1] == original[-1]
    assert state.history == original
