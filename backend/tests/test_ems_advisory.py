import importlib.util
import json
import shutil
import subprocess
import sys
from datetime import datetime

import pytest
from fastapi.testclient import TestClient

from backend.app.main import create_app
from backend.app.services.ems_dispatch import DEFAULT_CHECKPOINT, EMSDispatchAdvisory
from backend.app.simulation.store import SimulationStore
from test_simulation_support import FakeClock


ENDPOINT = "/api/v1/simulations/advisories/ems-dispatch"
OPTIONAL_MODEL = pytest.mark.skipif(importlib.util.find_spec("xgboost") is None,
                                    reason="Optional EMS dependencies: uv sync --group ems")


def test_disabled_by_default_and_no_write_methods(monkeypatch):
    monkeypatch.delenv("AEGIS_EMS_ADVISORY_ENABLED", raising=False)
    application = create_app()
    application.state.ems_dispatch_advisory.checkpoint = DEFAULT_CHECKPOINT / "does-not-exist"
    with TestClient(application) as client:
        response = client.get(ENDPOINT)
        assert response.status_code == 200
        assert response.json()["status"] == "disabled"
        assert response.json()["prediction_seconds"] is None
        assert response.json()["error_code"] == "feature_disabled"
        assert client.post(ENDPOINT, json={}).status_code == 405
        assert client.put(ENDPOINT, json={}).status_code == 405
        assert client.delete(ENDPOINT).status_code == 405


def test_default_app_does_not_import_optional_ml_dependencies(monkeypatch):
    monkeypatch.setenv("AEGIS_EMS_ADVISORY_ENABLED", "false")
    subprocess.run([sys.executable, "-c",
                    "import sys; from backend.app.main import create_app; create_app(); "
                    "assert 'xgboost' not in sys.modules; assert 'ml.ems.features' not in sys.modules"],
                   check=True)


def test_enabled_missing_checkpoint_fails_without_prediction(monkeypatch, tmp_path):
    monkeypatch.setenv("AEGIS_EMS_ADVISORY_ENABLED", "true")
    monkeypatch.setenv("AEGIS_EMS_MODEL_DIR", str(tmp_path))
    with TestClient(create_app()) as client:
        response = client.get(ENDPOINT)
    assert response.status_code == 503
    assert response.json()["status"] == "unavailable"
    assert response.json()["prediction_seconds"] is None
    assert response.json()["error_code"] == "model_unavailable"


@OPTIONAL_MODEL
def test_real_weights_match_frozen_demo_and_do_not_mutate_simulation(monkeypatch):
    monkeypatch.setenv("AEGIS_EMS_ADVISORY_ENABLED", "true")
    case = json.loads((DEFAULT_CHECKPOINT / "demo_case.json").read_text())
    assert case["example_kind"] == "illustrative_input_not_an_incident_record"
    assert "historical_observed_target_seconds" not in case
    application = create_app()
    application.state.simulation_store = SimulationStore(clock=FakeClock())
    with TestClient(application) as client:
        assert client.post("/api/v1/simulations/demo/reset", json={}).status_code == 201
        before = client.get("/api/v1/simulations/demo/state").json()
        events = client.get("/api/v1/simulations/demo/events").json()
        response = client.get(ENDPOINT, params=case["inputs"])
        assert response.status_code == 200
        result = response.json()
        assert result["prediction_seconds"] == pytest.approx(case["expected_prediction_seconds"], abs=1e-4)
        assert result["model_sha256"] == case["model_sha256"]
        assert result["provenance"]["row_count"] == 377606
        assert result["read_only"] is True
        assert "astar_distance" in result["distinction"]
        assert client.get("/api/v1/simulations/demo/state").json() == before
        assert client.get("/api/v1/simulations/demo/events").json() == events
        assert client.get(ENDPOINT, params={"incident_datetime": "not-a-date"}).status_code == 422


@OPTIONAL_MODEL
def test_unknown_missing_and_out_of_window_inputs_are_explicit():
    result = EMSDispatchAdvisory(enabled=True).evaluate(
        datetime(2026, 11, 14), initial_call_type="UNSEEN_TYPE", borough=None)
    assert result["status"] == "available"
    assert result["prediction_seconds"] >= 0
    assert any("Unseen" in warning for warning in result["warnings"])
    assert any("Missing" in warning for warning in result["warnings"])
    assert any("outside Q1 2023" in warning for warning in result["warnings"])


@OPTIONAL_MODEL
@pytest.mark.parametrize("failure", ["eligibility", "model_hash", "categories_hash"])
def test_checkpoint_integrity_and_benchmark_gate_fail_closed(tmp_path, failure):
    checkpoint = tmp_path / "checkpoint"
    shutil.copytree(DEFAULT_CHECKPOINT, checkpoint)
    report_file = checkpoint / "report.json"
    report = json.loads(report_file.read_text())
    if failure == "eligibility":
        report["advisory_eligible"] = False
        report_file.write_text(json.dumps(report))
    elif failure == "model_hash":
        (checkpoint / "model.ubj").write_bytes(b"tampered")
    else:
        (checkpoint / "categories.json").write_text("{}")
    result = EMSDispatchAdvisory(enabled=True, checkpoint=checkpoint).evaluate(datetime(2023, 3, 20))
    assert result["status"] == "unavailable"
    assert result["prediction_seconds"] is None


@OPTIONAL_MODEL
def test_demo_runs_inference_instead_of_returning_saved_prediction():
    from ml.ems.demo import demo

    result = demo()
    assert result["advisory"]["status"] == "available"
    assert result["advisory"]["prediction_seconds"] is not None
    assert result["inference_including_cold_load_seconds"] > 0
