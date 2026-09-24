from __future__ import annotations

import argparse
import asyncio

from .config import make_runner, project_root


class TerminalEventRenderer:
    """Only show an answer after the agent has accepted that attempt."""

    def __init__(self) -> None:
        self._provisional: dict[str, list[str]] = {}

    async def __call__(self, event: dict) -> None:
        kind, data = event["kind"], event["data"]
        if kind == "text_delta":
            if data.get("provisional"):
                attempt_id = str(data.get("attempt_id", ""))
                self._provisional.setdefault(attempt_id, []).append(data["text"])
            else:
                # Existing callbacks without the new protocol still stream directly.
                print(data["text"], end="", flush=True)
        elif kind == "text_committed":
            attempt_id = str(data.get("attempt_id", ""))
            buffered = "".join(self._provisional.pop(attempt_id, []))
            print(data.get("text", buffered), end="", flush=True)
        elif kind == "text_discarded":
            self._provisional.pop(str(data.get("attempt_id", "")), None)
        elif kind in {"tool_started", "tool_finished", "tool_failed", "run_failed"}:
            if kind == "run_failed":
                self._provisional.clear()
            progress = {key: value for key, value in data.items() if key != "result"}
            print(f"\n[{kind}] {progress}", flush=True)


async def _ask(question: str) -> None:
    runner = make_runner()
    state = await runner.run(question, callback=TerminalEventRenderer())
    print(f"\nrun_id={state.run_id} status={state.status}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Traceable data agent")
    sub = parser.add_subparsers(dest="command", required=True)
    ask = sub.add_parser("ask")
    ask.add_argument("question")
    serve = sub.add_parser("serve")
    serve.add_argument("--host", default="127.0.0.1")
    serve.add_argument("--port", type=int, default=8000)
    sub.add_parser("demo", help="Run a deterministic offline provider through real SQL tools")
    args = parser.parse_args()
    if args.command == "ask":
        asyncio.run(_ask(args.question))
    elif args.command == "serve":
        import uvicorn

        uvicorn.run("trust_agent.api:app", host=args.host, port=args.port)
    elif args.command == "demo":
        from .demo import DemoProvider
        from .loop import AgentRunner
        from .sql import QueryService
        from .store import EventStore
        from .tools import ToolRegistry

        root = project_root()
        runner = AgentRunner(DemoProvider(),
                             ToolRegistry(QueryService(root / "data/nyc_taxi.duckdb",
                                                       {"trips", "zones"}), root),
                             EventStore(root / "runtime/agent.sqlite3"))

        async def run_demo() -> None:
            result = await runner.run("2025 年 1 月和 2 月各有多少条合格行程？",
                                      callback=TerminalEventRenderer())
            print(f"\nrun_id={result.run_id} status={result.status}")

        asyncio.run(run_demo())


if __name__ == "__main__":
    main()
