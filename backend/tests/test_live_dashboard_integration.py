"""Live neural intelligence dashboard integration tests.

Tests the full 10-step lifecycle against an isolated scenario:
1. Neural shadow activation with genuine checkpoints.
2. Initial scene state & verify no_proposal status before first replan.
3. Generate initial OR-Tools proposal via replan.
4. Live side-by-side comparison query via GET /neural-comparison.
5. Objective metrics and disagreement analysis verification (OR-Tools, MLP, GNN).
6. Scene mutation & automatic stale detection (scene_revision bumped).
7. Stale comparison status verification.
8. Fresh recommendation generation (replan) & comparison refresh at new revision.
9. Operator approval gate operates exclusively on OR-Tools proposal.
10. Dispatch operates strictly on authoritative approval & assignments (neural models read-only).
"""

import os
import pytest
from fastapi.testclient import TestClient

torch = pytest.importorskip("torch", reason="Optional PyTorch dependency required for neural shadow live tests")

from backend.app.intelligence.neural_shadow import DEFAULT_WEIGHTS_DIR
from backend.app.main import create_app
from backend.app.services.scenarios import load_demo_scenario

_weights_available = (DEFAULT_WEIGHTS_DIR / "mlp_seed42.pt").is_file() and (DEFAULT_WEIGHTS_DIR / "gnn_seed42.pt").is_file()
if not _weights_available:
    pytest.skip("Neural shadow checkpoints (mlp_seed42.pt, gnn_seed42.pt) not provisioned in weights/", allow_module_level=True)


@pytest.fixture
def test_setup(monkeypatch):
    monkeypatch.setenv("AEGIS_NEURAL_SHADOW_ENABLED", "true")
    app = create_app()
    app.state.simulation_store.neural_shadow_enabled = True
    app.state.simulation_store.auto_recommend = False
    client = TestClient(app)
    scenario_id = "test_live_dashboard"

    # Initialize with isolated scenario copy
    scenario = load_demo_scenario()
    scenario.id = scenario_id
    r_init = client.post(
        f"/api/v1/simulations/{scenario_id}/reset",
        json={"mode": "simulation", "scenario": scenario.model_dump(mode="json")},
    )
    assert r_init.status_code == 201

    yield client, scenario_id

    client.post(
        f"/api/v1/simulations/{scenario_id}/reset",
        json={"mode": "simulation", "scenario": scenario.model_dump(mode="json")},
    )


def test_live_dashboard_10_step_lifecycle(test_setup):
    client, scenario_id = test_setup

    # Step 1: Initial state check - no proposal yet
    resp = client.get(f"/api/v1/simulations/{scenario_id}/neural-comparison")
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "no_proposal"
    assert data["context"] == "current"
    assert data["authorizes_dispatch"] is False
    assert data["read_only"] is True

    # Step 2: Generate authoritative OR-Tools proposal
    replan_resp = client.post(
        f"/api/v1/simulations/{scenario_id}/replan",
        json={"actor": "operator"},
    )
    assert replan_resp.status_code == 201
    state1 = client.get(f"/api/v1/simulations/{scenario_id}/state").json()
    proposal_id_1 = state1["current_proposal_id"]
    assert proposal_id_1 is not None
    scene_rev_1 = state1["scene_revision"]

    # Step 3: Fetch live neural comparison
    comp_resp_1 = client.get(f"/api/v1/simulations/{scenario_id}/neural-comparison")
    assert comp_resp_1.status_code == 200
    comp1 = comp_resp_1.json()

    # Step 4: Verify 3-model side-by-side predictions
    assert comp1["status"] == "available"
    assert comp1["context"] == "current"
    assert comp1["scene_revision"] == scene_rev_1
    assert comp1["proposal_id"] == proposal_id_1
    assert comp1["authorizes_dispatch"] is False
    assert comp1["read_only"] is True

    # Check OR-Tools Authoritative
    ot = comp1["ortools_summary"]
    assert ot is not None
    assert ot["authoritative"] is True
    assert ot["assigned_count"] == 2
    assert ot["travel_distance_m"] == pytest.approx(27.83, abs=0.1)
    assert ot["hard_constraint_validation"]["valid"] is True
    assert ot["hard_constraint_validation"]["violation_count"] == 0

    # Check Pairwise MLP Advisory
    mlp = comp1["mlp_prediction"]
    assert mlp is not None
    assert mlp["available"] is True
    assert mlp["status"] == "success"
    assert mlp["authorizes_dispatch"] is False
    assert mlp["assigned_count"] == 2
    assert mlp["hard_constraint_validation"]["valid"] is True
    assert mlp["hard_constraint_validation"]["violation_count"] == 0
    assert mlp["comparison_vs_ortools"]["matches_ortools_pairs"] is True
    assert mlp["inference_duration_ms"] > 0

    # Check Bipartite GNN Advisory
    gnn = comp1["gnn_prediction"]
    assert gnn is not None
    assert gnn["available"] is True
    assert gnn["status"] == "success"
    assert gnn["authorizes_dispatch"] is False
    assert gnn["assigned_count"] == 2
    assert gnn["hard_constraint_validation"]["valid"] is True
    assert gnn["hard_constraint_validation"]["violation_count"] == 0
    assert gnn["inference_duration_ms"] > 0

    # Check Disagreement Analysis (grounded deltas)
    disagreement = comp1["disagreement_analysis"]
    assert disagreement is not None
    assert disagreement["models_agree_with_ortools"] is False
    assert len(disagreement["differing_assignments"]) > 0
    assert "Divergent" in disagreement["summary"]
    assert "GNN" in comp1["voice_summary"]
    assert "3.0m" in comp1["voice_summary"]

    # Step 5: Mutate scene (update casualty assessment)
    first_casualty = state1["scenario"]["casualties"][0]["id"]
    assess_resp = client.post(
        f"/api/v1/simulations/{scenario_id}/casualties/{first_casualty}/assessment",
        json={
            "severity": "immediate",
            "source": "human",
            "assessed_by": "field-medic",
            "assessed_at": "2026-11-14T12:00:00Z",
        },
        headers={"X-Idempotency-Key": "test-dash-assess-1"},
    )
    assert assess_resp.status_code == 201

    state2 = client.get(f"/api/v1/simulations/{scenario_id}/state").json()
    assert state2["scene_revision"] > scene_rev_1

    # Step 6: Stale check - previous comparison must be visibly invalidated
    comp_resp_stale = client.get(f"/api/v1/simulations/{scenario_id}/neural-comparison")
    assert comp_resp_stale.status_code == 200
    comp_stale = comp_resp_stale.json()
    assert comp_stale["status"] == "stale"
    assert comp_stale["context"] == "stale"
    assert "scene_revision_changed" in comp_stale["stale_reasons"]
    assert comp_stale["scene_revision"] == scene_rev_1  # reflects evaluation revision

    # Step 7: Generate fresh recommendation for revised scene
    replan_resp_2 = client.post(
        f"/api/v1/simulations/{scenario_id}/replan",
        json={"actor": "operator"},
    )
    assert replan_resp_2.status_code == 201
    state3 = client.get(f"/api/v1/simulations/{scenario_id}/state").json()
    proposal_id_2 = state3["current_proposal_id"]
    assert proposal_id_2 != proposal_id_1

    # Step 8: Confirm refreshed comparison matches new scene
    comp_resp_2 = client.get(f"/api/v1/simulations/{scenario_id}/neural-comparison")
    assert comp_resp_2.status_code == 200
    comp2 = comp_resp_2.json()
    assert comp2["status"] == "available"
    assert comp2["context"] == "current"
    assert comp2["proposal_id"] == proposal_id_2
    assert comp2["scene_revision"] == state3["scene_revision"]

    # Step 9: Operator approval gate operates exclusively on OR-Tools proposal
    proposal = next(p for p in state3["proposals"] if p["id"] == proposal_id_2)
    approve_resp = client.post(
        f"/api/v1/simulations/{scenario_id}/proposals/{proposal_id_2}/decision",
        json={
            "mode": "simulation",
            "decision": "approve",
            "proposal_revision": proposal["revision"],
            "actor": "commander",
        },
        headers={"Idempotency-Key": "test-dash-approve-1"},
    )
    assert approve_resp.status_code == 201
    state_after_approval = approve_resp.json()["state"]
    approval_id = state_after_approval["approvals"][-1]["id"]
    assert approval_id is not None

    # Step 10: Dispatch operates strictly on authoritative approval & assignments
    dispatch_resp = client.post(
        f"/api/v1/simulations/{scenario_id}/dispatches",
        json={
            "mode": "simulation",
            "proposal_id": proposal_id_2,
            "proposal_revision": proposal["revision"],
            "approval_id": approval_id,
            "actor": "commander",
        },
        headers={"Idempotency-Key": "test-dash-dispatch-1"},
    )
    assert dispatch_resp.status_code == 201

    state_after_dispatch = dispatch_resp.json()["state"]
    ot_pairs = set(tuple(p) for p in comp2["ortools_summary"]["selected_pairs"])
    dispatched_pairs = {
        (a["casualty_id"], a["responder_id"])
        for a in state_after_dispatch["assignments"]
        if a["status"] in ("dispatched", "delivered", "acknowledged")
    }
    assert ot_pairs == dispatched_pairs
