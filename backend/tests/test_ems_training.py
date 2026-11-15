import importlib.util

import pytest


pytestmark = pytest.mark.skipif(importlib.util.find_spec("xgboost") is None,
                                reason="Optional EMS dependencies: uv sync --group ems")


@pytest.fixture
def sample():
    import pandas as pd

    return pd.DataFrame([
        {"incident_id": str(index), "incident_datetime": timestamp,
         "initial_call_type": "SICK", "initial_severity_level_code": "4",
         "borough": "MANHATTAN", "valid_dispatch_rspns_time_indc": "Y",
         "dispatch_response_seconds_qy": target}
        for index, (timestamp, target) in enumerate([
            ("2023-01-01T10:00:00", 10), ("2023-02-28T23:58:59", 30),
            ("2023-03-01T00:00:00", 20), ("2023-03-15T23:58:59", 40),
            ("2023-03-16T00:00:00", 50), ("2023-03-31T23:58:59", 60)])])


def test_temporal_boundaries_and_train_only_preprocessing(sample):
    from ml.ems.features import encode, feature_frame, fit_categories
    from ml.ems.train import prepare, temporal_split

    sample.loc[4, "initial_call_type"] = "TEST_ONLY_CATEGORY"
    prepared, audit = prepare(sample)
    splits = temporal_split(prepared)
    assert {name: len(part) for name, part in splits.items()} == {"train": 2, "validation": 2, "test": 2}
    assert audit["excluded_rows"] == 0
    categories = fit_categories(feature_frame(splits["train"]))
    assert "TEST_ONLY_CATEGORY" not in categories["initial_call_type"]
    import numpy as np
    assert np.isnan(encode(feature_frame(splits["test"]), categories)[0, 0])
    assert set(splits["train"].incident_id).isdisjoint(splits["test"].incident_id)


def test_outcome_fields_cannot_change_features(sample):
    from ml.ems.features import FEATURES, feature_frame

    before = feature_frame(sample)
    for name in ["final_severity_level_code", "first_assignment_datetime",
                 "first_activation_datetime", "incident_travel_tm_seconds_qy",
                 "dispatch_response_seconds_qy", "incident_dispatch_area"]:
        sample[name] = "FUTURE_OUTCOME"
    assert feature_frame(sample).equals(before)
    assert list(before) == FEATURES


def test_excludes_invalid_targets_but_preserves_zero_and_tail(sample):
    from ml.ems.train import prepare

    sample["dispatch_response_seconds_qy"] = sample["dispatch_response_seconds_qy"].astype(float)
    sample.loc[0, "dispatch_response_seconds_qy"] = -1
    sample.loc[1, "valid_dispatch_rspns_time_indc"] = "N"
    sample.loc[2, "dispatch_response_seconds_qy"] = float("inf")
    sample.loc[3, "dispatch_response_seconds_qy"] = None
    sample.loc[4, "dispatch_response_seconds_qy"] = 0
    sample.loc[5, "dispatch_response_seconds_qy"] = 1_000_000
    result, audit = prepare(sample)
    assert list(result.dispatch_response_seconds_qy) == [0, 1_000_000]
    assert audit["excluded_rows"] == 4


def test_duplicate_incidents_and_out_of_window_fail_closed(sample):
    from ml.ems.train import prepare

    sample.loc[1, "incident_id"] = sample.loc[0, "incident_id"]
    with pytest.raises(ValueError, match="duplicate"):
        prepare(sample)
    sample.loc[1, "incident_id"] = "unique"
    sample.loc[0, "incident_datetime"] = "2024-01-01"
    with pytest.raises(ValueError, match="outside"):
        prepare(sample)


def test_baseline_uses_training_outcomes_only(sample):
    from ml.ems.train import TARGET, baseline_predictions, prepare, temporal_split

    splits = temporal_split(prepare(sample)[0])
    expected = baseline_predictions(splits["train"], splits["test"])
    splits["test"][TARGET] = 10_000
    actual = baseline_predictions(splits["train"], splits["test"])
    assert all((actual[name] == expected[name]).all() for name in expected)
    assert list(actual["train_global_median"]) == [20, 20]


def test_missing_features_and_fixed_metric_values(sample):
    from ml.ems.features import MISSING, feature_frame
    from ml.ems.train import metrics

    sample.loc[0, "initial_call_type"] = None
    assert feature_frame(sample).iloc[0].initial_call_type == MISSING
    assert metrics([0, 10], [0, 0])["mae_seconds"] == 5
    assert metrics([0, 10], [0, 0])["absolute_error_percentiles_seconds"]["50"] == 5


def test_aware_inference_timestamp_converts_to_nyc_wall_time():
    from datetime import datetime
    from ml.ems.features import prediction_frame

    frame = prediction_frame(datetime.fromisoformat("2023-03-20T14:00:00+00:00"), None, None, None)
    assert frame.iloc[0].hour == "10"


def test_fetch_refuses_large_sample_and_existing_snapshot(tmp_path, monkeypatch):
    from ml.ems import fetch

    monkeypatch.setattr(fetch, "request_json", lambda url, parameters=None:
                        {} if parameters is None else [{"count": str(fetch.MAX_ROWS + 1)}])
    with pytest.raises(ValueError, match="unbounded"):
        fetch.fetch(tmp_path)
    (tmp_path / "sample.jsonl").write_text("immutable")
    with pytest.raises(ValueError, match="never overwritten"):
        fetch.fetch(tmp_path)


def test_training_rejects_tampered_data_before_fitting(tmp_path):
    import json
    from ml.ems.train import train

    (tmp_path / "sample.jsonl").write_text("tampered")
    (tmp_path / "manifest.json").write_text(json.dumps({"sample_sha256": "wrong"}))
    with pytest.raises(ValueError, match="checksum"):
        train(tmp_path, tmp_path / "output")


def test_labels_must_be_available_before_partition_end(sample):
    from ml.ems.train import prepare, temporal_split

    sample.loc[1, "incident_datetime"] = "2023-02-28T23:59:59"
    sample.loc[3, "incident_datetime"] = "2023-03-15T23:59:59"
    sample.loc[5, "incident_datetime"] = "2023-03-31T23:59:59"
    splits = temporal_split(prepare(sample)[0])
    assert {name: len(part) for name, part in splits.items()} == {"train": 1, "validation": 1, "test": 1}
