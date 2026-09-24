from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(slots=True)
class ProviderEvent:
    kind: str
    data: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class SummaryResult:
    text: str
    usage: dict[str, Any] = field(default_factory=dict)


class ProviderStreamError(RuntimeError):
    """A failed model stream with any usage the gateway reported before failure."""

    def __init__(self, message: str, *, usage: dict[str, Any] | None = None,
                 model: str | None = None, response_id: str | None = None,
                 retryable: bool = False, reason: str | None = None):
        super().__init__(message)
        self.usage = usage or {}
        self.model = model
        self.response_id = response_id
        self.retryable = retryable
        self.reason = reason


@dataclass(slots=True)
class RunState:
    run_id: str
    history: list[dict[str, Any]] = field(default_factory=list)
    summary: str = ""
    turn: int = 0
    status: str = "running"
    answer: str = ""
    stop_reason: str | None = None
    compacted_until: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "history": self.history,
            "summary": self.summary,
            "turn": self.turn,
            "status": self.status,
            "answer": self.answer,
            "stop_reason": self.stop_reason,
            "compacted_until": self.compacted_until,
        }

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> RunState:
        return cls(**value)
