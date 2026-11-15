from datetime import datetime
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd


FEATURES = ["initial_call_type", "initial_severity_level_code", "borough", "hour", "day_of_week", "month"]
MISSING = "__MISSING__"
DEMO_INPUTS = {"incident_datetime": "2023-03-20T12:00:00", "initial_call_type": "RESPIR",
               "initial_severity_level_code": "4", "borough": "BRONX"}


def feature_frame(records: pd.DataFrame) -> pd.DataFrame:
    timestamps = pd.to_datetime(records["incident_datetime"], errors="raise")
    result = records.reindex(columns=FEATURES[:3]).copy()
    result["hour"] = timestamps.dt.hour
    result["day_of_week"] = timestamps.dt.day_of_week
    result["month"] = timestamps.dt.month
    return result.fillna(MISSING).astype(str)


def fit_categories(frame: pd.DataFrame) -> dict[str, list[str]]:
    return {name: sorted(set(frame[name]) | {MISSING}) for name in FEATURES}


def encode(frame: pd.DataFrame, categories: dict[str, list[str]]) -> np.ndarray:
    encoded = []
    for name in FEATURES:
        lookup = {value: index for index, value in enumerate(categories[name])}
        encoded.append(frame[name].map(lookup).to_numpy(dtype=np.float32))
    return np.column_stack(encoded)


def prediction_frame(incident_datetime: datetime, initial_call_type: str | None,
                     initial_severity_level_code: str | None, borough: str | None) -> pd.DataFrame:
    if incident_datetime.tzinfo is not None:
        incident_datetime = incident_datetime.astimezone(ZoneInfo("America/New_York")).replace(tzinfo=None)
    return feature_frame(pd.DataFrame([{
        "incident_datetime": incident_datetime.isoformat(),
        "initial_call_type": initial_call_type,
        "initial_severity_level_code": initial_severity_level_code,
        "borough": borough,
    }]))
