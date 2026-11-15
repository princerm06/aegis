"""Comprehensive tests for AEGIS Live Neural Shadow Advisory Integration.

Proves:
1. Correct checkpoint loading and SHA-256 digest verification.
2. Matching feature schemas (Pair: 48, Casualty Node: 18, Responder Node: 12).
3. Genuine inference rather than fixture playback (logits change dynamically with inputs).
4. Accurate translation from optimization requests to model features without teacher label leakage.
5. MLP and GNN output actual decoded assignments via constrained matching.
6. Independent constraint validation guarantees zero committed violations.
7. Infeasible neural edges cannot become approved assignments.
8. Neural results do not alter OR-Tools output.
9. Exact scene-revision association.
10. Stale comparisons are detected and flagged when scene revision changes.
11. Missing weights fail safely with explicit unavailable status.
12. Disabled feature flag leaves normal optimization completely unaffected.
13. Neither neural model can trigger approval or dispatch (authorizes_dispatch=False).
14. End-to-end API lifecycle across multiple scene revisions.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient
import numpy as np
import pytest
torch = pytest.importorskip("torch", reason="Optional PyTorch dependency required for neural shadow tests")

def _checkpoints_available() -> bool:
    return (DEFAULT_WEIGHTS_DIR / "mlp_seed42.pt").is_file() and (DEFAULT_WEIGHTS_DIR / "gnn_seed42.pt").is_file()

checkpoints_required = pytest.mark.skipif(
    not _checkpoints_available(),
    reason="Neural shadow checkpoints not provisioned in experiments/neural_assignment/weights/",
)

from backend.app.intelligence.neural_shadow import (
    DEFAULT_WEIGHTS_DIR,
    NeuralComparisonReport,
    NeuralShadowService,
    get_file_sha256,
    is_neural_shadow_enabled,
)
from backend.app.main import create_app
from backend.app.models import MedicalAssessment, Position
from backend.app.simulation.store import (
    DEMO_SCENARIO_ID,
    DecisionRequest,
    DispatchRequest,
    ReplanRequest,
    ResetRequest,
    SimulationStore,
)
from neural_assignment.decoder import count_unfiltered_invalid_proposals, decode_and_validate
from neural_assignment.features import (
    CASUALTY_NODE_FEATURE_DIM,
    PAIR_FEATURE_DIM,
    RESPONDER_NODE_FEATURE_DIM,
    extract_features_from_request,
)
from neural_assignment.models import BipartiteAssignmentGNN, PairwiseAssignmentMLP
from neural_assignment.train import load_trained_model
from neural_assignment.validator import validate_assignments


EXPECTED_WEIGHT_DIGESTS = {
    "mlp_seed42.pt": "3df9bd6a5f9e",
    "mlp_seed43.pt": "dbfeede473d8",
    "mlp_seed44.pt": "1614ad8d5e37",
    "gnn_seed42.pt": "fb6ba734ded8",
    "gnn_seed43.pt": "aa0ddad994d5",
    "gnn_seed44.pt": "ab459dcd37ff",
}


# -----------------------------------------------------------------------------
# 1. Checkpoint Loading & Cryptographic Integrity Tests
# -----------------------------------------------------------------------------


@checkpoints_required
def test_checkpoints_exist_and_match_pr6_report_digests():
    """Verify that all 6 trained checkpoints exist and have exact digests from PR #6 report."""
    weights_dir = DEFAULT_WEIGHTS_DIR
    assert weights_dir.is_dir(), f"Weights directory missing: {weights_dir}"

    for filename, expected_prefix in EXPECTED_WEIGHT_DIGESTS.items():
        file_path = weights_dir / filename
        assert file_path.is_file(), f"Missing checkpoint: {filename}"
        digest = get_file_sha256(file_path)
        assert digest.startswith(
            expected_prefix
        ), f"Checksum mismatch for {filename}: expected prefix {expected_prefix}, got {digest}"


@checkpoints_required
def test_checkpoint_loading_weights_only_and_metadata():
    """Verify that checkpoints load safely with weights_only=True and have matching architecture."""
    weights_dir = DEFAULT_WEIGHTS_DIR
    mlp_model, mlp_ck = load_trained_model(weights_dir / "mlp_seed42.pt")
    gnn_model, gnn_ck = load_trained_model(weights_dir / "gnn_seed42.pt")

    assert isinstance(mlp_model, PairwiseAssignmentMLP)
    assert isinstance(gnn_model, BipartiteAssignmentGNN)
    assert mlp_ck["model_kind"] == "mlp"
    assert gnn_ck["model_kind"] == "gnn"
    assert mlp_ck["seed"] == 42
    assert gnn_ck["seed"] == 42
    assert mlp_ck["feature_schema_version"] == "aegis-pair-features-v1.0"
    assert gnn_ck["feature_schema_version"] == "aegis-pair-features-v1.0"


# -----------------------------------------------------------------------------
# 2. Feature Schema & Leakage Prevention Tests
# -----------------------------------------------------------------------------


def test_feature_schema_dimensions():
    """Verify feature dimension contracts match models and PR #6 schema."""
    assert PAIR_FEATURE_DIM == 48
    assert CASUALTY_NODE_FEATURE_DIM == 18
    assert RESPONDER_NODE_FEATURE_DIM == 12


def test_no_teacher_label_leakage_in_feature_extraction():
    """Verify that feature extraction consumes only pre-assignment request fields without solver results."""
    store = SimulationStore()
    store.reset(DEMO_SCENARIO_ID, ResetRequest())
    session = store._session(DEMO_SCENARIO_ID)
    job = store._prepare(session, store._now(), "test_leakage")
    req = job.request

    sf1 = extract_features_from_request(req)

    # Solve with OR-Tools
    res = store.solver(req)
    assert len(res.assignments) > 0

    # Extract again after solving - features must be completely invariant
    sf2 = extract_features_from_request(req)

    np.testing.assert_array_equal(sf1.pair_features, sf2.pair_features)
    np.testing.assert_array_equal(sf1.casualty_node_features, sf2.casualty_node_features)
    np.testing.assert_array_equal(sf1.responder_node_features, sf2.responder_node_features)
    np.testing.assert_array_equal(sf1.feasible_mask, sf2.feasible_mask)


# -----------------------------------------------------------------------------
# 3. Genuine Inference & Dynamic Computation Tests
# -----------------------------------------------------------------------------


@checkpoints_required
def test_genuine_inference_changes_dynamically_with_scene_inputs():
    """Verify genuine model forward pass: changing casualty positions dynamically alters neural logits."""
    weights_dir = DEFAULT_WEIGHTS_DIR
    mlp_model, _ = load_trained_model(weights_dir / "mlp_seed42.pt")
    gnn_model, _ = load_trained_model(weights_dir / "gnn_seed42.pt")

    store = SimulationStore()
    store.reset(DEMO_SCENARIO_ID, ResetRequest())
    session = store._session(DEMO_SCENARIO_ID)
    job = store._prepare(session, store._now(), "test_inference")
    req1 = job.request

    sf1 = extract_features_from_request(req1)
    p1 = torch.from_numpy(sf1.pair_features)
    c1 = torch.from_numpy(sf1.casualty_node_features)
    r1 = torch.from_numpy(sf1.responder_node_features)
    f1 = torch.from_numpy(sf1.feasible_mask)

    with torch.no_grad():
        mlp_logits1 = mlp_model(p1, c1, r1, f1).cpu().numpy()
        gnn_logits1 = gnn_model(p1, c1, r1, f1).cpu().numpy()

    # Move a casualty position in the scenario
    req2 = req1.model_copy(deep=True)
    req2.scenario.casualties[0].position = Position(frame_id=req1.scenario.casualties[0].position.frame_id, x_m=18.0, y_m=18.0)
    sf2 = extract_features_from_request(req2)
    p2 = torch.from_numpy(sf2.pair_features)
    c2 = torch.from_numpy(sf2.casualty_node_features)
    r2 = torch.from_numpy(sf2.responder_node_features)
    f2 = torch.from_numpy(sf2.feasible_mask)

    with torch.no_grad():
        mlp_logits2 = mlp_model(p2, c2, r2, f2).cpu().numpy()
        gnn_logits2 = gnn_model(p2, c2, r2, f2).cpu().numpy()

    assert not np.allclose(mlp_logits1, mlp_logits2), "MLP logits did not change with altered scene"
    assert not np.allclose(gnn_logits1, gnn_logits2), "GNN logits did not change with altered scene"


# -----------------------------------------------------------------------------
# 4. Constrained Decoding & Independent Validator Safety Tests
# -----------------------------------------------------------------------------


def test_independent_validator_and_zero_committed_violations():
    """Verify that decoded neural assignments pass independent hard-constraint validation with 0 violations."""
    service = NeuralShadowService()
    store = SimulationStore()
    store.reset(DEMO_SCENARIO_ID, ResetRequest())
    session = store._session(DEMO_SCENARIO_ID)
    job = store._prepare(session, store._now(), "test_val")
    req = job.request

    ot_res = store.solver(req)
    comparison = service.run_inference(req, ot_res.assignments, ot_res.runtime_ms)

    assert comparison.available is True
    assert comparison.mlp_prediction.available is True
    assert comparison.gnn_prediction.available is True

    # Check committed violations are strictly 0
    assert comparison.mlp_prediction.hard_constraint_validation.violation_count == 0
    assert comparison.mlp_prediction.hard_constraint_validation.valid is True
    assert comparison.gnn_prediction.hard_constraint_validation.violation_count == 0
    assert comparison.gnn_prediction.hard_constraint_validation.valid is True

    # Check dispatch authorization is strictly False
    assert comparison.authorizes_dispatch is False
    assert comparison.mlp_prediction.authorizes_dispatch is False
    assert comparison.gnn_prediction.authorizes_dispatch is False


def test_adversarial_infeasible_edges_cannot_become_approved_assignments():
    """Verify that even with an artificially boosted adversarial logit on an infeasible pair, the hard gate filters it."""
    store = SimulationStore()
    store.reset(DEMO_SCENARIO_ID, ResetRequest())
    session = store._session(DEMO_SCENARIO_ID)
    job = store._prepare(session, store._now(), "test_adversarial")
    req = job.request

    sf = extract_features_from_request(req)
    infeasible_indices = np.where(~sf.feasible_mask)

    if len(infeasible_indices[0]) > 0:
        bad_i, bad_j = infeasible_indices[0][0], infeasible_indices[1][0]
        # Construct adversarial logits with enormous score on the illegal edge
        adv_logits = np.zeros_like(sf.pair_features[:, :, 0])
        adv_logits[bad_i, bad_j] = 999.0

        # Unfiltered diagnostic detects the illegal proposal
        unfiltered_invalid = count_unfiltered_invalid_proposals(adv_logits, sf, threshold=0.0)
        assert unfiltered_invalid >= 1

        # Constrained decoder completely ignores the infeasible edge
        decoded = decode_and_validate(adv_logits, req, scene_features=sf, decoder="matching")
        bad_pair = (sf.casualty_ids[bad_i], sf.responder_ids[bad_j])
        assert bad_pair not in decoded.selected_pairs
        assert decoded.committed_constraint_violations == 0


# -----------------------------------------------------------------------------
# 5. Non-Interference with Authoritative Solver & Dispatch
# -----------------------------------------------------------------------------


def test_neural_shadow_does_not_alter_ortools_output():
    """Verify that OR-Tools proposal is 100% identical with neural shadow enabled vs disabled."""
    # Run with disabled
    store_disabled = SimulationStore(neural_shadow_enabled=False)
    store_disabled.reset(DEMO_SCENARIO_ID, ResetRequest())
    store_disabled.replan(DEMO_SCENARIO_ID, ReplanRequest(actor="operator"))
    p_disabled = store_disabled.snapshot(DEMO_SCENARIO_ID).proposals[-1]

    # Run with enabled
    store_enabled = SimulationStore(neural_shadow_enabled=True)
    store_enabled.reset(DEMO_SCENARIO_ID, ResetRequest())
    store_enabled.replan(DEMO_SCENARIO_ID, ReplanRequest(actor="operator"))
    p_enabled = store_enabled.snapshot(DEMO_SCENARIO_ID).proposals[-1]

    assert p_disabled is not None
    assert p_enabled is not None

    pairs_disabled = sorted((a.casualty_id, a.responder_id) for a in p_disabled.assignments)
    pairs_enabled = sorted((a.casualty_id, a.responder_id) for a in p_enabled.assignments)
    assert pairs_disabled == pairs_enabled
    assert p_disabled.objective_values == p_enabled.objective_values


def test_neural_shadow_cannot_authorize_or_dispatch():
    """Verify that approval and dispatch require an exact OR-Tools proposal revision."""
    store = SimulationStore(neural_shadow_enabled=True)
    store.reset(DEMO_SCENARIO_ID, ResetRequest())
    store.replan(DEMO_SCENARIO_ID, ReplanRequest(actor="operator"))
    proposal = store.snapshot(DEMO_SCENARIO_ID).proposals[-1]
    assert proposal is not None

    # Retrieve neural comparison
    comp: NeuralComparisonReport = store.neural_comparison(DEMO_SCENARIO_ID)
    assert comp.authorizes_dispatch is False
    assert comp.authoritative_engine == "ortools"

    # Attempt to dispatch using a fake proposal ID from neural model should fail
    with pytest.raises(Exception):
        store.dispatch(
            DEMO_SCENARIO_ID,
            DispatchRequest(
                proposal_id="neural-shadow-fake",
                revision=proposal.revision,
                actor="operator",
            ),
            idempotency_key="disp-fake",
        )

    # Approve legitimate OR-Tools proposal
    appr_resp = store.decide(
        DEMO_SCENARIO_ID,
        proposal.id,
        DecisionRequest(decision="approve", proposal_revision=proposal.revision, actor="operator"),
        idempotency_key="appr-1",
    )
    assert appr_resp.result["status"] == "active"
    approval_id = appr_resp.result["id"]

    # Dispatch legitimate proposal succeeds
    disp_resp = store.dispatch(
        DEMO_SCENARIO_ID,
        DispatchRequest(
            proposal_id=proposal.id,
            proposal_revision=proposal.revision,
            approval_id=approval_id,
            actor="operator",
        ),
        idempotency_key="disp-1",
    )
    assert disp_resp.action == "dispatch"


# -----------------------------------------------------------------------------
# 6. Staleness & Revision Association Tests
# -----------------------------------------------------------------------------


@checkpoints_required
def test_comparison_detects_scene_revision_changes_as_stale_and_refreshes():
    """Verify that scene mutations mark previous comparison as stale, and replan refreshes it."""
    # Disable auto_recommend so we observe the stale state before manual replan
    store = SimulationStore(neural_shadow_enabled=True, auto_recommend=False)
    store.reset(DEMO_SCENARIO_ID, ResetRequest())
    store.replan(DEMO_SCENARIO_ID, ReplanRequest(actor="operator"))
    proposal = store.snapshot(DEMO_SCENARIO_ID).proposals[-1]
    assert proposal is not None

    comp_current: NeuralComparisonReport = store.neural_comparison(DEMO_SCENARIO_ID)
    assert comp_current.context == "current"
    assert comp_current.status == "available"
    assert comp_current.scene_revision == proposal.revision

    # Mutate scene revision: record medical assessment for casualty-001
    store.record_assessment(
        DEMO_SCENARIO_ID,
        "casualty-001",
        MedicalAssessment(
            severity="delayed",
            source="human",
            assessed_at=store._now(),
            assessed_by="medic-1",
        ),
    )

    comp_after_mutation: NeuralComparisonReport = store.neural_comparison(DEMO_SCENARIO_ID)
    assert comp_after_mutation.context == "stale"
    assert comp_after_mutation.status == "stale"
    assert "scene_revision_changed" in comp_after_mutation.stale_reasons

    # Explicit replan on the new revision produces fresh, non-stale predictions
    store.replan(DEMO_SCENARIO_ID, ReplanRequest(actor="operator"))
    comp_refreshed: NeuralComparisonReport = store.neural_comparison(DEMO_SCENARIO_ID)
    assert comp_refreshed.context == "current"
    assert comp_refreshed.status == "available"
    assert comp_refreshed.scene_revision > comp_current.scene_revision


# -----------------------------------------------------------------------------
# 7. Safe Failure Modes & Feature Flag Tests
# -----------------------------------------------------------------------------


def test_corrupt_or_modified_checkpoint_is_rejected_and_fails_safely(tmp_path):
    """Verify that modified/corrupted checkpoints are strictly rejected by auditor and fail safely in shadow service."""
    # 1. Test verification rejection with exact equality
    corrupt_weights_dir = tmp_path / "weights"
    corrupt_weights_dir.mkdir()
    corrupt_mlp = corrupt_weights_dir / "mlp_seed42.pt"
    corrupt_mlp.write_bytes(b"corrupted_tensor_weight_data_invalid_hash")

    scripts_dir = str(DEFAULT_WEIGHTS_DIR.parent / "scripts")
    sys_path_added = False
    if scripts_dir not in sys.path:
        sys.path.insert(0, scripts_dir)
        sys_path_added = True
    try:
        from provision_checkpoints import verify_checkpoints
        assert verify_checkpoints(corrupt_weights_dir, target_seeds=[42]) is False
    finally:
        if sys_path_added and scripts_dir in sys.path:
            sys.path.remove(scripts_dir)

    # 2. Test runtime service safety: invalid checkpoint does not crash or produce false predictions
    service = NeuralShadowService(weights_dir=corrupt_weights_dir)
    store = SimulationStore(neural_shadow_enabled=True, neural_shadow_service=service)
    store.reset(DEMO_SCENARIO_ID, ResetRequest())
    store.replan(DEMO_SCENARIO_ID, ReplanRequest(actor="operator"))

    comp: NeuralComparisonReport = store.neural_comparison(DEMO_SCENARIO_ID)
    assert comp.available is False
    assert comp.status == "checkpoints_unavailable"
    assert comp.authorizes_dispatch is False
    assert comp.mlp_prediction is None
    assert comp.gnn_prediction is None
    snapshot = store.snapshot(DEMO_SCENARIO_ID)
    assert snapshot.current_proposal_id is not None
    assert snapshot.proposals[-1].engine == "ortools"


def test_missing_weights_fails_safely():
    """Verify that missing weights directories return explicit unavailable status without raising 500."""
    fake_dir = Path("/tmp/nonexistent_weights_dir_aegis_test")
    service = NeuralShadowService(weights_dir=fake_dir)
    store = SimulationStore(neural_shadow_enabled=True, neural_shadow_service=service)
    store.reset(DEMO_SCENARIO_ID, ResetRequest())
    store.replan(DEMO_SCENARIO_ID, ReplanRequest(actor="operator"))

    comp: NeuralComparisonReport = store.neural_comparison(DEMO_SCENARIO_ID)
    assert comp.available is False
    assert comp.status == "checkpoints_unavailable"
    assert "unavailable" in comp.message.lower()


def test_disabled_feature_flag_returns_disabled_report():
    """Verify that AEGIS_NEURAL_SHADOW_ENABLED=false returns clean disabled status."""
    store = SimulationStore(neural_shadow_enabled=False)
    store.reset(DEMO_SCENARIO_ID, ResetRequest())
    store.replan(DEMO_SCENARIO_ID, ReplanRequest(actor="operator"))

    comp: NeuralComparisonReport = store.neural_comparison(DEMO_SCENARIO_ID)
    assert comp.available is False
    assert comp.status == "disabled"
    assert comp.authoritative_engine == "ortools"
    assert "disabled" in comp.message.lower()


# -----------------------------------------------------------------------------
# 8. End-to-End FastAPI Client Test
# -----------------------------------------------------------------------------


@checkpoints_required
def test_e2e_api_neural_comparison_endpoint():
    """Full HTTP test of GET /api/v1/simulations/{scenario_id}/neural-comparison."""
    app = create_app()
    # Enable shadow inference on app's simulation store
    app.state.simulation_store.neural_shadow_enabled = True

    client = TestClient(app)

    # 1. Reset simulation
    r_reset = client.post(f"/api/v1/simulations/{DEMO_SCENARIO_ID}/reset", json={})
    assert r_reset.status_code == 201

    # 2. Replan
    r_replan = client.post(
        f"/api/v1/simulations/{DEMO_SCENARIO_ID}/replan", json={"actor": "operator"}
    )
    assert r_replan.status_code == 201

    # 3. Query comparison endpoint
    r_comp = client.get(f"/api/v1/simulations/{DEMO_SCENARIO_ID}/neural-comparison")
    assert r_comp.status_code == 200
    data = r_comp.json()

    assert data["available"] is True
    assert data["status"] == "available"
    assert data["context"] == "current"
    assert data["authoritative_engine"] == "ortools"
    assert data["authorizes_dispatch"] is False

    # Check OR-Tools summary
    ot = data["ortools_summary"]
    assert ot["engine"] == "ortools"
    assert ot["authoritative"] is True
    assert ot["assigned_count"] > 0
    assert len(ot["selected_pairs"]) > 0

    # Check MLP prediction
    mlp = data["mlp_prediction"]
    assert mlp["model_name"] == "PairwiseAssignmentMLP"
    assert mlp["available"] is True
    assert mlp["status"] == "success"
    assert mlp["checkpoint_digest"].startswith("3df9bd6a5f9e")
    assert mlp["hard_constraint_validation"]["valid"] is True
    assert mlp["hard_constraint_validation"]["violation_count"] == 0
    assert mlp["inference_duration_ms"] > 0.0

    # Check GNN prediction
    gnn = data["gnn_prediction"]
    assert gnn["model_name"] == "BipartiteAssignmentGNN"
    assert gnn["available"] is True
    assert gnn["status"] == "success"
    assert gnn["checkpoint_digest"].startswith("fb6ba734ded8")
    assert gnn["hard_constraint_validation"]["valid"] is True
    assert gnn["hard_constraint_validation"]["violation_count"] == 0
    assert gnn["inference_duration_ms"] > 0.0

    # Check Disagreement analysis & Voice summary
    assert "disagreement_analysis" in data
    assert data["voice_summary"] is not None
    assert len(data["voice_summary"]) > 10
