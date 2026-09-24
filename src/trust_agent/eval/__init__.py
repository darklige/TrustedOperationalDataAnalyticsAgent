"""Offline-first evaluation of data-agent runs and one-shot SQL baselines."""

from .scoring import TrialScore, score_prediction, score_trace, summarize_trials
from .tasks import EvalCase, load_cases

__all__ = [
    "EvalCase", "TrialScore", "load_cases", "score_prediction", "score_trace",
    "summarize_trials",
]
