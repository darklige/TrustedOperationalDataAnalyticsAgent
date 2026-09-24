from types import SimpleNamespace

import pytest

from trust_agent.provider import OpenAIResponsesProvider


class Dumpable:
    def __init__(self, value):
        self.value = value

    def model_dump(self, **kwargs):
        return self.value


class FakeResponses:
    async def create(self, **kwargs):
        assert kwargs["stream"] is True
        assert kwargs["store"] is False

        async def events():
            yield SimpleNamespace(type="response.output_text.delta", delta="查到结果")
            yield SimpleNamespace(type="response.function_call_arguments.delta",
                                  output_index=0, delta='{"sql":')
            yield SimpleNamespace(type="response.function_call_arguments.done",
                                  item=SimpleNamespace(call_id="c1", name="run_sql",
                                                       arguments='{"sql":"SELECT 1"}'))
            yield SimpleNamespace(type="response.completed", response=SimpleNamespace(
                id="resp1", output=[Dumpable({"type": "function_call", "call_id": "c1",
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
