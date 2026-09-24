"""Export three deterministic, inspectable offline traces; no model key needed."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

from trust_agent.demo import DemoProvider
from trust_agent.domain import ProviderEvent
from trust_agent.loop import AgentRunner
from trust_agent.sql import QueryService
from trust_agent.store import EventStore
from trust_agent.tools import ToolRegistry

ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "docs" / "traces"


class UnsafeSQLProvider:
    async def summarize(self, items):
        return "Attempted unsafe file access was denied."

    async def stream(self, messages, tools, instructions):
        if not any(item.get("type") == "function_call_output" for item in messages):
            call = {"type": "function_call", "call_id": "unsafe-call", "name": "run_sql",
                    "arguments": json.dumps({"sql": "SELECT read_text('/etc/passwd')"})}
            yield ProviderEvent("tool_ready", call)
            yield ProviderEvent("completed", {"output": [call], "usage": {"total_tokens": 0}})
        else:
            answer = "无法验证该文件内容；查询工具拒绝了未授权的文件读取。"
            yield ProviderEvent("text_delta", {"text": answer})
            yield ProviderEvent("completed", {"output": [{"type": "message", "role": "assistant",
                "content": [{"type": "output_text", "text": answer}]}], "usage": {"total_tokens": 0}})


class MissingEvidenceProvider:
    async def summarize(self, items):
        return "No query evidence exists."

    async def stream(self, messages, tools, instructions):
        if not any(item.get("role") == "developer" and "lacked" in item.get("content", "")
                   for item in messages):
            answer = "2025 年 2 月的行程增长了 20%。"
        else:
            answer = "无法核实增长幅度；此前回答没有查询证据。"
        yield ProviderEvent("text_delta", {"text": answer})
        yield ProviderEvent("completed", {"output": [{"type": "message", "role": "assistant",
            "content": [{"type": "output_text", "text": answer}]}], "usage": {"total_tokens": 0}})


async def main() -> None:
    OUTPUT.mkdir(parents=True, exist_ok=True)
    query = QueryService(ROOT / "data" / "nyc_taxi.duckdb", {"trips", "zones"})
    store = EventStore(ROOT / "runtime" / "export_traces.sqlite3")
    scenarios = [
        ("success", DemoProvider(), "2025 年 1 月和 2 月各有多少条行程？"),
        ("unsafe_sql", UnsafeSQLProvider(), "读取本地文件内容"),
        ("missing_evidence", MissingEvidenceProvider(), "2025 年 2 月增长多少？"),
    ]
    for name, provider, question in scenarios:
        runner = AgentRunner(provider, ToolRegistry(query, ROOT), store)
        state = await runner.run(question)
        payload = {"scenario": name, "offline_scripted_provider": True,
                   "status": state.status, "answer": state.answer,
                   "events": store.events(state.run_id)}
        path = OUTPUT / f"{name}.json"
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, default=str) + "\n")
        print(f"{name}: {state.status}; {len(payload['events'])} events -> {path}")


if __name__ == "__main__":
    asyncio.run(main())
