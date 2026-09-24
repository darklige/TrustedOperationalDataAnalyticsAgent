from types import SimpleNamespace

import pytest
from openai.types.responses import (
    ResponseFunctionCallArgumentsDoneEvent,
    ResponseOutputItemDoneEvent,
)

from trust_agent.provider import OpenAIResponsesProvider


class Dumpable:
    def __init__(self, value):
        self.value = value

    def model_dump(self, **kwargs):
        return self.value


class FakeResponses:
    def __init__(self, *, item_done=True):
        self.item_done = item_done

    async def create(self, **kwargs):
        assert kwargs["stream"] is True
        assert kwargs["store"] is False

        async def events():
            yield SimpleNamespace(type="response.output_text.delta", delta="查到结果")
            yield SimpleNamespace(type="response.function_call_arguments.delta",
                                  output_index=0, delta='{"sql":')
            yield ResponseFunctionCallArgumentsDoneEvent.model_validate({
                "type": "response.function_call_arguments.done",
                "item_id": "fc_1", "output_index": 0, "sequence_number": 3,
                "name": "run_sql", "arguments": '{"sql":"SELECT 1"}',
            })
            if self.item_done:
                yield ResponseOutputItemDoneEvent.model_validate({
                    "type": "response.output_item.done", "output_index": 0,
                    "sequence_number": 4,
                    "item": {"type": "function_call", "id": "fc_1", "call_id": "c1",
                             "name": "run_sql", "arguments": '{"sql":"SELECT 1"}',
                             "status": "completed"},
                })
            yield SimpleNamespace(type="response.completed", response=SimpleNamespace(
                id="resp1", model="test-model", output=[Dumpable({"type": "function_call", "call_id": "c1",
                                               "name": "run_sql",
                                               "arguments": '{"sql":"SELECT 1"}'})],
                usage=Dumpable({"total_tokens": 12})))

        return events()


@pytest.mark.asyncio
async def test_responses_stream_normalizes_completed_tool_call():
    provider = OpenAIResponsesProvider("test-model", api_key="test-key")
    provider.client = SimpleNamespace(responses=FakeResponses())
    events = [event async for event in provider.stream([{"role": "user", "content": "hi"}],
                                                        [], "test")]
    assert [event.kind for event in events] == [
        "text_delta", "tool_delta", "tool_ready", "completed"]
    assert events[2].data == {"call_id": "c1", "name": "run_sql",
                              "arguments": '{"sql":"SELECT 1"}'}
    assert events[-1].data["usage"]["total_tokens"] == 12


@pytest.mark.asyncio
async def test_completed_response_recovers_call_if_item_done_event_was_missing():
    provider = OpenAIResponsesProvider("test-model", api_key="test-key")
    provider.client = SimpleNamespace(responses=FakeResponses(item_done=False))
    events = [event async for event in provider.stream([], [], "test")]
    assert [event.kind for event in events][-2:] == ["tool_ready", "completed"]
    assert events[-2].data["call_id"] == "c1"
