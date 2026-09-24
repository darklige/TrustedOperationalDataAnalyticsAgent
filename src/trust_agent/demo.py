"""Deterministic offline provider for exercising the real tool and trace path."""

from __future__ import annotations

import json

from .domain import ProviderEvent


class DemoProvider:
    async def summarize(self, items):
        return "The user asked for January and February 2025 trip counts."

    async def stream(self, messages, tools, instructions):
        outputs = [json.loads(item["output"]) for item in messages
                   if item.get("type") == "function_call_output"]
        if not outputs:
            call = {"type": "function_call", "call_id": "demo-schema", "name": "describe_data",
                    "arguments": "{}"}
            yield ProviderEvent("tool_delta", {"index": 0, "delta": "{}"})
            yield ProviderEvent("tool_ready", call)
            yield ProviderEvent("completed", {"output": [call], "usage": {"total_tokens": 0}})
        elif not any("query_id" in item for item in outputs):
            sql = "SELECT source_month, count(*) AS trip_count FROM trips GROUP BY 1 ORDER BY 1"
            call = {"type": "function_call", "call_id": "demo-query", "name": "run_sql",
                    "arguments": json.dumps({"sql": sql})}
            yield ProviderEvent("tool_delta", {"index": 0, "delta": call["arguments"]})
            yield ProviderEvent("tool_ready", call)
            yield ProviderEvent("completed", {"output": [call], "usage": {"total_tokens": 0}})
        else:
            result = next(item for item in outputs if "query_id" in item)
            rows = result["rows"]
            answer = (f"2025 年 1 月有 {rows[0][1]:,} 条、2 月有 {rows[1][1]:,} 条合格行程。"
                      f"口径：清洗后的 trip_count；这是行程数，不是独立乘客数。"
                      f"[query_id:{result['query_id']}]")
            for segment in [answer[:30], answer[30:]]:
                yield ProviderEvent("text_delta", {"text": segment})
            yield ProviderEvent("completed", {"output": [{"type": "message", "role": "assistant",
                "content": [{"type": "output_text", "text": answer}]}],
                "usage": {"total_tokens": 0}})
