import argparse
import json
import time
from datetime import datetime
from pathlib import Path

from backend.app.services.ems_dispatch import DEFAULT_CHECKPOINT, EMSDispatchAdvisory


def demo(checkpoint: Path = DEFAULT_CHECKPOINT):
    case = json.loads((checkpoint / "demo_case.json").read_text())
    inputs = dict(case["inputs"])
    inputs["incident_datetime"] = datetime.fromisoformat(inputs["incident_datetime"])
    started = time.perf_counter()
    result = EMSDispatchAdvisory(enabled=True, checkpoint=checkpoint).evaluate(**inputs)
    if result["status"] != "available" or result["prediction_seconds"] is None:
        raise RuntimeError("Actual model inference unavailable; no fallback prediction is permitted")
    if result["model_sha256"] != case["model_sha256"]:
        raise RuntimeError("Demo and checkpoint identities differ")
    if abs(result["prediction_seconds"] - case["expected_prediction_seconds"]) > 1e-4:
        raise RuntimeError("Loaded model prediction differs from frozen illustrative example")
    return {"inputs": case["inputs"], "example_kind": case["example_kind"],
            "inference_including_cold_load_seconds": time.perf_counter() - started,
            "advisory": result}


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--checkpoint", type=Path, default=DEFAULT_CHECKPOINT)
    print(json.dumps(demo(parser.parse_args().checkpoint), indent=2))
