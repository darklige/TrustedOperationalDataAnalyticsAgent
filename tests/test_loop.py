import asyncio
import json

import pytest

from trust_agent.domain import ProviderEvent, ProviderStreamError
from trust_agent.loop import AgentRunner
from trust_agent.store import EventStore
from trust_agent.tools import ToolRegistry


class FakeQuery:
    def __init__(self):
        self.calls = []

    def schema(self):
        return {"trips": ["trip_id", "fare_amount"]}

    def query(self, sql):
        self.calls.append(sql)
        return {"columns": ["count"], "rows": [[3]], "row_count": 1,
                "truncated": False, "sql": sql}


class ScriptedProvider:
    def __init__(self):
        self.turns = 0

    async def summarize(self, items):
        return "Earlier user asked about the trip count."

    async def stream(self, messages, tools, instructions):
        self.turns += 1
        if self.turns == 1:
            call = {"type": "function_call", "call_id": "c1", "name": "describe_data",
                    "arguments": "{}"}
            yield ProviderEvent("tool_delta", {"index": 0, "delta": "{"})
            yield ProviderEvent("tool_ready", call)
            yield ProviderEvent("completed", {"output": [call], "usage": {}})
        elif self.turns == 2:
            args = json.dumps({"sql": "SELECT COUNT(*) FROM trips"})
            call = {"type": "function_call", "call_id": "c2", "name": "run_sql",
                    "arguments": args}
            yield ProviderEvent("tool_delta", {"index": 0, "delta": args[:10]})
            yield ProviderEvent("tool_delta", {"index": 0, "delta": args[10:]})
            yield ProviderEvent("tool_ready", call)
            yield ProviderEvent("completed", {"output": [call], "usage": {}})
        else:
            result = next(json.loads(item["output"]) for item in messages
                          if item.get("type") == "function_call_output"
                          and "query_id" in item["output"])
            answer = f"共有 3 条。[query_id:{result['query_id']}]"
            yield ProviderEvent("text_delta", {"text": answer})
            yield ProviderEvent("completed", {"output": [{"type": "message", "role": "assistant",
                "content": [{"type": "output_text", "text": answer}]}], "usage": {}})


@pytest.mark.asyncio
async def test_loop_tools_evidence_and_replay(tmp_path):
    query = FakeQuery()
    store = EventStore(tmp_path / "state.db")
    runner = AgentRunner(ScriptedProvider(), ToolRegistry(query, tmp_path), store)
    state = await runner.run("有多少条行程？")
    assert state.status == "completed"
    assert state.turn == 3
    assert query.calls == ["SELECT COUNT(*) FROM trips"]
    events = store.events(state.run_id)
    assert [event["seq"] for event in events] == sorted(event["seq"] for event in events)
    assert [event["kind"] for event in events].count("tool_finished") == 2
    assert events[-1]["kind"] == "run_completed"
    assert store.get(state.run_id).answer == state.answer
    rebuilt = store.replay(state.run_id)
    assert rebuilt.status == state.status
    assert rebuilt.answer == state.answer
    assert rebuilt.turn == state.turn
    assert rebuilt.history == state.history
    next_state = await runner.run("再确认一次", run_id=state.run_id)
    assert next_state.status == "completed"
    replayed = store.replay(state.run_id)
    assert [item["content"] for item in replayed.history if item.get("role") == "user"] == [
        "有多少条行程？", "再确认一次"]


class IncompleteProvider:
    async def summarize(self, items):
        return "summary"

    async def stream(self, messages, tools, instructions):
        yield ProviderEvent("tool_delta", {"index": 0, "delta": '{"sql":"SELECT'})
        # Simulates a broken/cancelled stream: no completed tool or response.


@pytest.mark.asyncio
async def test_partial_tool_json_is_never_executed(tmp_path):
    query = FakeQuery()
    store = EventStore(tmp_path / "state.db")
    runner = AgentRunner(IncompleteProvider(), ToolRegistry(query, tmp_path), store)
    state = await runner.run("count trips")
    assert state.status == "failed"
    assert query.calls == []
    assert store.events(state.run_id)[-1]["kind"] == "run_failed"


class OverlapProvider:
    def __init__(self):
        self.calls = 0

    async def summarize(self, items):
        return "summary"

    async def stream(self, messages, tools, instructions):
        self.calls += 1
        if self.calls == 1:
            call = {"type": "function_call", "call_id": "overlap", "name": "run_sql",
                    "arguments": json.dumps({"sql": "SELECT COUNT(*) FROM trips"})}
            yield ProviderEvent("tool_ready", call)
            await asyncio.sleep(0.05)
            yield ProviderEvent("completed", {"output": [call], "usage": {}})
        else:
            query_id = next(json.loads(item["output"])["query_id"] for item in messages
                            if item.get("type") == "function_call_output")
            answer = f"3 rows [query_id:{query_id}]"
            yield ProviderEvent("text_delta", {"text": answer})
            yield ProviderEvent("completed", {"output": [], "usage": {}})


@pytest.mark.asyncio
async def test_completed_tool_call_starts_while_model_stream_is_open(tmp_path):
    store = EventStore(tmp_path / "state.db")
    runner = AgentRunner(OverlapProvider(), ToolRegistry(FakeQuery(), tmp_path), store)
    state = await runner.run("count")
    kinds = [event["kind"] for event in store.events(state.run_id)]
    assert state.status == "completed"
    assert kinds.index("tool_started") < kinds.index("model_completed")


@pytest.mark.asyncio
async def test_tool_budget_stops_before_execution(tmp_path):
    query = FakeQuery()
    store = EventStore(tmp_path / "state.db")
    runner = AgentRunner(OverlapProvider(), ToolRegistry(query, tmp_path), store,
                         max_tool_calls=0)
    state = await runner.run("count")
    assert state.status == "budget_exceeded"
    assert query.calls == []


@pytest.mark.asyncio
async def test_explicit_memory_persists_after_short_run(tmp_path):
    store = EventStore(tmp_path / "state.db")
    runner = AgentRunner(ScriptedProvider(), ToolRegistry(FakeQuery(), tmp_path), store)
    await runner.run("请记住：以后用纽约本地时间分析")
    assert "以后用纽约本地时间分析" in store.memories()


def test_large_tool_result_keeps_evidence_id_in_model_view():
    result = {"columns": ["long_text"], "rows": [["x" * 10_000]],
              "row_count": 1, "query_id": "q123", "dataset_version": "v1",
              "result_sha256": "abc"}
    view = json.loads(AgentRunner._model_result_view(result))
    assert view["query_id"] == "q123"
    assert len(json.dumps(view)) < 6_000


@pytest.mark.parametrize("answer", [
    "无法核实。但共有 3051046 条行程。",
    "无法核实，订单上涨了 3.05%。",
    "无法核实。收入约 305 万元。",
    "无法核实，但共有三百万条行程。",
    "无法核实，但增长百分之三十。",
    "请澄清口径；该月为 86.5%。",
])
def test_abstention_cannot_launder_numeric_claim_without_query(answer):
    assert not AgentRunner._answer_has_evidence(answer, [])


@pytest.mark.parametrize("answer", [
    "无法核实该结果。",
    "无法核实 2025-03 的数据，因为当前数据集未覆盖该月份。",
    "无法核实 2025 年 3 月的数据；请补充该月份的来源。",
])
def test_pure_abstention_without_query_is_allowed(answer):
    assert AgentRunner._answer_has_evidence(answer, [])


class UnsupportedThenRefusalProvider:
    def __init__(self):
        self.turns = 0

    async def summarize(self, items):
        return "summary"

    async def stream(self, messages, tools, instructions):
        self.turns += 1
        answer = ("无法核实，但共有 3 条行程。" if self.turns == 1 else
                  "无法核实该结果，请补充数据来源。")
        yield ProviderEvent("text_delta", {"text": answer})
        yield ProviderEvent("completed", {"output": [{"type": "message", "role": "assistant",
            "content": [{"type": "output_text", "text": answer}]}], "usage": {}})


@pytest.mark.asyncio
async def test_numeric_abstention_is_rejected_then_pure_refusal_completes(tmp_path):
    query = FakeQuery()
    store = EventStore(tmp_path / "state.db")
    provider = UnsupportedThenRefusalProvider()
    runner = AgentRunner(provider, ToolRegistry(query, tmp_path), store)
    state = await runner.run("有多少条行程？")
    assert state.status == "completed"
    assert state.turn == 2
    assert state.answer == "无法核实该结果，请补充数据来源。"
    assert query.calls == []
    assert [event["kind"] for event in store.events(state.run_id)].count("answer_rejected") == 1


class TokenHeavyProvider:
    async def summarize(self, items):
        return "summary"

    async def stream(self, messages, tools, instructions):
        yield ProviderEvent("text_delta", {"text": "无法核实"})
        yield ProviderEvent("completed", {"output": [], "usage": {"total_tokens": 11}})


@pytest.mark.asyncio
async def test_token_budget_is_terminal(tmp_path):
    store = EventStore(tmp_path / "state.db")
    runner = AgentRunner(TokenHeavyProvider(), ToolRegistry(FakeQuery(), tmp_path), store,
                         max_total_tokens=10)
    state = await runner.run("answer")
    assert state.status == "budget_exceeded"
    assert store.events(state.run_id)[-1]["data"]["error_type"] == "BudgetExceeded"


class TruncatedProvider:
    async def summarize(self, items):
        return "summary"

    async def stream(self, messages, tools, instructions):
        raise ProviderStreamError("truncated", usage={"input_tokens": 10,
            "output_tokens": 100, "total_tokens": 110}, model="actual-model")
        yield  # pragma: no cover - keep the method an async generator


@pytest.mark.asyncio
async def test_failed_stream_persists_reported_usage(tmp_path):
    store = EventStore(tmp_path / "state.db")
    runner = AgentRunner(TruncatedProvider(), ToolRegistry(FakeQuery(), tmp_path), store)
    state = await runner.run("count")
    assert state.status == "failed"
    failed = [event for event in store.events(state.run_id) if event["kind"] == "model_failed"]
    assert len(failed) == 1
    assert failed[0]["data"]["usage"]["total_tokens"] == 110
    assert failed[0]["data"]["model"] == "actual-model"
