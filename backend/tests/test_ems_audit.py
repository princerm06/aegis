import importlib.util

import pytest


pytestmark = pytest.mark.skipif(importlib.util.find_spec("xgboost") is None,
                                reason="Optional EMS dependencies: uv sync --group ems")


def test_severity_exclusion_and_difference_direction():
    from ml.ems.audit import subgroup_analysis

    result = subgroup_analysis([10, 20, 100], [8, 18, 90], [5, 15, 0], ["1", "2", "8"])
    assert result["excluding_severity_8"]["n"] == 2
    assert result["excluding_severity_8"]["model"]["mae_seconds"] == 2
    assert result["excluding_severity_8"]["baseline"]["mae_seconds"] == 5
    assert result["per_severity"]["1"]["model_minus_baseline_seconds"]["mae_seconds"] == -3
    assert result["severity_8_share_of_absolute_error_reduction_percent"] == pytest.approx(93.75)


def test_tail_sensitivity_does_not_change_primary_population():
    from ml.ems.audit import outlier_analysis, subgroup_analysis

    actual, model, baseline = [10, 20, 10000], [10, 20, 0], [0, 0, 0]
    tail = outlier_analysis(actual, model, baseline)
    primary = subgroup_analysis(actual, model, baseline, ["1", "1", "8"])
    assert tail["target_tail_sensitivity"]["3600"]["tail_n"] == 1
    assert tail["target_tail_sensitivity"]["3600"]["non_tail_metrics"]["n"] == 2
    assert tail["residual_tail_influence"]["model"]["0.01"]["share_of_squared_error_percent"] == 100
    assert primary["all"]["n"] == 3


def test_zero_reduction_is_explicit_instead_of_dividing_by_zero():
    from ml.ems.audit import subgroup_analysis

    result = subgroup_analysis([10, 100], [9, 90], [9, 90], ["1", "8"])
    assert result["severity_8_share_of_absolute_error_reduction_percent"] is None
    assert result["severity_8_share_of_squared_error_reduction_percent"] is None


def test_public_audit_withholds_incident_level_records():
    import json
    from backend.app.services.ems_dispatch import DEFAULT_CHECKPOINT

    report = json.loads((DEFAULT_CHECKPOINT / "generalization_audit.json").read_text())
    assert "ten_largest_target_examples" not in report["outliers"]
    assert "ten_largest_model_error_examples" not in report["outliers"]
    assert "Withheld" in report["outliers"]["incident_level_examples"]
