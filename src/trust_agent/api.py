from __future__ import annotations

import asyncio
import json
import uuid
from collections.abc import AsyncIterator
from pathlib import Path

from fastapi import FastAPI, Header, HTTPException, Query
from fastapi.responses import FileResponse, StreamingResponse
from pydantic import BaseModel, Field

from .config import make_runner
from .loop import AgentRunner


class StartRun(BaseModel):
    question: str = Field(min_length=1, max_length=4000)


def create_app(runner: AgentRunner | None = None) -> FastAPI:
    app = FastAPI(title="Trustworthy Data Agent", version="0.1.0")
    jobs: dict[str, asyncio.Task] = {}
    web_dir = Path(__file__).with_name("web")

    @app.get("/ui", include_in_schema=False)
    @app.get("/ui/", include_in_schema=False)
    async def ui() -> FileResponse:
        return FileResponse(web_dir / "index.html", media_type="text/html",
                            headers={"Cache-Control": "no-store",
                                     "X-Content-Type-Options": "nosniff"})

    @app.get("/ui/app.mjs", include_in_schema=False)
    async def ui_script() -> FileResponse:
        return FileResponse(web_dir / "app.mjs", media_type="text/javascript",
                            headers={"Cache-Control": "no-store",
                                     "X-Content-Type-Options": "nosniff"})

    @app.post("/runs", status_code=202)
    async def start_run(body: StartRun) -> dict[str, str]:
        run_id = uuid.uuid4().hex
        active_runner = runner or make_runner()
        jobs[run_id] = asyncio.create_task(active_runner.run(body.question, run_id))
        return {"run_id": run_id, "events_url": f"/runs/{run_id}/events"}

    @app.get("/runs/{run_id}")
    async def get_run(run_id: str) -> dict:
        active_runner = runner or make_runner()
        state = active_runner.store.get(run_id)
        if not state:
            raise HTTPException(404, "unknown run")
        return state.to_dict()

    @app.post("/runs/{run_id}/messages", status_code=202)
    async def continue_run(run_id: str, body: StartRun) -> dict[str, str]:
        active_runner = runner or make_runner()
        if active_runner.store.get(run_id) is None:
            raise HTTPException(404, "unknown run")
        previous = jobs.get(run_id)
        if previous and not previous.done():
            raise HTTPException(409, "run is active")
        jobs[run_id] = asyncio.create_task(active_runner.run(body.question, run_id))
        return {"run_id": run_id, "events_url": f"/runs/{run_id}/events"}

    @app.get("/runs/{run_id}/events")
    async def stream_events(run_id: str, after: int = Query(0, ge=0),
                            last_event_id: str | None = Header(None)) -> StreamingResponse:
        active_runner = runner or make_runner()
        if run_id not in jobs and active_runner.store.get(run_id) is None:
            raise HTTPException(404, "unknown run")
        if last_event_id and last_event_id.isdigit():
            after = max(after, int(last_event_id))

        async def replay() -> AsyncIterator[str]:
            cursor = after
            terminal_kinds = {"run_completed", "run_failed", "run_cancelled"}
            while True:
                batch = active_runner.store.events(run_id, cursor)
                for event in batch:
                    cursor = event["seq"]
                    yield f"id: {cursor}\nevent: {event['kind']}\ndata: " + \
                          json.dumps(event, ensure_ascii=False) + "\n\n"
                snapshot = active_runner.store.get(run_id)
                if (batch and batch[-1]["kind"] in terminal_kinds and snapshot
                        and snapshot.status in
                        {"completed", "failed", "cancelled", "budget_exceeded"}):
                    break
                # A reconnect can start after the terminal event. The event
                # store remains authoritative even if the state snapshot was
                # saved slightly before that event was appended.
                if not batch:
                    job = jobs.get(run_id)
                    if (snapshot and snapshot.status in
                            {"completed", "failed", "cancelled", "budget_exceeded"}
                            and (job is None or job.done())):
                        terminal = [event for event in active_runner.store.events(run_id)
                                    if event["kind"] in terminal_kinds]
                        if terminal and cursor >= terminal[-1]["seq"]:
                            break
                await asyncio.sleep(0.2)

        return StreamingResponse(replay(), media_type="text/event-stream",
                                 headers={"Cache-Control": "no-cache"})

    @app.post("/runs/{run_id}/cancel")
    async def cancel_run(run_id: str) -> dict[str, str]:
        job = jobs.get(run_id)
        if not job or job.done():
            raise HTTPException(404, "no active run")
        job.cancel()
        return {"run_id": run_id, "status": "cancelling"}

    return app


app = create_app()
