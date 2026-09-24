from __future__ import annotations

import argparse
import asyncio

from .config import make_runner, project_root


async def _ask(question: str) -> None:
    runner = make_runner()

    async def show(event: dict) -> None:
        kind, data = event["kind"], event["data"]
        if kind == "text_delta":
            print(data["text"], end="", flush=True)
        elif kind in {"tool_started", "tool_finished", "tool_failed", "run_failed"}:
            print(f"\n[{kind}] {data}", flush=True)

    state = await runner.run(question, callback=show)
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

        async def show(event: dict) -> None:
            if event["kind"] == "text_delta":
                print(event["data"]["text"], end="", flush=True)
            elif event["kind"] in {"tool_started", "tool_finished", "run_failed"}:
                data = {k: v for k, v in event["data"].items() if k != "result"}
                print(f"\n[{event['kind']}] {data}", flush=True)

        async def run_demo() -> None:
            result = await runner.run("2025 年 1 月和 2 月各有多少条合格行程？", callback=show)
            print(f"\nrun_id={result.run_id} status={result.status}")

        asyncio.run(run_demo())


if __name__ == "__main__":
    main()
