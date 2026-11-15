"""Optional historical research inference; never connected to assignment or dispatch."""

import hashlib
import json
import logging
import math
from datetime import datetime
from pathlib import Path
from threading import Lock


DEFAULT_CHECKPOINT = Path(__file__).resolve().parents[3] / "ml" / "ems" / "checkpoint"
DISTINCTION = {
    "dispatch_prediction": "Historical NYC incident creation to first assignment, seconds.",
    "astar_distance": "Independent indoor route distance; not predicted by this model.",
    "simulated_indoor_travel": "Independent simulation travel estimate; not predicted or modified here.",
    "authority": "Read-only research advisory; no assignment, clinical, approval, dispatch or hardware authority.",
}
logger = logging.getLogger(__name__)


class EMSDispatchAdvisory:
    def __init__(self, enabled: bool = False, checkpoint: Path = DEFAULT_CHECKPOINT):
        self.enabled = enabled
        self.checkpoint = checkpoint
        self._model = None
        self._report = None
        self._categories = None
        self._lock = Lock()

    def _load(self):
        with self._lock:
            if self._model is not None:
                return
            import xgboost as xgb
            from ml.ems.features import FEATURES

            report = json.loads((self.checkpoint / "report.json").read_text())
            baseline = report["test_baselines"][report["selected_baseline"]]
            if not report["advisory_eligible"] or not all(
                report["test"][key] < baseline[key] for key in ("mae_seconds", "rmse_seconds")
            ):
                raise ValueError("Checkpoint did not pass the held-out benchmark gate")
            if report["features"] != FEATURES or report["target"] != "dispatch_response_seconds_qy":
                raise ValueError("Incompatible model contract")
            for name, hash_key in [("model.ubj", "model_sha256"), ("categories.json", "categories_sha256")]:
                if hashlib.sha256((self.checkpoint / name).read_bytes()).hexdigest() != report[hash_key]:
                    raise ValueError("Checkpoint checksum mismatch")
            categories = json.loads((self.checkpoint / "categories.json").read_text())
            model = xgb.XGBRegressor()
            model.load_model(self.checkpoint / "model.ubj")
            self._report = report
            self._categories = categories
            self._model = model

    def evaluate(self, incident_datetime: datetime | None = None,
                 initial_call_type: str | None = None,
                 initial_severity_level_code: str | None = None,
                 borough: str | None = None) -> dict:
        result = {"status": "disabled", "mode": "simulation_advisory", "read_only": True,
                  "prediction_seconds": None, "target": "dispatch_response_seconds_qy",
                  "model_id": None, "model_sha256": None, "provenance": None,
                  "benchmark": None, "warnings": [], "error_code": None,
                  "distinction": DISTINCTION, "limitations": []}
        if not self.enabled:
            result["error_code"] = "feature_disabled"
            return result
        try:
            self._load()
            report = self._report
            result.update(status="available", model_id=report["model_id"],
                          model_sha256=report["model_sha256"], provenance=report["provenance"],
                          benchmark={"test": report["test"], "selected_baseline": report["selected_baseline"],
                                     "baseline": report["test_baselines"][report["selected_baseline"]]},
                          limitations=report["limitations"])
            if incident_datetime is None:
                return result
            from ml.ems.features import FEATURES, MISSING, encode, prediction_frame

            frame = prediction_frame(incident_datetime, initial_call_type,
                                     initial_severity_level_code, borough)
            unknown = [name for name in FEATURES if frame.iloc[0][name] not in self._categories[name]]
            missing = [name for name in FEATURES if frame.iloc[0][name] == MISSING]
            if unknown:
                result["warnings"].append("Unseen categories use tree missing branches: " + ", ".join(unknown))
            if missing:
                result["warnings"].append("Missing inputs use explicit missing category: " + ", ".join(missing))
            if not (report["provenance"]["start_inclusive"] <= frame_time(incident_datetime)
                    < report["provenance"]["end_exclusive"]):
                result["warnings"].append("Timestamp outside Q1 2023; future/general geographic applicability is unvalidated.")
            prediction = float(self._model.predict(encode(frame, self._categories))[0])
            if not math.isfinite(prediction):
                raise ValueError("Nonfinite model output")
            result["prediction_seconds"] = max(0.0, prediction)
            return result
        except Exception:
            logger.warning("Historical EMS advisory unavailable", exc_info=True)
            result.update(status="unavailable", prediction_seconds=None, error_code="model_unavailable")
            return result


def frame_time(value: datetime) -> str:
    from zoneinfo import ZoneInfo

    if value.tzinfo is not None:
        value = value.astimezone(ZoneInfo("America/New_York")).replace(tzinfo=None)
    return value.isoformat()
