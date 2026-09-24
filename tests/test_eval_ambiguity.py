import json

import pytest

from trust_agent.eval.scoring import score_prediction
from trust_agent.eval.tasks import EvalCase, apply_ambiguity_sidecar


class Query:
    def query(self, sql, row_limit=1000):
        return {"rows": [[251, 72.81]] if sql == "alternative" else [[243, 77.26]],
                "truncated": False}


def test_documented_metric_alternative_only_triggers_review(tmp_path):
    case = EvalCase("E020", "route_fare", "mean fare", "gold",
                    [[243, 77.26]], None)
    assert score_prediction(case, "alternative", Query())[0] is False
    sidecar = tmp_path / "ambiguities.json"
    sidecar.write_text(json.dumps({
        "version": 1, "historical_assets_immutable": True,
        "ambiguities": [{"case_id": "E020", "alternative_sql": "alternative",
                         "alternative_expected_rows": [[251, 72.81]],
                         "rationale": "Question does not specify the nonnegative filter",
                         "decision": "needs_review"}],
    }))
    amended = apply_ambiguity_sidecar([case], sidecar, Query())[0]
    assert amended.expected_rows == [[243, 77.26]]
    assert score_prediction(amended, "gold", Query()) == (True, [])
    correct, notes = score_prediction(amended, "alternative", Query())
    assert correct is None
    assert "documented alternative" in notes[0]


def test_sidecar_refuses_unverified_alternative(tmp_path):
    case = EvalCase("E020", "route_fare", "mean fare", "gold",
                    [[243, 77.26]], None)
    sidecar = tmp_path / "ambiguities.json"
    sidecar.write_text(json.dumps({
        "version": 1, "historical_assets_immutable": True,
        "ambiguities": [{"case_id": "E020", "alternative_sql": "alternative",
                         "alternative_expected_rows": [[999, 1]],
                         "rationale": "review", "decision": "needs_review"}],
    }))
    with pytest.raises(ValueError, match="differs"):
        apply_ambiguity_sidecar([case], sidecar, Query())
