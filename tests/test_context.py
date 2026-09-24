import json

import pytest

from trust_agent.context import ContextBuilder
from trust_agent.domain import RunState
from trust_agent.token_budget import estimate_input_tokens


class SummaryProvider:
    def __init__(self):
        self.items = None

    async def summarize(self, items):
        self.items = items
        return "Earlier query established the January count."


class MeteredSummaryProvider(SummaryProvider):
    async def summarize(self, items):
        from trust_agent.domain import SummaryResult

        self.items = items
        return SummaryResult("Earlier query established the January count.",
                             {"input_tokens": 200, "output_tokens": 20,
                              "total_tokens": 220})


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
async def test_summary_usage_is_returned_for_total_budget_accounting():
    state = RunState("summary-metered", history=[
        {"role": "user", "content": "old" * 400},
        {"role": "user", "content": "current question"},
    ])
    view = await ContextBuilder(char_budget=600, keep_recent=1).build(
        state, MeteredSummaryProvider())
    assert view.summary_usage == {"input_tokens": 200, "output_tokens": 20,
                                  "total_tokens": 220}


@pytest.mark.asyncio
async def test_unavoidable_budget_overflow_keeps_the_current_question():
    question = {"role": "user", "content": "current task " * 200}
    state = RunState("overflow", history=[question])
    view = await ContextBuilder(char_budget=100).build(state, SummaryProvider())
    assert view.budget_exceeded
    assert view.messages == [question]
    assert state.history == [question]


@pytest.mark.asyncio
async def test_input_token_budget_reserves_output_and_counts_tools():
    provider = SummaryProvider()
    question = {"role": "user", "content": "Count monthly trips"}
    tools = [{"name": "run_sql", "description": "a" * 600,
              "parameters": {"type": "object", "properties": {}}}]
    state = RunState("token-budget", history=[question])
    builder = ContextBuilder(char_budget=10_000, model_context_tokens=900,
                             output_reserve_tokens=300, token_safety_margin=100)
    assert builder.input_token_budget == 500
    view = await builder.build(state, provider, instructions="Rules", tools=tools)
    assert view.budget_exceeded
    assert view.estimated_input_tokens > view.input_token_budget
    assert state.history == [question]
    assert view.messages == [question]


@pytest.mark.asyncio
async def test_layered_context_fits_token_budget_without_mutating_trace():
    history = [{"role": "user", "content": "Compare monthly groups"}]
    for number in range(4):
        history += [
            {"type": "function_call", "call_id": f"c{number}", "name": "run_sql",
             "arguments": "{}"},
            {"type": "function_call_output", "call_id": f"c{number}",
             "output": json.dumps({"query_id": f"q{number}", "dataset_version": "v1",
                                   "result_sha256": f"hash{number}", "sql": "SELECT 1",
                                   "row_count": 100,
                                   "rows": [[n] for n in range(100)]})},
        ]
    history.append({"role": "user", "content": "Explain latest"})
    original = json.loads(json.dumps(history))
    provider = SummaryProvider()
    full_tokens = estimate_input_tokens(provider, history, [], "Rules")
    builder = ContextBuilder(char_budget=100_000,
                             model_context_tokens=full_tokens + 512 + 100,
                             output_reserve_tokens=512, token_safety_margin=100)
    builder.input_token_budget = full_tokens - 1
    state = RunState("token-layer", history=history)
    view = await builder.build(state, provider, instructions="Rules")
    assert view.layered_outputs == 2
    assert view.estimated_input_tokens <= view.input_token_budget
    assert view.chars < builder.char_budget
    assert state.history == original
    assert provider.items is None


@pytest.mark.asyncio
async def test_long_history_summarizes_in_bounded_chunks_and_accounts_each_call():
    from trust_agent.domain import SummaryResult

    class ChunkProvider:
        def __init__(self):
            self.chunks = []

        async def summarize(self, items):
            self.chunks.append(items)
            return SummaryResult("Earlier context summarized.", {
                "input_tokens": 100, "output_tokens": 10, "total_tokens": 110})

    provider = ChunkProvider()
    history = [{"role": "user", "content": f"old-{number}:" + "x" * 450}
               for number in range(16)]
    history.append({"role": "user", "content": "current question"})
    original = json.loads(json.dumps(history))
    state = RunState("chunks", history=history)
    usages = []

    async def record(usage):
        usages.append(usage)

    builder = ContextBuilder(char_budget=700, keep_recent=1,
                             model_context_tokens=3_000,
                             output_reserve_tokens=512, token_safety_margin=100)
    view = await builder.build(state, provider, on_summary_usage=record)
    assert len(provider.chunks) > 1
    assert len(usages) == len(provider.chunks)
    assert view.summary_usage["total_tokens"] == 110 * len(provider.chunks)
    assert all(estimate_input_tokens(provider, [{"role": "user", "content": json.dumps(
        chunk, ensure_ascii=False)}], [], "Summarize prior context") + 1024 <=
        builder.input_token_budget for chunk in provider.chunks)
    assert view.messages[-1] == original[-1]
    assert state.history == original
