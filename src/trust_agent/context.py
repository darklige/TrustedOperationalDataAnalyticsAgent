from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from .domain import RunState
from .provider import ModelProvider


@dataclass(slots=True)
class ContextView:
    messages: list[dict[str, Any]]
    compacted: bool
    omitted_items: int
    chars: int


class ContextBuilder:
    """Compacts only the model view; the event log and full history remain intact."""

    def __init__(self, char_budget: int = 45_000, keep_recent: int = 10):
        self.char_budget = char_budget
        self.keep_recent = keep_recent

    async def build(self, state: RunState, provider: ModelProvider) -> ContextView:
        history = state.history
        active = history[state.compacted_until:]
        messages = self._with_summary(state.summary, active)
        current_chars = self._chars(messages)
        if current_chars <= self.char_budget:
            return ContextView(messages, False, 0, current_chars)

        # Reserve room for the summary. Only cut after every tool call in a batch
        # has its output, so Chat Completions never receives orphan tool messages.
        cuts = self._safe_cuts(history, state.compacted_until)
        desired = max(state.compacted_until, len(history) - self.keep_recent)
        cut = max((candidate for candidate in cuts if candidate <= desired),
                  default=state.compacted_until)
        target = max(1, self.char_budget - min(8_000, self.char_budget // 4))
        for candidate in cuts:
            if self._chars(history[cut:]) <= target:
                break
            cut = max(cut, candidate)
        older, recent = history[state.compacted_until:cut], history[cut:]
        if older:
            new_summary = await provider.summarize(older)
            evidence_ids = []
            for item in older:
                if item.get("type") != "function_call_output":
                    continue
                try:
                    evidence = json.loads(item.get("output", "{}")).get("query_id")
                except json.JSONDecodeError:
                    evidence = None
                if evidence:
                    evidence_ids.append(evidence)
            pointer = "Historical query IDs: " + ", ".join(dict.fromkeys(evidence_ids))
            state.summary = "\n".join(filter(None, [state.summary, new_summary,
                                                       pointer if evidence_ids else ""]))[-8_000:]
            state.compacted_until = cut
        messages = self._with_summary(state.summary, recent)
        return ContextView(messages, bool(older), len(older), self._chars(messages))

    @staticmethod
    def _chars(messages: list[dict[str, Any]]) -> int:
        return len(json.dumps(messages, ensure_ascii=False, default=str))

    @staticmethod
    def _safe_cuts(history: list[dict[str, Any]], start: int) -> list[int]:
        pending: set[str] = set()
        cuts = [start]
        for index in range(start, len(history) - 1):
            item = history[index]
            if item.get("type") == "function_call":
                pending.add(item["call_id"])
            elif item.get("type") == "function_call_output":
                pending.discard(item["call_id"])
            if not pending:
                cuts.append(index + 1)
        return cuts

    @staticmethod
    def _with_summary(summary: str, history: list[dict[str, Any]]) -> list[dict[str, Any]]:
        if not summary:
            return list(history)
        return [{"role": "developer", "content": "Prior context summary (may omit detail; verify with tools):\n"
                 + summary}, *history]
