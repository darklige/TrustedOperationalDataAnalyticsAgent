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
    layered_outputs: int = 0
    budget_exceeded: bool = False


class ContextBuilder:
    """Compacts only the model view; the event log and full history remain intact."""

    def __init__(self, char_budget: int = 45_000, keep_recent: int = 10,
                 keep_recent_results: int = 2):
        self.char_budget = char_budget
        self.keep_recent = keep_recent
        self.keep_recent_results = keep_recent_results

    async def build(self, state: RunState, provider: ModelProvider) -> ContextView:
        history = state.history
        active = history[state.compacted_until:]
        messages = self._with_summary(state.summary, active)
        current_chars = self._chars(messages)
        if current_chars <= self.char_budget:
            return ContextView(messages, False, 0, current_chars)

        # Tool output can dominate a long transcript. Shrink only the model
        # view, keeping calls and outputs paired and the latest results intact.
        layered, layered_count = self._layer_outputs(active)
        messages = self._with_summary(state.summary, layered)
        if self._chars(messages) <= self.char_budget:
            return ContextView(messages, layered_count > 0, 0,
                               self._chars(messages), layered_count)

        # Reserve room for the summary. Only cut after every tool call in a batch
        # has its output, so Chat Completions never receives orphan tool messages.
        cuts = self._safe_cuts(history, state.compacted_until)
        output_indices = [index for index in range(state.compacted_until, len(history))
                          if history[index].get("type") == "function_call_output"]
        if output_indices:
            # A safe cut before the latest output's call keeps that whole
            # exchange available for follow-up questions and citation checks.
            first_kept_output = output_indices[-min(len(output_indices),
                                                     max(1, self.keep_recent_results))]
            cuts = [candidate for candidate in cuts if candidate <= first_kept_output]
        desired = max(state.compacted_until, len(history) - self.keep_recent)
        cut = max((candidate for candidate in cuts if candidate <= desired),
                  default=state.compacted_until)
        target = max(1, self.char_budget - min(8_000, self.char_budget // 4))
        for candidate in cuts:
            if self._chars(layered[cut - state.compacted_until:]) <= target:
                break
            cut = max(cut, candidate)
        older = layered[:cut - state.compacted_until]
        recent = layered[cut - state.compacted_until:]
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
        messages = self._bounded_view(state.summary, recent)
        chars = self._chars(messages)
        return ContextView(messages, bool(older) or layered_count > 0, len(older),
                           chars, layered_count, chars > self.char_budget)

    def _layer_outputs(self, active: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], int]:
        output_indices = [index for index, item in enumerate(active)
                          if item.get("type") == "function_call_output"]
        recent = set(output_indices[-max(1, self.keep_recent_results):])
        layered = list(active)
        changed = 0
        for index in output_indices:
            if index in recent:
                continue
            original = active[index]
            compact = self._compact_output(str(original.get("output", "")))
            if compact is not None and len(compact) < len(str(original.get("output", ""))):
                layered[index] = {**original, "output": compact}
                changed += 1
        return layered, changed

    @staticmethod
    def _compact_output(raw: str) -> str | None:
        try:
            result = json.loads(raw)
        except (TypeError, json.JSONDecodeError):
            return None
        if not isinstance(result, dict):
            return None
        # Preserve the evidence contract and the first result rows. SQL lets
        # the model reproduce an older calculation; the Trace keeps every row.
        retained = {key: result[key] for key in (
            "query_id", "dataset_version", "result_sha256", "sql", "row_count",
            "truncated", "columns", "definition", "name", "error", "message")
            if key in result}
        rows = result.get("rows")
        if isinstance(rows, list):
            retained["rows"] = rows[:3]
            if len(rows) > 3:
                retained["omitted_rows"] = len(rows) - 3
                retained["note"] = "Older result condensed in model view; full rows remain in Trace. Rerun SQL for details."
        else:
            # Keep short, non-SQL results such as schema and metric definitions
            # unchanged; they may contain the only available field definitions.
            return None
        return json.dumps(retained, ensure_ascii=False, default=str)

    def _bounded_view(self, summary: str, recent: list[dict[str, Any]]) -> list[dict[str, Any]]:
        messages = self._with_summary(summary, recent)
        if self._chars(messages) <= self.char_budget:
            return messages
        # Retain the latest question and complete tool exchanges. If that
        # immutable suffix alone exceeds the budget, report the overflow.
        if self._chars(recent) > self.char_budget:
            return list(recent)
        low, high = 0, len(summary)
        best = list(recent)
        while low <= high:
            middle = (low + high) // 2
            candidate = self._with_summary(summary[-middle:] if middle else "", recent)
            if self._chars(candidate) <= self.char_budget:
                best = candidate
                low = middle + 1
            else:
                high = middle - 1
        return best

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
