import json

import pytest

from trust_agent.context import ContextBuilder
from trust_agent.domain import RunState


class SummaryProvider:
    def __init__(self):
        self.items = None

    async def summarize(self, items):
        self.items = items
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
    assert "q123" in json.dumps(view.messages)
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


@pytest.mark.asyncio
async def test_compaction_keeps_multi_tool_batch_atomic_for_chat_protocol():
    from trust_agent.chat_provider import ChatCompletionsProvider

    history = [
        {"role": "user", "content": "x" * 1000},
        {"type": "function_call", "call_id": "c1", "name": "describe_data", "arguments": "{}"},
        {"type": "function_call", "call_id": "c2", "name": "run_sql",
         "arguments": '{"sql":"SELECT 1"}'},
        {"type": "function_call_output", "call_id": "c1", "output": "{}"},
        {"type": "function_call_output", "call_id": "c2", "output": '{"rows":[[1]]}'},
        {"role": "user", "content": "now explain"},
    ]
    state = RunState("multi", history=list(history))
    view = await ContextBuilder(char_budget=500, keep_recent=2).build(state, SummaryProvider())
    assert view.compacted
    assert state.compacted_until not in {2, 3, 4}
    ChatCompletionsProvider.to_chat_messages(view.messages, "rules")
    assert state.history == history


@pytest.mark.asyncio
async def test_layered_output_preserves_evidence_and_latest_result_without_summarizing():
    from trust_agent.chat_provider import ChatCompletionsProvider

    history = [{"role": "user", "content": "Compare these counts"}]
    for number in range(5):
        call_id = f"c{number}"
        history.extend([
            {"type": "function_call", "call_id": call_id, "name": "run_sql",
             "arguments": '{"sql":"SELECT count(*) FROM trips"}'},
            {"type": "function_call_output", "call_id": call_id,
             "output": json.dumps({"query_id": f"q{number}", "dataset_version": "v1",
                                   "result_sha256": f"hash{number}",
                                   "sql": "SELECT count(*) FROM trips", "row_count": 100,
                                   "columns": ["count"],
                                   "rows": [[number], *[[n] for n in range(50)]]})},
        ])
    history.append({"role": "user", "content": "Explain the latest count"})
    original = json.loads(json.dumps(history))
    provider = SummaryProvider()
    raw_chars = ContextBuilder._chars(history)
    view = await ContextBuilder(char_budget=raw_chars - 1, keep_recent_results=2).build(
        RunState("long", history=history), provider)
    assert provider.items is None  # The cheaper layer absorbed the pressure.
    assert view.layered_outputs == 3
    assert view.compacted and not view.budget_exceeded
    assert view.chars <= raw_chars - 1
    assert history == original
    assert view.messages[-1] == original[-1]
    old = json.loads(view.messages[2]["output"])
    assert old["query_id"] == "q0"
    assert old["dataset_version"] == "v1"
    assert old["result_sha256"] == "hash0"
    assert old["row_count"] == 100
    assert old["sql"] == "SELECT count(*) FROM trips"
    assert old["rows"][0] == [0]
    assert old["omitted_rows"] == 48
    assert view.messages[-2]["output"] == original[-2]["output"]
    ChatCompletionsProvider.to_chat_messages(view.messages, "rules")


@pytest.mark.asyncio
async def test_summarizer_receives_layered_history_and_recent_question_survives():
    history = [{"role": "user", "content": "January analysis"}]
    for number in range(12):
        call_id = f"c{number}"
        history += [
            {"type": "function_call", "call_id": call_id, "name": "run_sql",
             "arguments": '{"sql":"SELECT 1"}'},
            {"type": "function_call_output", "call_id": call_id,
             "output": json.dumps({"query_id": f"q{number}", "dataset_version": "v1",
                                   "result_sha256": f"hash{number}", "sql": "SELECT 1",
                                   "row_count": 50, "rows": [[number], *[[n] for n in range(30)]]})},
        ]
    history.append({"role": "user", "content": "Why did the last count change?"})
    original = json.loads(json.dumps(history))
    state = RunState("longer", history=history)
    provider = SummaryProvider()
    view = await ContextBuilder(char_budget=1_200, keep_recent=3).build(state, provider)
    assert provider.items is not None
    old_outputs = [item for item in provider.items
                   if item.get("type") == "function_call_output"]
    assert old_outputs
    assert all(len(json.loads(item["output"])["rows"]) <= 3 for item in old_outputs)
    assert "q0" in state.summary
    assert view.messages[-1] == original[-1]
    assert view.chars <= 1_200
    assert state.history == original


@pytest.mark.asyncio
async def test_unavoidable_budget_overflow_keeps_the_current_question():
    question = {"role": "user", "content": "current task " * 200}
    state = RunState("overflow", history=[question])
    view = await ContextBuilder(char_budget=100).build(state, SummaryProvider())
    assert view.budget_exceeded
    assert view.messages == [question]
    assert state.history == [question]
