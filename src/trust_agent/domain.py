from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(slots=True)
class ProviderEvent:
    kind: str
    data: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class RunState:
    run_id: str
    history: list[dict[str, Any]] = field(default_factory=list)
    summary: str = ""
    turn: int = 0
    status: str = "running"
    answer: str = ""
    compacted_until: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "history": self.history,
            "summary": self.summary,
            "turn": self.turn,
            "status": self.status,
            "answer": self.answer,
            "compacted_until": self.compacted_until,
        }

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> RunState:
        return cls(**value)
