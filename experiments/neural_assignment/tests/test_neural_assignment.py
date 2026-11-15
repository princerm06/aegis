"""Tests for the isolated AEGIS neural assignment experiment.

Covers:
1. Scenario generator determinism and reproducibility
2. OR-Tools teacher label validity, proven optimality, and tie-equivalence detection
3. Split and geometry-family leakage prevention
4. Pre-assignment feature extraction shapes and post-assignment non-leakage
5. Independent hard-constraint validator coverage of all safety gates
6. Constrained greedy and bipartite matching decoders under adversarial logits
7. Supervised MLP and Bipartite GNN training, checkpoint hashing, and inference
8. Committed evaluation report schema and zero-violation regression checks
"""

from __future__ import annotations

from datetime import timedelta
import json
from pathlib import Path
import numpy as np
import pytest
torch = pytest.importorskip("torch", reason="Optional PyTorch dependency required for neural assignment experiments")

from neural_assignment import FEATURE_SCHEMA_VERSION, GENERATOR_VERSION
from neural_assignment._rootpath import ensure_repo_root_on_path

ensure_repo_root_on_path()

from backend.app.models import (
    Casualty,
    CVObservation,
    CVTrack,
    MedicalAssessment,
    Obstacle,
    OccupancyGrid,
    Position,
    Responder,
    Scenario,
    SceneMap,
)
from neural_assignment.decoder import (
    count_unfiltered_invalid_proposals,
    decode_and_validate,
    decode_constrained_greedy,
    decode_constrained_matching,
)
from neural_assignment.features import (
    CASUALTY_NODE_FEATURE_DIM,
    PAIR_FEATURE_DIM,
    PAIR_FEATURE_NAMES,
    RESPONDER_NODE_FEATURE_DIM,
    extract_features_from_bundle,
    extract_features_from_request,
)
from neural_assignment.generator import (
    REFERENCE_NOW,
    GeneratorConfig,
    _find_all_optimal_edges,
    generate_scenario_bundle,
    generate_split_bundles,
    verify_no_split_leakage,
)
from neural_assignment.models import BipartiteAssignmentGNN, PairwiseAssignmentMLP
from neural_assignment.train import (
    build_padded_dataset,
    load_trained_model,
    train_model,
)
from neural_assignment.validator import validate_assignments
from optimization.models import OptimizationRequest
from optimization.routing import _GridRouter


def test_generator_reproducibility_and_determinism() -> None:
    b1 = generate_scenario_bundle(10042, split="train")
    b2 = generate_scenario_bundle(10042, split="train")

    assert b1["generator_version"] == GENERATOR_VERSION
    assert b1["scenario"].model_dump() == b2["scenario"].model_dump()
    assert b1["required_capabilities"] == b2["required_capabilities"]
    assert b1["teacher_pairs"] == b2["teacher_pairs"]
    assert b1["any_optimal_pairs"] == b2["any_optimal_pairs"]
    assert b1["tie_alternative_pairs"] == b2["tie_alternative_pairs"]
    assert b1["feasible_pairs"] == b2["feasible_pairs"]
    assert b1["objective_values"] == b2["objective_values"]


def test_teacher_label_validity_and_tie_equivalence() -> None:
    cfg = GeneratorConfig(train_count=12, val_count=6, test_id_count=6, test_ood_count=6)
    bundles, stats = generate_split_bundles("train", count=12, config=cfg)
    assert len(bundles) == 12
    assert stats["excluded_non_optimal"] == 0

    for b in bundles:
        assert b["proven_optimal"] is True
        assert b["teacher_engine"] == "ortools"
        feas_set = set(map(tuple, b["feasible_pairs"]))
        teacher_set = set(map(tuple, b["teacher_pairs"]))
        any_opt_set = set(map(tuple, b["any_optimal_pairs"]))
        tie_alt_set = set(map(tuple, b["tie_alternative_pairs"]))

        assert teacher_set.issubset(feas_set)
        assert teacher_set.issubset(any_opt_set)
        assert any_opt_set.issubset(feas_set)
        assert tie_alt_set == (any_opt_set - teacher_set)

        check = validate_assignments(b["request"], b["teacher_pairs"])
        assert check["violation_count"] == 0

    # Construct a symmetric scenario with two equidistant responders to verify tie detection
    scene_map = SceneMap(
        frame_id="map",
        revision="rev-sym",
        obstacles=[],
        occupancy_grid=OccupancyGrid(
            origin=Position(frame_id="map", x_m=0.0, y_m=0.0),
            resolution_m=1.0,
            width=6,
            height=6,
            cells=[0] * 36,
        ),
    )
    router = _GridRouter(scene_map)
    c0 = Position(frame_id="map", x_m=2.5, y_m=2.5)
    r0 = Position(frame_id="map", x_m=1.5, y_m=2.5)
    r1 = Position(frame_id="map", x_m=3.5, y_m=2.5)
    routes = {
        ("c0", "r0"): router.plan(r0, c0),
        ("c0", "r1"): router.plan(r1, c0),
    }
    opt_edges, match_count = _find_all_optimal_edges(
        routes, {"c0": 2}, target_count=1, target_points=2, target_travel_mm=1000
    )
    assert match_count == 2
    assert opt_edges == {("c0", "r0"), ("c0", "r1")}


def test_split_and_geometry_family_leakage_prevention() -> None:
    cfg = GeneratorConfig(train_count=10, val_count=5, test_id_count=5, test_ood_count=5)
    train_b, _ = generate_split_bundles("train", count=10, config=cfg)
    val_b, _ = generate_split_bundles("val", count=5, config=cfg)
    test_id_b, _ = generate_split_bundles("test_id", count=5, config=cfg)
    test_ood_b, _ = generate_split_bundles("test_ood", count=5, config=cfg)

    res = verify_no_split_leakage(
        {"train": train_b, "val": val_b, "test_id": test_id_b, "test_ood": test_ood_b}
    )
    assert res["leakage_free"] is True
    assert res["total_unique_seeds"] == 25

    # Inject duplicate seed across splits -> must raise AssertionError
    with pytest.raises(AssertionError, match="Seed leakage"):
        verify_no_split_leakage({"train": train_b, "val": [train_b[0]]})

    # Disallow OOD geometry family inside train split
    with pytest.raises(ValueError, match="not permitted in split"):
        generate_scenario_bundle(10001, split="train", family="u_shaped_courtyard")


def test_feature_engineering_shapes_and_no_post_assignment_leakage() -> None:
    bundle = generate_scenario_bundle(10007, split="train")
    sf_from_bundle = extract_features_from_bundle(bundle)
    sf_from_raw_req = extract_features_from_request(bundle["request"])

    n_c = len(bundle["scenario"].casualties)
    n_r = len(bundle["scenario"].responders)

    assert sf_from_bundle.schema_version == FEATURE_SCHEMA_VERSION
    assert sf_from_bundle.pair_features.shape == (n_c, n_r, PAIR_FEATURE_DIM)
    assert sf_from_bundle.casualty_node_features.shape == (n_c, CASUALTY_NODE_FEATURE_DIM)
    assert sf_from_bundle.responder_node_features.shape == (n_r, RESPONDER_NODE_FEATURE_DIM)
    assert np.all(np.isfinite(sf_from_bundle.pair_features))
    assert np.all(np.isfinite(sf_from_bundle.casualty_node_features))
    assert np.all(np.isfinite(sf_from_bundle.responder_node_features))

    # Prove post-assignment teacher labels have zero influence on extracted features
    corrupted_bundle = dict(bundle)
    corrupted_bundle["teacher_pairs"] = [("c0", "r0"), ("c1", "r1")]
    corrupted_bundle["teacher_result"] = None
    sf_corrupted = extract_features_from_bundle(corrupted_bundle)

    assert np.array_equal(sf_from_bundle.pair_features, sf_from_raw_req.pair_features)
    assert np.array_equal(sf_from_bundle.pair_features, sf_corrupted.pair_features)

    feas_idx = PAIR_FEATURE_NAMES.index("pair_hard_feasible")
    assert np.array_equal(
        sf_from_bundle.pair_features[:, :, feas_idx] > 0.5,
        sf_from_bundle.feasible_mask,
    )


def test_independent_validator_catches_all_violation_categories() -> None:
    now = REFERENCE_NOW
    # Obstacle wall across x=3..4, y=0..6 blocking left side (x<3) from right side (x>4)
    wall = Obstacle(
        id="full-divider",
        vertices=[
            Position(frame_id="map", x_m=3.0, y_m=0.0),
            Position(frame_id="map", x_m=4.0, y_m=0.0),
            Position(frame_id="map", x_m=4.0, y_m=6.0),
            Position(frame_id="map", x_m=3.0, y_m=6.0),
        ],
    )
    scene_map = SceneMap(
        frame_id="map",
        revision="rev-v",
        obstacles=[wall],
        occupancy_grid=OccupancyGrid(
            origin=Position(frame_id="map", x_m=0.0, y_m=0.0),
            resolution_m=1.0,
            width=6,
            height=6,
            cells=[0] * 36,
        ),
    )
    casualties = [
        Casualty(
            id="c_ok",
            position=Position(frame_id="map", x_m=1.5, y_m=1.5),
            medical_assessment=MedicalAssessment(
                severity="immediate", source="human", assessed_by="op", assessed_at=now
            ),
        ),
        Casualty(
            id="c_stale",
            position=Position(frame_id="map", x_m=1.5, y_m=2.5),
            medical_assessment=None,
        ),
        Casualty(
            id="c_unreach",
            position=Position(frame_id="map", x_m=5.5, y_m=1.5),
            medical_assessment=None,
        ),
    ]
    responders = [
        Responder(
            id="r_ok",
            display_name="R0",
            position=Position(frame_id="map", x_m=1.5, y_m=4.5),
            status="available",
            capabilities=["bls"],
        ),
        Responder(
            id="r_busy",
            display_name="R1",
            position=Position(frame_id="map", x_m=1.5, y_m=3.5),
            status="busy",
            capabilities=["als", "bls"],
        ),
    ]
    observations = {
        "c_stale": CVObservation(
            id="obs-stale",
            source="cv",
            observed_at=now - timedelta(seconds=90),
            position=Position(frame_id="map", x_m=1.5, y_m=2.5),
            kind="person_detected",
            confidence=0.9,
            track=CVTrack(
                track_id="t-stale",
                state="visible",
                first_seen_at=now - timedelta(seconds=100),
                last_seen_at=now - timedelta(seconds=90),
            ),
        )
    }
    scenario = Scenario(
        schema_version="1.0",
        id="scen-val",
        name="Validator test",
        synthetic=True,
        frame_id="map",
        created_at=now,
        casualties=casualties,
        responders=responders,
        scene_map=scene_map,
    )
    req = OptimizationRequest(
        scenario=scenario,
        required_capabilities={"c_ok": ["als"]},
        observations=observations,
        now=now,
    )

    assert "required_capabilities_missing:c_ok/r_ok" in validate_assignments(
        req, [("c_ok", "r_ok")]
    )["violations"]
    assert "responder_unavailable:c_ok/r_busy" in validate_assignments(
        req, [("c_ok", "r_busy")]
    )["violations"]
    assert any(
        v.startswith("observation_blocked:c_stale/r_ok")
        for v in validate_assignments(req, [("c_stale", "r_ok")])["violations"]
    )

    req_no_cap = req.model_copy(deep=True)
    req_no_cap.required_capabilities = {}
    assert "unreachable:c_unreach/r_ok" in validate_assignments(
        req_no_cap, [("c_unreach", "r_ok")]
    )["violations"]
    assert "duplicate_responder:c_unreach/r_ok" in validate_assignments(
        req_no_cap, [("c_ok", "r_ok"), ("c_unreach", "r_ok")]
    )["violations"]


def test_constrained_decoders_filter_adversarial_logits_and_maximize_matching() -> None:
    bundle = generate_scenario_bundle(10015, split="train")
    req = bundle["request"]
    sf = extract_features_from_bundle(bundle)

    # Set +100 logit on every INFEASIBLE pair and -10 logit on every FEASIBLE pair
    adversarial_logits = np.where(sf.feasible_mask, -10.0, 100.0).astype(np.float32)

    raw_invalid = count_unfiltered_invalid_proposals(adversarial_logits, sf, threshold=0.0)
    if not np.all(sf.feasible_mask):
        assert raw_invalid > 0

    for dec_kind in ("greedy", "matching"):
        res = decode_and_validate(adversarial_logits, req, scene_features=sf, decoder=dec_kind)
        assert res.committed_constraint_violations == 0
        assert res.violation_details == []
        assert res.authorizes_dispatch is False
        for pair in res.selected_pairs:
            assert pair in set(map(tuple, bundle["feasible_pairs"]))

    # Verify matching beats greedy on a classic augmenting-path trap:
    # c0 can connect to r0 (logit 5.0) or r1 (logit 4.0); c1 can ONLY connect to r0 (logit 4.5)
    # Greedy picks (c0, r0) first, leaving c1 unassigned (cardinality 1).
    # Constrained matching picks (c0, r1) and (c1, r0) -> cardinality 2!
    fake_sf = extract_features_from_bundle(generate_scenario_bundle(10002, split="train"))
    if fake_sf.feasible_mask.shape[0] >= 2 and fake_sf.feasible_mask.shape[1] >= 2:
        trap_mask = np.zeros_like(fake_sf.feasible_mask)
        trap_mask[0, 0] = True
        trap_mask[0, 1] = True
        trap_mask[1, 0] = True
        fake_sf.feasible_mask = trap_mask
        trap_logits = np.full(trap_mask.shape, -5.0, dtype=np.float32)
        trap_logits[0, 0] = 5.0
        trap_logits[0, 1] = 4.0
        trap_logits[1, 0] = 4.5

        greedy_pairs = decode_constrained_greedy(trap_logits, fake_sf)
        matching_pairs = decode_constrained_matching(trap_logits, fake_sf)
        assert len(greedy_pairs) == 1
        assert len(matching_pairs) == 2


def test_mlp_and_gnn_training_and_inference_pipeline(tmp_path: Path) -> None:
    cfg = GeneratorConfig(train_count=24, val_count=8, test_id_count=6, test_ood_count=6)
    train_bundles, _ = generate_split_bundles("train", count=24, config=cfg)
    val_bundles, _ = generate_split_bundles("val", count=8, config=cfg)

    train_ds = build_padded_dataset(train_bundles)
    val_ds = build_padded_dataset(val_bundles)

    for kind in ("mlp", "gnn"):
        ckpt = tmp_path / f"{kind}_test.pt"
        model, meta = train_model(
            model_kind=kind,
            train_dataset=train_ds,
            val_dataset=val_ds,
            seed=42,
            epochs=6,
            batch_size=12,
            save_path=ckpt,
        )
        assert ckpt.exists()
        assert len(meta["weight_sha256"]) == 64
        assert meta["loss_curve_summary"]["final_train_loss"] <= meta["loss_curve_summary"]["epoch_1_train_loss"]

        loaded_model, loaded_ckpt = load_trained_model(ckpt)
        assert loaded_ckpt["model_kind"] == kind

        sample_bundle = val_bundles[0]
        sf = extract_features_from_bundle(sample_bundle)
        with torch.no_grad():
            logits1 = model(
                torch.from_numpy(sf.pair_features),
                torch.from_numpy(sf.casualty_node_features),
                torch.from_numpy(sf.responder_node_features),
                torch.from_numpy(sf.feasible_mask),
            ).numpy()
            logits2 = loaded_model(
                torch.from_numpy(sf.pair_features),
                torch.from_numpy(sf.casualty_node_features),
                torch.from_numpy(sf.responder_node_features),
                torch.from_numpy(sf.feasible_mask),
            ).numpy()
        assert np.allclose(logits1, logits2, atol=1e-6)
        dec = decode_and_validate(logits1, sample_bundle["request"], scene_features=sf, decoder="matching")
        assert dec.committed_constraint_violations == 0


def test_committed_reports_exist_and_have_zero_violations() -> None:
    reports_dir = Path(__file__).resolve().parents[1] / "reports"
    eval_path = reports_dir / "eval_results.json"
    panel_path = reports_dir / "benchmark_panel.json"
    train_path = reports_dir / "training_summary.json"

    if not eval_path.exists():
        pytest.skip("Evaluation reports not yet generated")

    report = json.loads(eval_path.read_text())
    panel = json.loads(panel_path.read_text())
    train_summary = json.loads(train_path.read_text())

    assert report["synthetic_only"] is True
    assert report["authorizes_dispatch"] is False
    assert report["dataset"]["leakage_verification"]["leakage_free"] is True
    assert report["dataset"]["splits"]["train"]["count"] == 5000
    assert panel["synthetic_only"] is True
    assert panel["authorizes_dispatch"] is False
    assert len(train_summary["runs"]) >= 2

    for split_name, suite in report["evaluation"].items():
        for contender, metrics in suite.items():
            assert metrics["safety"]["committed_constraint_violations_total"] == 0, (
                f"Non-zero violation in {split_name}/{contender}"
            )
        # OR-Tools must have 100% zero-regret rate against itself
        assert suite["ortools"]["zero_regret_optimal_rate"]["mean"] == 1.0
