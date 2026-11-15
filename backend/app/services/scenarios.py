"""Read synthetic fixtures without maintaining mutable application state."""

import os
from pathlib import Path

from backend.app.models import Scenario


DEMO_SCENARIO_PATH = (
    Path(__file__).resolve().parents[3] / "shared" / "mock" / "scenario.demo.json"
)


def load_demo_scenario() -> Scenario:
    return Scenario.model_validate_json(DEMO_SCENARIO_PATH.read_text(encoding="utf-8"))


def camera_only_demo() -> bool:
    """AEGIS_DEMO_CAMERA_ONLY=true: the demo starts with no synthetic casualties.

    Every casualty then comes from a camera track the operator confirms.
    """
    return os.getenv("AEGIS_DEMO_CAMERA_ONLY", "false").strip().lower() == "true"
