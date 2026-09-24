from __future__ import annotations

import json
from collections.abc import AsyncIterator
from typing import Any, Protocol

from openai import APIConnectionError, APITimeoutError, InternalServerError, RateLimitError

from .domain import ProviderEvent, ProviderStreamError, SummaryResult

_TRANSIENT_ERRORS = (APIConnectionError, APITimeoutError, InternalServerError, RateLimitError)


class ModelProvider(Protocol):
    def stream(self, messages: list[dict[str, Any]], tools: list[dict[str, Any]],
               instructions: str) -> AsyncIterator[ProviderEvent]: ...

    async def summarize(self, items: list[dict[str, Any]]) -> str | SummaryResult: ...


class OpenAIResponsesProvider:
    def __init__(self, model: str, api_key: str | None = None, base_url: str | None = None,
                 max_output_tokens: int = 2_048):
        from openai import AsyncOpenAI

        if not model:
            raise ValueError("TRUST_AGENT_MODEL must be set")
        if max_output_tokens <= 0:
            raise ValueError("max_output_tokens must be positive")
        self.model = model
        self.max_output_tokens = max_output_tokens
        self.client = AsyncOpenAI(api_key=api_key, base_url=base_url)

    async def stream(self, messages: list[dict[str, Any]], tools: list[dict[str, Any]],
                     instructions: str) -> AsyncIterator[ProviderEvent]:
        ready_ids: set[str] = set()
        observed: dict[str, Any] = {"usage": {}, "model": None, "response_id": None}
        async for event in self._events(messages, tools, instructions, observed):
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
                observed.update({"usage": (response.usage.model_dump(exclude_none=True)
                                           if response.usage else {}),
                                 "model": response.model, "response_id": response.id})
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
                                                  "response_id": response.id,
                                                  "model": response.model})
            elif event.type in {"response.failed", "error", "response.incomplete"}:
                response = getattr(event, "response", None)
                usage = (response.usage.model_dump(exclude_none=True)
                         if response and getattr(response, "usage", None) else observed["usage"])
                raise ProviderStreamError(
                    f"model stream ended with {event.type}", usage=usage,
                    model=getattr(response, "model", None) or observed["model"],
                    response_id=getattr(response, "id", None) or observed["response_id"],
                    retryable=event.type == "response.incomplete",
                    reason="truncated" if event.type == "response.incomplete" else "provider_failed")

    async def _events(self, messages: list[dict[str, Any]], tools: list[dict[str, Any]],
                      instructions: str, observed: dict[str, Any]):
        try:
            stream = await self.client.responses.create(
                model=self.model, input=messages, instructions=instructions,
                tools=tools, stream=True, store=False,
                max_output_tokens=self.max_output_tokens,
            )
            async for event in stream:
                yield event
        except _TRANSIENT_ERRORS as exc:
            raise ProviderStreamError(
                f"transient Responses transport error: {type(exc).__name__}",
                usage=observed["usage"], model=observed["model"],
                response_id=observed["response_id"], retryable=True,
                reason="transport") from exc

    async def summarize(self, items: list[dict[str, Any]]) -> SummaryResult:
        response = await self.client.responses.create(
            model=self.model,
            instructions=("Summarize the prior conversation for future task continuity. "
                          "Preserve user constraints, metric definitions, source IDs, unresolved questions "
                          "and correction history. Do not invent facts. Output plain text under 1200 words."),
            input=json.dumps(items, ensure_ascii=False, default=str),
            store=False, max_output_tokens=min(self.max_output_tokens, 1_024),
        )
        usage = response.usage.model_dump(exclude_none=True) if response.usage else {}
        if int(usage.get("total_tokens") or 0) <= 0:
            raise ProviderStreamError("summary response did not report token usage")
        if getattr(response, "status", None) != "completed":
            raise ProviderStreamError("summary response was incomplete", usage=usage,
                                      reason="truncated")
        return SummaryResult(response.output_text, usage)
