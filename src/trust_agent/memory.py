from __future__ import annotations

import asyncio
import re

from .store import EventStore


class MemoryManager:
    """Conservative asynchronous extraction of explicit long-lived user instructions."""

    _pattern = re.compile(r"(?:请记住|记住|以后请|remember that)\s*[:：]?\s*(.{3,300})", re.IGNORECASE)

    def __init__(self, store: EventStore):
        self.store = store
        self.pending: dict[str, asyncio.Task[str | None]] = {}

    def schedule(self, run_id: str, text: str) -> None:
        async def extract() -> str | None:
            await asyncio.sleep(0)
            match = self._pattern.search(text)
            return match.group(1).strip() if match else None

        self.pending[run_id] = asyncio.create_task(extract())

    async def collect(self, run_id: str) -> str | None:
        task = self.pending.get(run_id)
        if task is None or not task.done():
            return None
        self.pending.pop(run_id, None)
        result = task.result()
        if result:
            self.store.add_memory(result, run_id)
        return result

    async def flush(self, run_id: str) -> None:
        """Persist an explicit memory even if a short run ends before another loop."""
        task = self.pending.get(run_id)
        if task is not None:
            await task
            await self.collect(run_id)
