from __future__ import annotations

import json
from collections.abc import AsyncIterator
from typing import Any, Protocol

from .domain import ProviderEvent


class ModelProvider(Protocol):
    def stream(self, messages: list[dict[str, Any]], tools: list[dict[str, Any]],
               instructions: str) -> AsyncIterator[ProviderEvent]: ...

    async def summarize(self, items: list[dict[str, Any]]) -> str: ...


class OpenAIResponsesProvider:
    def __init__(self, model: str, api_key: str | None = None, base_url: str | None = None):
        from openai import AsyncOpenAI

        if not model:
            raise ValueError("TRUST_AGENT_MODEL must be set")
        self.model = model
        self.client = AsyncOpenAI(api_key=api_key, base_url=base_url)

    async def stream(self, messages: list[dict[str, Any]], tools: list[dict[str, Any]],
                     instructions: str) -> AsyncIterator[ProviderEvent]:
        stream = await self.client.responses.create(
            model=self.model, input=messages, instructions=instructions,
            tools=tools, stream=True, store=False,
        )
        ready_ids: set[str] = set()
        async for event in stream:
            if event.type == "response.output_text.delta":
                yield ProviderEvent("text_delta", {"text": event.delta})
            elif event.type == "response.function_call_arguments.delta":
                yield ProviderEvent("tool_delta", {"index": event.output_index,
                                                   "delta": event.delta})
            elif event.type == "response.output_item.done":
                item = event.item
                if item.type == "function_call" and item.status != "incomplete" and \
                        item.call_id not in ready_ids:
                    ready_ids.add(item.call_id)
                    yield ProviderEvent("tool_ready", {"call_id": item.call_id,
                                                       "name": item.name,
                                                       "arguments": item.arguments})
            elif event.type == "response.completed":
                response = event.response
                output = [item.model_dump(exclude_none=True) for item in response.output]
                for item in output:
                    if item.get("type") == "function_call" and \
                            item.get("status") != "incomplete" and \
                            item.get("call_id") not in ready_ids:
                        ready_ids.add(item["call_id"])
                        yield ProviderEvent("tool_ready", {"call_id": item["call_id"],
                                                           "name": item["name"],
                                                           "arguments": item["arguments"]})
                usage = response.usage.model_dump(exclude_none=True) if response.usage else {}
                yield ProviderEvent("completed", {"output": output, "usage": usage,
                                                  "response_id": response.id})
            elif event.type in {"response.failed", "error", "response.incomplete"}:
                raise RuntimeError(f"model stream ended with {event.type}")

    async def summarize(self, items: list[dict[str, Any]]) -> str:
        response = await self.client.responses.create(
            model=self.model,
            instructions=("Summarize the prior conversation for future task continuity. "
                          "Preserve user constraints, metric definitions, source IDs, unresolved questions "
                          "and correction history. Do not invent facts. Output plain text under 1200 words."),
            input=json.dumps(items, ensure_ascii=False, default=str),
            store=False,
        )
        return response.output_text
