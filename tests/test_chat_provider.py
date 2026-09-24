import json
from types import SimpleNamespace

import httpx
import pytest
from openai import APIConnectionError
from openai.types.chat.chat_completion_chunk import ChatCompletionChunk

from trust_agent.chat_provider import ChatCompletionsProvider
from trust_agent.domain import ProviderStreamError


def chunk(delta=None, *, finish=None, usage=None):
    return ChatCompletionChunk.model_validate({
        "id": "chat_1", "object": "chat.completion.chunk", "created": 1,
        "model": "observed-model",
        "choices": [] if delta is None else [{"index": 0, "delta": delta,
                                             "finish_reason": finish}],
        "usage": usage,
    })


class FakeChat:
    def __init__(self, chunks):
        self.chunks = chunks
        self.request = None

    async def create(self, **kwargs):
        self.request = kwargs

        async def events():
            for item in self.chunks:
                yield item

        return events()


@pytest.mark.asyncio
async def test_chat_stream_assembles_multiple_tool_calls_before_dispatch():
    chunks = [
        chunk({"tool_calls": [{"index": 0, "id": "c1", "type": "function",
                               "function": {"name": "get_metric", "arguments": ""}},
                              {"index": 1, "id": "c2", "type": "function",
                               "function": {"name": "run_sql", "arguments": ""}}]}),
        chunk({"tool_calls": [{"index": 0, "function": {"arguments": '{"name":"trip_count"}'}},
                              {"index": 1, "function": {"arguments": '{"sql":"SELECT'} }]}),
        chunk({"tool_calls": [{"index": 1, "function": {"arguments": ' 1"}'}}]}),
        chunk({}, finish="stop"),  # The tested gateway reports stop even for tool calls.
        chunk(usage={"prompt_tokens": 20, "completion_tokens": 10, "total_tokens": 30}),
    ]
    fake = FakeChat(chunks)
    provider = ChatCompletionsProvider("alias", api_key="test-key", base_url="https://example.com/v1")
    provider.client = SimpleNamespace(chat=SimpleNamespace(completions=fake))
    tool = {"type": "function", "name": "get_metric", "description": "metric",
            "parameters": {"type": "object", "properties": {}}, "strict": True}
    events = [event async for event in provider.stream([{"role": "user", "content": "hi"}],
                                                        [tool], "system rules")]
    assert [event.kind for event in events] == ["tool_delta"] * 3 + ["tool_ready"] * 2 + [
        "completed"]
    assert [event.data["call_id"] for event in events if event.kind == "tool_ready"] == ["c1", "c2"]
    assert events[-1].data["usage"] == {"input_tokens": 20, "output_tokens": 10,
                                         "total_tokens": 30}
    assert events[-1].data["model"] == "observed-model"
    assert fake.request["tools"][0]["function"]["name"] == "get_metric"
    assert "name" not in fake.request["tools"][0]
    assert fake.request["stream_options"] == {"include_usage": True}


@pytest.mark.asyncio
async def test_chat_truncation_never_dispatches_tool():
    fake = FakeChat([
        chunk({"tool_calls": [{"index": 0, "id": "c1", "function": {
            "name": "run_sql", "arguments": '{"sql":"SELECT 1"}'}}]}),
        chunk({}, finish="length"),
        chunk(usage={"prompt_tokens": 30, "completion_tokens": 100,
                     "total_tokens": 130}),
    ])
    provider = ChatCompletionsProvider("alias", api_key="test-key", base_url="https://example.com/v1")
    provider.client = SimpleNamespace(chat=SimpleNamespace(completions=fake))
    tool = {"type": "function", "name": "run_sql", "description": "query",
            "parameters": {"type": "object", "properties": {}}}
    events = []
    with pytest.raises(ProviderStreamError, match="length") as captured:
        async for event in provider.stream([], [tool], "system"):
            events.append(event)
    assert not any(event.kind == "tool_ready" for event in events)
    assert captured.value.usage["total_tokens"] == 130
    assert captured.value.model == "observed-model"


def test_history_maps_to_chat_assistant_tool_exchange():
    history = [
        {"role": "user", "content": "count trips"},
        {"type": "message", "role": "assistant", "content": [
            {"type": "output_text", "text": "checking"}]},
        {"type": "function_call", "call_id": "c1", "name": "describe_data", "arguments": "{}"},
        {"type": "function_call", "call_id": "c2", "name": "run_sql",
         "arguments": json.dumps({"sql": "SELECT 1"})},
        {"type": "function_call_output", "call_id": "c1", "output": "{}"},
        {"type": "function_call_output", "call_id": "c2", "output": '{"rows":[[1]]}'},
    ]
    messages = ChatCompletionsProvider.to_chat_messages(history, "rules")
    assert [message["role"] for message in messages] == ["system", "user", "assistant", "tool", "tool"]
    assert messages[2]["content"] == "checking"
    assert [call["id"] for call in messages[2]["tool_calls"]] == ["c1", "c2"]
    assert [message["tool_call_id"] for message in messages[3:]] == ["c1", "c2"]


def test_orphan_tool_result_is_rejected():
    with pytest.raises(ValueError, match="orphan"):
        ChatCompletionsProvider.to_chat_messages([
            {"type": "function_call_output", "call_id": "missing", "output": "{}"},
        ], "rules")


@pytest.mark.asyncio
async def test_chat_stream_without_usage_fails_before_tool_dispatch():
    fake = FakeChat([
        chunk({"tool_calls": [{"index": 0, "id": "c1", "function": {
            "name": "run_sql", "arguments": '{"sql":"SELECT 1"}'}}]}),
        chunk({}, finish="tool_calls"),
    ])
    provider = ChatCompletionsProvider("alias", api_key="test-key", base_url="https://example.com/v1")
    provider.client = SimpleNamespace(chat=SimpleNamespace(completions=fake))
    tool = {"type": "function", "name": "run_sql", "description": "query",
            "parameters": {"type": "object", "properties": {}}}
    events = []
    with pytest.raises(ProviderStreamError, match="token usage"):
        async for event in provider.stream([], [tool], "system"):
            events.append(event)
    assert not any(event.kind == "tool_ready" for event in events)


@pytest.mark.asyncio
async def test_chat_transport_failure_preserves_observed_usage():
    class FailingChat:
        async def create(self, **kwargs):
            async def events():
                yield chunk({"content": "draft"})
                yield chunk(usage={"prompt_tokens": 8, "completion_tokens": 3,
                                   "total_tokens": 11})
                raise APIConnectionError(request=httpx.Request("POST", "https://example.com"))
            return events()

    provider = ChatCompletionsProvider("alias", api_key="test-key",
                                       base_url="https://example.com/v1")
    provider.client = SimpleNamespace(chat=SimpleNamespace(completions=FailingChat()))
    events = []
    with pytest.raises(ProviderStreamError) as caught:
        async for event in provider.stream([], [], "system"):
            events.append(event)
    assert [event.kind for event in events] == ["text_delta"]
    assert caught.value.retryable is True
    assert caught.value.reason == "transport"
    assert caught.value.usage["total_tokens"] == 11


@pytest.mark.asyncio
async def test_chat_summary_returns_usage():
    class FakeSummary:
        async def create(self, **kwargs):
            return SimpleNamespace(
                choices=[SimpleNamespace(message=SimpleNamespace(content="brief"),
                                         finish_reason="stop")],
                usage=SimpleNamespace(model_dump=lambda **_: {
                    "prompt_tokens": 9, "completion_tokens": 2, "total_tokens": 11}),
            )

    provider = ChatCompletionsProvider("alias", api_key="test-key",
                                       base_url="https://example.com/v1")
    provider.client = SimpleNamespace(chat=SimpleNamespace(completions=FakeSummary()))
    result = await provider.summarize([{"role": "user", "content": "test"}])
    assert result.text == "brief"
    assert result.usage == {"input_tokens": 9, "output_tokens": 2,
                            "total_tokens": 11}


@pytest.mark.asyncio
@pytest.mark.parametrize(("finish_reason", "usage"), [
    ("length", {"prompt_tokens": 9, "completion_tokens": 2, "total_tokens": 11}),
    ("stop", None),
])
async def test_chat_summary_rejects_truncation_or_missing_usage(finish_reason, usage):
    class FakeSummary:
        async def create(self, **kwargs):
            return SimpleNamespace(
                choices=[SimpleNamespace(message=SimpleNamespace(content="partial"),
                                         finish_reason=finish_reason)],
                usage=(SimpleNamespace(model_dump=lambda **_: usage) if usage else None),
            )

    provider = ChatCompletionsProvider("alias", api_key="test-key",
                                       base_url="https://example.com/v1")
    provider.client = SimpleNamespace(chat=SimpleNamespace(completions=FakeSummary()))
    with pytest.raises(ProviderStreamError):
        await provider.summarize([{"role": "user", "content": "test"}])
