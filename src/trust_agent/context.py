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

        # Reserve room for the new summary, and keep call/result pairs together.
        cut = max(state.compacted_until, len(history) - self.keep_recent)
        while cut > state.compacted_until and history[cut].get("type") == "function_call_output":
            cut -= 1
        target = max(1, self.char_budget - min(8_000, self.char_budget // 4))
        while cut < len(history) - 1 and self._chars(history[cut:]) > target:
            next_cut = cut + 1
            while (next_cut < len(history) - 1 and
                   history[next_cut].get("type") == "function_call_output"):
                next_cut += 1
            if history[next_cut].get("type") == "function_call_output":
                break  # The last tool result needs its matching call.
            cut = next_cut
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
    def _with_summary(summary: str, history: list[dict[str, Any]]) -> list[dict[str, Any]]:
        if not summary:
            return list(history)
        return [{"role": "developer", "content": "Prior context summary (may omit detail; verify with tools):\n"
                 + summary}, *history]
