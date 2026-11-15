"""Audit frozen historical predictions; never fit or alter the model."""

import argparse
import json
import time
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd
import xgboost as xgb

from ml.ems.features import encode, feature_frame, fit_categories
from ml.ems.fetch import END, START, sha256
from ml.ems.train import TARGET, TEST_START, VALIDATION_START, baseline_predictions, metrics, prepare, temporal_split


def compare(actual, model, baseline) -> dict:
    model_metrics = metrics(actual, model)
    baseline_metrics = metrics(actual, baseline)
    return {"n": len(actual), "model": model_metrics, "baseline": baseline_metrics,
            "model_minus_baseline_seconds": {
                key: model_metrics[key] - baseline_metrics[key]
                for key in ("mae_seconds", "rmse_seconds")},
            "improvement_percent": {
                key: 100 * (baseline_metrics[key] - model_metrics[key]) / baseline_metrics[key]
                if baseline_metrics[key] else None
                for key in ("mae_seconds", "rmse_seconds")}}


def subgroup_analysis(actual, model, baseline, severity) -> dict:
    actual, model, baseline = map(np.asarray, (actual, model, baseline))
    severity = np.asarray(severity).astype(str)
    groups = {level: compare(actual[severity == level], model[severity == level], baseline[severity == level])
              for level in sorted(set(severity))}
    difference = np.abs(baseline - actual) - np.abs(model - actual)
    squared_difference = (baseline - actual) ** 2 - (model - actual) ** 2
    severity8 = severity == "8"
    return {"all": compare(actual, model, baseline),
            "excluding_severity_8": compare(actual[~severity8], model[~severity8], baseline[~severity8]),
            "per_severity": groups,
            "severity_8_count": int(severity8.sum()),
            "severity_8_population_percent": float(100 * severity8.mean()),
            "severity_8_share_of_absolute_error_reduction_percent":
                float(100 * difference[severity8].sum() / difference.sum()) if difference.sum() else None,
            "severity_8_share_of_squared_error_reduction_percent":
                float(100 * squared_difference[severity8].sum() / squared_difference.sum()) if squared_difference.sum() else None}


def outlier_analysis(actual, model, baseline) -> dict:
    actual, model, baseline = map(np.asarray, (actual, model, baseline))
    result = {"target_percentiles_seconds": {
        str(percentile): float(np.percentile(actual, percentile))
        for percentile in (0, 50, 90, 95, 99, 99.9, 100)},
        "target_tail_sensitivity": {}, "residual_tail_influence": {}}
    for threshold in (600, 3600, 21600):
        tail = actual > threshold
        result["target_tail_sensitivity"][str(threshold)] = {
            "target_greater_than_seconds": threshold, "tail_n": int(tail.sum()),
            "tail_population_percent": float(100 * tail.mean()),
            "tail_metrics": compare(actual[tail], model[tail], baseline[tail]) if tail.any() else None,
            "non_tail_metrics": compare(actual[~tail], model[~tail], baseline[~tail]) if (~tail).any() else None}
    for name, predictions in (("model", model), ("baseline", baseline)):
        errors = np.abs(predictions - actual)
        squared = errors ** 2
        influence = {}
        for fraction in (0.001, 0.01):
            count = max(1, int(np.ceil(len(actual) * fraction)))
            positions = np.argsort(errors, kind="stable")[-count:]
            influence[str(fraction)] = {"n": count,
                "share_of_absolute_error_percent": float(100 * errors[positions].sum() / errors.sum()) if errors.sum() else 0,
                "share_of_squared_error_percent": float(100 * squared[positions].sum() / squared.sum()) if squared.sum() else 0}
        result["residual_tail_influence"][name] = influence
    return result


def audit(data: Path, checkpoint: Path) -> dict:
    started = time.perf_counter()
    report = json.loads((checkpoint / "report.json").read_text())
    manifest = json.loads((data / "manifest.json").read_text())
    if manifest != report["provenance"]:
        raise ValueError("Audit data and training provenance differ")
    for filename, expected in (("sample.jsonl", "sample_sha256"),
                               ("metadata.json", "metadata_sha256"), ("dictionary.xlsx", "dictionary_sha256")):
        if sha256(data / filename) != manifest[expected]:
            raise ValueError(f"Source checksum mismatch: {filename}")
    original_model_hash = sha256(checkpoint / "model.ubj")
    if original_model_hash != report["model_sha256"] or sha256(checkpoint / "categories.json") != report["categories_sha256"]:
        raise ValueError("Frozen checkpoint checksum mismatch")
    raw = pd.read_json(data / "sample.jsonl", lines=True, dtype=False)
    if len(raw) != manifest["row_count"]:
        raise ValueError("Source row count mismatch")
    prepared, quality = prepare(raw)
    splits = temporal_split(prepared)
    categories = json.loads((checkpoint / "categories.json").read_text())
    if fit_categories(feature_frame(splits["train"])) != categories:
        raise ValueError("Saved category vocabularies differ from training-only categories")
    chronology = {}
    prior_ids = set()
    for name, start, end in (("train", START, VALIDATION_START),
                              ("validation", VALIDATION_START, TEST_START), ("test", TEST_START, END)):
        part = splits[name]
        times = part["incident_datetime"]
        available_at = times + pd.to_timedelta(part[TARGET], unit="s")
        ids = set(part["incident_id"].astype(str))
        if not (times.ge(pd.Timestamp(start)).all() and times.lt(pd.Timestamp(end)).all()
                and available_at.lt(pd.Timestamp(end)).all() and prior_ids.isdisjoint(ids)):
            raise ValueError("Chronology, label availability or incident disjointness failed")
        if len(part) != report["splits"][name]["n"]:
            raise ValueError("Partition differs from training report")
        prior_ids.update(ids)
        chronology[name] = {"n": len(part), "creation_min": times.min().isoformat(),
                            "creation_max": times.max().isoformat(),
                            "latest_label_available_at": available_at.max().isoformat(),
                            "end_exclusive": end, "incident_ids_disjoint": True,
                            "labels_available_strictly_before_end": True}
    best_baseline = min(report["validation_baselines"], key=lambda name: report["validation_baselines"][name]["mae_seconds"])
    best_candidate = min(report["validation_candidates"], key=lambda item: item["validation"]["mae_seconds"])
    if best_baseline != report["selected_baseline"] or any(
        report["selected_parameters"][key] != best_candidate[key] for key in ("max_depth", "n_estimators")
    ):
        raise ValueError("Selection differs from the declared validation-only rule")
    test = splits["test"]
    features = feature_frame(test)
    actual = test[TARGET].to_numpy()
    model = xgb.XGBRegressor()
    model.load_model(checkpoint / "model.ubj")
    predicted = np.maximum(0, model.predict(encode(features, categories)))
    baseline = baseline_predictions(splits["train"], test)[best_baseline]
    for expected, observed in ((report["test"], metrics(actual, predicted)),
                               (report["test_baselines"][best_baseline], metrics(actual, baseline))):
        for key in ("mae_seconds", "rmse_seconds"):
            if not np.isclose(expected[key], observed[key], rtol=1e-12):
                raise ValueError("Recomputed frozen-weight metrics differ from the report")
    saved = pd.read_csv(data / "heldout_predictions.csv", dtype={"incident_id": str})
    if saved["incident_id"].tolist() != test["incident_id"].astype(str).tolist():
        raise ValueError("Saved prediction IDs do not match held-out incident order")
    np.testing.assert_allclose(saved["prediction_seconds"], predicted, rtol=1e-6, atol=1e-4)
    np.testing.assert_array_equal(saved[TARGET], actual)
    np.testing.assert_array_equal(saved["baseline_seconds"], baseline)
    severity = features["initial_severity_level_code"].to_numpy()
    results = subgroup_analysis(actual, predicted, baseline, severity)
    results["outliers"] = outlier_analysis(actual, predicted, baseline)
    for level in sorted(set(severity)):
        tail = (actual > 3600) & (severity == level)
        results["per_severity"][level]["target_over_one_hour_n"] = int(tail.sum())
    largest = np.argsort(actual, kind="stable")[-10:][::-1]
    examples = test.iloc[largest][["incident_datetime", "initial_call_type", "initial_severity_level_code", "borough", TARGET]].copy()
    examples["incident_datetime"] = examples["incident_datetime"].astype(str)
    examples["model_prediction_seconds"] = predicted[largest].astype(float)
    examples["baseline_prediction_seconds"] = baseline[largest]
    largest_errors = np.argsort(np.abs(predicted - actual), kind="stable")[-10:][::-1]
    error_examples = test.iloc[largest_errors][["incident_datetime", "initial_call_type", "initial_severity_level_code", "borough", TARGET]].copy()
    error_examples["incident_datetime"] = error_examples["incident_datetime"].astype(str)
    error_examples["model_prediction_seconds"] = predicted[largest_errors].astype(float)
    error_examples["baseline_prediction_seconds"] = baseline[largest_errors]
    private_examples = {"ten_largest_target_examples": examples.to_dict(orient="records"),
                        "ten_largest_model_error_examples": error_examples.to_dict(orient="records")}
    (data / "audit_examples.json").write_text(json.dumps(private_examples, indent=2, allow_nan=False) + "\n")
    results["outliers"]["incident_level_examples"] = "Withheld from public report; retained in local data directory audit_examples.json."
    results["outliers"]["model_overprediction_over_one_hour_n"] = int(((predicted - actual) > 3600).sum())
    results["outliers"]["model_underprediction_over_one_hour_n"] = int(((actual - predicted) > 3600).sum())
    call_types = features["initial_call_type"].to_numpy()
    standby_codes = {"STNDBM": "MUNICIPAL UNIT STAND BY REQ", "STNDBY": "REQUEST FOR STAND-BY", "SPEVNT": "SPECIAL EVENT"}
    standby = np.isin(call_types, list(standby_codes))
    results["standby_special_event_analysis"] = {
        "definitions_from_local_official_dictionary": standby_codes,
        "n": int(standby.sum()), "severity_8_n": int((standby & (severity == "8")).sum()),
        "top_ten_targets_in_group": int(standby[largest].sum()),
        "comparison": compare(actual[standby], predicted[standby], baseline[standby])}
    results.update({"audited_at_utc": datetime.now(timezone.utc).isoformat(),
                    "model_sha256": original_model_hash, "sample_sha256": manifest["sample_sha256"],
                    "selected_baseline": best_baseline, "chronology": chronology,
                    "quality_exclusions": quality,
                    "boundary_exclusions": report["audit"]["label_availability_boundary_exclusions"],
                    "training_only_categories_verified": True, "validation_only_selection_verified": True,
                    "saved_predictions_and_report_metrics_verified": True,
                    "feature_availability": {
                        "initial_call_type": "Official dictionary explicitly assigns at incident creation.",
                        "initial_severity_level_code": "Official dictionary explicitly assigns at incident creation.",
                        "hour_day_of_week_month": "Derived only from incident creation timestamp, NYC wall time.",
                        "borough": "Incident location in official dictionary; creation-time availability is assumed, not independently provable from field history.",
                        "verification_limit": "Chronology and feature allowlist verified; no immutable as-of record versions or field edit histories available."},
                    "unseen_category_counts": report["unseen_category_counts"],
                    "methodology": "Frozen-checkpoint inference and fixed-baseline recomputation only; no fitting, tuning, label clipping or checkpoint changes.",
                    "sensitivity_scope": "Severity exclusions and target-tail thresholds are post-hoc descriptive audit slices, not replacement benchmarks or model selection.",
                    "audit_seconds": time.perf_counter() - started})
    if sha256(checkpoint / "model.ubj") != original_model_hash:
        raise ValueError("Model changed during audit")
    return results


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--data", type=Path, default=Path("data/ems/q1-2023"))
    parser.add_argument("--checkpoint", type=Path, default=Path("ml/ems/checkpoint"))
    parser.add_argument("--output", type=Path, default=Path("ml/ems/checkpoint/generalization_audit.json"))
    arguments = parser.parse_args()
    result = audit(arguments.data, arguments.checkpoint)
    arguments.output.write_text(json.dumps(result, indent=2, allow_nan=False) + "\n")
    print(json.dumps({key: result[key] for key in ("all", "excluding_severity_8", "per_severity",
                     "severity_8_share_of_absolute_error_reduction_percent", "severity_8_share_of_squared_error_reduction_percent")}, indent=2))
