import argparse
import json
import platform
import time
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd
import sklearn
import xgboost as xgb

from ml.ems.features import DEMO_INPUTS, FEATURES, MISSING, encode, feature_frame, fit_categories, prediction_frame
from ml.ems.fetch import END, START, sha256


VALIDATION_START = "2023-03-01"
TEST_START = "2023-03-16"
TARGET = "dispatch_response_seconds_qy"
LIMITATIONS = [
    "Historical NYC creation-to-first-assignment duration, not on-scene arrival or indoor travel.",
    "Not operationally or clinically validated; not transferable to indoor emergency response.",
    "One city and Q1 2023 only; temporal holdout does not establish future generalization.",
    "No staffing, workload, queue, weather or resource availability covariates.",
    "Borough assumes incident location was known at creation; source has no field-level change history.",
    "Initial call type is dispatch caller information, not a patient diagnosis.",
    "Only records with a valid, finite nonnegative dispatch target are evaluated; missing outcomes may be biased.",
    "Socrata historical records can be revised; exact replay requires the hashed local snapshot.",
    "Error percentiles are retrospective absolute errors, not calibrated prediction intervals.",
    "Aggregate gains conceal subgroup regressions and are largely driven by 158 severity-8 test incidents.",
    "March is unseen in training; month uses the tree missing branch for every validation/test incident.",
]


def prepare(raw: pd.DataFrame):
    frame = raw.copy()
    frame["incident_datetime"] = pd.to_datetime(frame["incident_datetime"], errors="coerce")
    frame[TARGET] = pd.to_numeric(frame[TARGET], errors="coerce")
    invalid_time = frame["incident_datetime"].isna()
    invalid_flag = frame["valid_dispatch_rspns_time_indc"].ne("Y")
    invalid_target = ~np.isfinite(frame[TARGET]) | frame[TARGET].lt(0)
    valid = ~(invalid_time | invalid_flag | invalid_target)
    if frame["incident_id"].isna().any() or frame["incident_id"].duplicated().any():
        raise ValueError("Missing or duplicate incident IDs; refusing potentially overlapping samples")
    if (~invalid_time & ((frame["incident_datetime"] < pd.Timestamp(START)) |
                         (frame["incident_datetime"] >= pd.Timestamp(END)))).any():
        raise ValueError("Records outside the declared historical window")
    audit = {"raw_rows": len(frame), "retained_rows": int(valid.sum()),
             "excluded_rows": int((~valid).sum()),
             "invalid_timestamp_rows": int(invalid_time.sum()),
             "invalid_dispatch_flag_rows": int(invalid_flag.sum()),
             "invalid_target_rows": int(invalid_target.sum()),
             "exclusion_counts_overlap": True,
             "raw_missing_by_column": raw.isna().sum().astype(int).to_dict()}
    return frame.loc[valid].sort_values(["incident_datetime", "incident_id"]).copy(), audit


def temporal_split(frame: pd.DataFrame):
    timestamps = frame["incident_datetime"]
    available_at = timestamps + pd.to_timedelta(frame[TARGET], unit="s")
    splits = {"train": frame.loc[(timestamps < VALIDATION_START) & (available_at < VALIDATION_START)],
              "validation": frame.loc[(timestamps >= VALIDATION_START) & (timestamps < TEST_START)
                                      & (available_at < TEST_START)],
              "test": frame.loc[(timestamps >= TEST_START) & (available_at < END)]}
    if any(part.empty for part in splits.values()):
        raise ValueError("All three chronological partitions must contain incidents")
    if not (splits["train"]["incident_datetime"].max() < splits["validation"]["incident_datetime"].min()
            <= splits["validation"]["incident_datetime"].max() < splits["test"]["incident_datetime"].min()):
        raise ValueError("Temporal partitions overlap")
    return splits


def metrics(actual, predicted):
    residual = np.asarray(predicted) - np.asarray(actual)
    errors = np.abs(residual)
    return {"n": len(errors), "mae_seconds": float(errors.mean()),
            "rmse_seconds": float(np.sqrt(np.mean(residual ** 2))),
            "mean_signed_error_seconds": float(residual.mean()),
            "absolute_error_percentiles_seconds": {
                str(percentile): float(np.percentile(errors, percentile))
                for percentile in (50, 75, 90, 95, 99)}}


def baseline_predictions(train: pd.DataFrame, other: pd.DataFrame):
    global_median = float(train[TARGET].median())
    result = {"train_global_median": np.full(len(other), global_median)}
    train_features = feature_frame(train)
    other_features = feature_frame(other)
    for name, columns in {
        "train_hour_weekday_median": ["hour", "day_of_week"],
        "train_severity_hour_weekday_median": ["initial_severity_level_code", "hour", "day_of_week"],
    }.items():
        grouped = train_features.assign(target=train[TARGET].to_numpy()).groupby(columns)["target"].agg(["median", "count"])
        lookup = {key: float(row["median"]) for key, row in grouped.iterrows() if row["count"] >= 30}
        result[name] = np.array([lookup.get(tuple(row), global_median)
                                 for row in other_features[columns].itertuples(index=False, name=None)])
    return result


def train(data: Path, output: Path):
    started = time.perf_counter()
    if output.exists() and any(output.iterdir()):
        raise ValueError("Use an empty model directory; benchmark results must not be silently overwritten")
    manifest = json.loads((data / "manifest.json").read_text())
    if sha256(data / "sample.jsonl") != manifest["sample_sha256"]:
        raise ValueError("Sample checksum mismatch")
    if (manifest["start_inclusive"], manifest["end_exclusive"]) != (START, END):
        raise ValueError("Manifest dates differ from the predeclared benchmark window")
    raw = pd.read_json(data / "sample.jsonl", lines=True, dtype=False)
    if len(raw) != manifest["row_count"]:
        raise ValueError("Manifest row count mismatch")
    frame, audit = prepare(raw)
    splits = temporal_split(frame)
    audit["label_availability_boundary_exclusions"] = {
        "train": int((frame["incident_datetime"] < VALIDATION_START).sum()) - len(splits["train"]),
        "validation": int(((frame["incident_datetime"] >= VALIDATION_START) &
                           (frame["incident_datetime"] < TEST_START)).sum()) - len(splits["validation"]),
        "test": int((frame["incident_datetime"] >= TEST_START).sum()) - len(splits["test"]),
    }
    frames = {name: feature_frame(part) for name, part in splits.items()}
    categories = fit_categories(frames["train"])
    matrices = {name: encode(part, categories) for name, part in frames.items()}
    baselines = {name: baseline_predictions(splits["train"], part)
                 for name, part in splits.items() if name != "train"}
    validation_baselines = {name: metrics(splits["validation"][TARGET], values)
                            for name, values in baselines["validation"].items()}
    baseline_name = min(validation_baselines, key=lambda name: validation_baselines[name]["mae_seconds"])
    candidates = []
    best_model = None
    best_mae = float("inf")
    for depth, estimators in [(3, 180), (5, 240), (7, 300)]:
        fit_started = time.perf_counter()
        model = xgb.XGBRegressor(objective="reg:absoluteerror", n_estimators=estimators,
                                 max_depth=depth, learning_rate=0.06, min_child_weight=30,
                                 subsample=1.0, colsample_bytree=1.0, random_state=2023,
                                 tree_method="hist", enable_categorical=True, n_jobs=4,
                                 feature_types=["c"] * len(FEATURES))
        model.fit(matrices["train"], splits["train"][TARGET].to_numpy())
        evaluation = metrics(splits["validation"][TARGET], np.maximum(0, model.predict(matrices["validation"])))
        candidates.append({"max_depth": depth, "n_estimators": estimators,
                           "fit_seconds": time.perf_counter() - fit_started,
                           "validation": evaluation})
        print(json.dumps(candidates[-1]), flush=True)
        if evaluation["mae_seconds"] < best_mae:
            best_mae = evaluation["mae_seconds"]
            best_model = model
    predictions = np.maximum(0, best_model.predict(matrices["test"]))
    test_metrics = metrics(splits["test"][TARGET], predictions)
    test_baselines = {name: metrics(splits["test"][TARGET], values)
                      for name, values in baselines["test"].items()}
    baseline = test_baselines[baseline_name]
    eligible = all(test_metrics[key] < baseline[key] for key in ["mae_seconds", "rmse_seconds"])
    severity = {}
    severity_values = frames["test"]["initial_severity_level_code"].to_numpy()
    for level in sorted(set(severity_values)):
        mask = severity_values == level
        severity[level] = {"model": metrics(splits["test"][TARGET].to_numpy()[mask], predictions[mask]),
                           "baseline": metrics(splits["test"][TARGET].to_numpy()[mask], baselines["test"][baseline_name][mask])}
    output.mkdir(parents=True, exist_ok=True)
    best_model.save_model(output / "model.ubj")
    (output / "categories.json").write_text(json.dumps(categories, indent=2) + "\n")
    report = {"model_id": "nyc-ems-dispatch-xgboost-q1-2023-v1", "target": TARGET,
              "target_definition": "Seconds from incident creation to first assignment; not arrival or travel.",
              "features": FEATURES, "missing_value_token": MISSING, "unknown_category_encoding": "NaN (tree missing branch)",
              "categories_fit_on": "train only", "model_sha256": sha256(output / "model.ubj"),
              "categories_sha256": sha256(output / "categories.json"),
              "provenance": manifest, "audit": audit,
              "splits": {name: {"n": len(part), "min_datetime": str(part["incident_datetime"].min()),
                                "max_datetime": str(part["incident_datetime"].max()),
                                "target_median_seconds": float(part[TARGET].median()),
                                "target_max_seconds": float(part[TARGET].max())} for name, part in splits.items()},
              "validation_candidates": candidates, "validation_baselines": validation_baselines,
              "selection_rule": "Lowest validation MAE; no refit or selection using test outcomes.",
              "label_availability_rule": "Creation time plus dispatch duration must be strictly before each partition's end; boundary-crossing labels excluded.",
              "unseen_category_counts": {name: {column: int((~part[column].isin(categories[column])).sum())
                                                for column in FEATURES} for name, part in frames.items()},
              "selected_parameters": {name: value for name, value in best_model.get_params().items()
                                      if value is not None and not (isinstance(value, float) and not np.isfinite(value))},
              "selected_baseline": baseline_name,
              "test": test_metrics, "test_baselines": test_baselines, "test_by_initial_severity": severity,
              "advisory_eligible": eligible,
              "eligibility_rule": "Strictly lower held-out MAE AND RMSE than validation-selected temporal baseline.",
              "limitations": LIMITATIONS,
              "versions": {"python": platform.python_version(), "pandas": pd.__version__,
                           "numpy": np.__version__, "sklearn": sklearn.__version__, "xgboost": xgb.__version__},
              "train_evaluate_seconds": time.perf_counter() - started}
    (output / "report.json").write_text(json.dumps(report, indent=2, allow_nan=False) + "\n")
    demo_inputs = dict(DEMO_INPUTS)
    demo_inputs["incident_datetime"] = datetime.fromisoformat(demo_inputs["incident_datetime"])
    demo_prediction = best_model.predict(encode(prediction_frame(**demo_inputs), categories))[0]
    demo_case = {"inputs": DEMO_INPUTS,
                 "example_kind": "illustrative_input_not_an_incident_record",
                 "expected_prediction_seconds": max(0.0, float(demo_prediction)),
                 "model_sha256": report["model_sha256"],
                 "note": "Fixed illustrative query, not a source incident. No observed target is published; weights and evaluation use real historical data."}
    (output / "demo_case.json").write_text(json.dumps(demo_case, indent=2) + "\n")
    splits["test"][["incident_id", "incident_datetime", TARGET]].assign(
        prediction_seconds=predictions,
        baseline_seconds=baselines["test"][baseline_name]).to_csv(data / "heldout_predictions.csv", index=False)
    print(json.dumps({"test": test_metrics, "baseline": baseline, "advisory_eligible": eligible}, indent=2))
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--data", type=Path, default=Path("data/ems/q1-2023"))
    parser.add_argument("--output", type=Path, default=Path("ml/ems/checkpoint"))
    arguments = parser.parse_args()
    train(arguments.data, arguments.output)
