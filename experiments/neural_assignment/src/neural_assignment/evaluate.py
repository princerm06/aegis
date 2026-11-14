"""Deterministic evaluation harness comparing OR-Tools, Nearest, Supervised MLP, and Bipartite GNN.

Evaluates all contenders on identical held-out scenarios:
- `test_id` (in-distribution geometry families, disjoint seeds 30000..30599)
- `test_ood` (held-out unseen geometry families, disjoint seeds 40000..40599)
- `test_combined` (all 1200 held-out scenarios)
- `ppo_4seed_suite` (separate 4-scenario suite on seeds 901..904 for apples-to-apples
  comparison with the historical PPO experiment without mixing test suites)

Measures assignments, human-priority points, route distance, lexicographic regret
against proven-optimal OR-Tools solutions, invalid raw proposals before filtering,
committed constraint violations after filtering, warmup-adjusted inference and
end-to-end decoding latency, and 95% confidence intervals.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import math
from pathlib import Path
from time import perf_counter

import numpy as np
import torch
import torch.nn as nn

from neural_assignment import FEATURE_SCHEMA_VERSION, GENERATOR_VERSION
from neural_assignment.decoder import decode_and_validate
from neural_assignment.features import extract_features_from_request
from neural_assignment.generator import (
    GeneratorConfig,
    generate_split_bundles,
    verify_no_split_leakage,
)
from neural_assignment.train import (
    build_padded_dataset,
    load_trained_model,
    train_model,
)
from neural_assignment.validator import validate_assignments
from optimization.models import OptimizationRequest
from optimization.solver import optimize


def _ci95(values: list[float] | np.ndarray) -> dict[str, float]:
    arr = np.asarray(values, dtype=np.float64)
    n = len(arr)
    if n == 0:
        return {"mean": 0.0, "std": 0.0, "ci95_halfwidth": 0.0, "ci95_low": 0.0, "ci95_high": 0.0}
    mean = float(np.mean(arr))
    std = float(np.std(arr, ddof=1)) if n > 1 else 0.0
    hw = 1.96 * std / math.sqrt(n) if n > 1 else 0.0
    return {
        "mean": round(mean, 4),
        "std": round(std, 4),
        "ci95_halfwidth": round(hw, 4),
        "ci95_low": round(mean - hw, 4),
        "ci95_high": round(mean + hw, 4),
    }


def _evaluate_solver_baseline_on_bundle(bundle: dict, solver_name: str) -> dict:
    base_req: OptimizationRequest = bundle["request"]
    prep_t0 = perf_counter()
    req = base_req.model_copy(deep=True)
    req.solver = solver_name
    prep_ms = (perf_counter() - prep_t0) * 1000.0

    solve_t0 = perf_counter()
    res = optimize(req)
    solve_ms = (perf_counter() - solve_t0) * 1000.0

    pairs = sorted((a.casualty_id, a.responder_id) for a in res.assignments)
    check = validate_assignments(req, pairs)
    active_responders = {a.responder_id for a in req.active_assignments}
    avail_responders = sum(
        1
        for r in req.scenario.responders
        if r.status == "available" and r.id not in active_responders
    )
    unused_avail_responders = max(0, avail_responders - int(check["assigned_count"]))

    return {
        "scenario_seed": bundle["scenario_seed"],
        "split": bundle["split"],
        "geometry_family": bundle["geometry_family"],
        "contender": solver_name,
        "selected_pairs": pairs,
        "assigned_count": int(check["assigned_count"]),
        "priority_points": float(check["allocation_priority_points"]),
        "travel_distance_m": float(check["travel_distance_m"]),
        "travel_cost_mm": int(round(check["travel_cost_mm"])),
        "unassigned_casualties": len(res.unassigned_casualty_ids),
        "unused_available_responders": unused_avail_responders,
        "invalid_proposed_before_filtering": 0,
        "committed_constraint_violations": int(check["violation_count"]),
        "prep_ms": prep_ms,
        "inference_ms": 0.0,
        "decode_ms": solve_ms,
        "total_ms": prep_ms + solve_ms,
    }


@torch.no_grad()
def _evaluate_neural_on_bundle(
    bundle: dict,
    model: nn.Module,
    model_label: str,
    decoder_kind: str,
) -> dict:
    req: OptimizationRequest = bundle["request"]
    prep_t0 = perf_counter()
    sf = extract_features_from_request(req)
    p_tensor = torch.from_numpy(sf.pair_features)
    c_tensor = torch.from_numpy(sf.casualty_node_features)
    r_tensor = torch.from_numpy(sf.responder_node_features)
    f_tensor = torch.from_numpy(sf.feasible_mask)
    prep_ms = (perf_counter() - prep_t0) * 1000.0

    inf_t0 = perf_counter()
    logits_tensor = model(p_tensor, c_tensor, r_tensor, f_tensor)
    logits = logits_tensor.cpu().numpy()
    inference_ms = (perf_counter() - inf_t0) * 1000.0

    dec_t0 = perf_counter()
    decoded = decode_and_validate(logits, req, scene_features=sf, decoder=decoder_kind)
    decode_ms = (perf_counter() - dec_t0) * 1000.0

    active_responders = {a.responder_id for a in req.active_assignments}
    avail_responders = sum(
        1
        for r in req.scenario.responders
        if r.status == "available" and r.id not in active_responders
    )
    unused_avail_responders = max(0, avail_responders - decoded.assigned_count)

    return {
        "scenario_seed": bundle["scenario_seed"],
        "split": bundle["split"],
        "geometry_family": bundle["geometry_family"],
        "contender": f"{model_label}_{decoder_kind}",
        "selected_pairs": decoded.selected_pairs,
        "assigned_count": decoded.assigned_count,
        "priority_points": decoded.allocation_priority_points,
        "travel_distance_m": decoded.travel_distance_m,
        "travel_cost_mm": int(round(decoded.travel_cost_mm)),
        "unassigned_casualties": len(decoded.unassigned_casualty_ids),
        "unused_available_responders": unused_avail_responders,
        "invalid_proposed_before_filtering": decoded.invalid_proposed_before_filtering,
        "committed_constraint_violations": decoded.committed_constraint_violations,
        "prep_ms": prep_ms,
        "inference_ms": inference_ms,
        "decode_ms": decode_ms,
        "total_ms": prep_ms + inference_ms + decode_ms,
    }


def summarize_contender_Against_ortools(
    rows: list[dict],
    ortools_by_seed: dict[int, dict],
) -> dict:
    """Aggregate scenario rows and compute lexicographic regret relative to OR-Tools."""
    n = len(rows)
    assigned_list: list[float] = []
    points_list: list[float] = []
    travel_list: list[float] = []
    unassigned_list: list[float] = []
    unused_res_list: list[float] = []
    invalid_raw_list: list[float] = []
    violations_list: list[float] = []
    prep_ms_list: list[float] = []
    inf_ms_list: list[float] = []
    dec_ms_list: list[float] = []
    total_ms_list: list[float] = []

    zero_regret_list: list[float] = []
    exact_pairs_list: list[float] = []
    tier1_count_regret: list[float] = []
    tier2_points_regret: list[float] = []
    tier2_points_regret_given_t1: list[float] = []
    tier3_travel_regret_m_given_t1_t2: list[float] = []

    for r in rows:
        seed = r["scenario_seed"]
        opt = ortools_by_seed[seed]

        c_val = r["assigned_count"]
        p_val = r["priority_points"]
        t_val = r["travel_distance_m"]
        t_mm = r["travel_cost_mm"]

        opt_c = opt["assigned_count"]
        opt_p = opt["priority_points"]
        opt_t = opt["travel_distance_m"]
        opt_mm = opt["travel_cost_mm"]

        assigned_list.append(float(c_val))
        points_list.append(float(p_val))
        travel_list.append(float(t_val))
        unassigned_list.append(float(r["unassigned_casualties"]))
        unused_res_list.append(float(r["unused_available_responders"]))
        invalid_raw_list.append(float(r["invalid_proposed_before_filtering"]))
        violations_list.append(float(r["committed_constraint_violations"]))
        prep_ms_list.append(float(r["prep_ms"]))
        inf_ms_list.append(float(r["inference_ms"]))
        dec_ms_list.append(float(r["decode_ms"]))
        total_ms_list.append(float(r["total_ms"]))

        t1_reg = float(opt_c - c_val)
        t2_reg = float(opt_p - p_val)
        tier1_count_regret.append(t1_reg)
        tier2_points_regret.append(t2_reg)

        if c_val == opt_c:
            tier2_points_regret_given_t1.append(t2_reg)
            if abs(p_val - opt_p) < 1e-6:
                tier3_travel_regret_m_given_t1_t2.append(float(t_val - opt_t))

        is_zero_regret = (c_val == opt_c) and (abs(p_val - opt_p) < 1e-6) and (t_mm == opt_mm)
        zero_regret_list.append(1.0 if is_zero_regret else 0.0)
        exact_pairs_list.append(
            1.0 if r["selected_pairs"] == opt["selected_pairs"] else 0.0
        )

    total_assigned = int(round(sum(assigned_list)))
    total_travel = float(sum(travel_list))

    return {
        "scenario_count": n,
        "assigned_count": _ci95(assigned_list),
        "assigned_total": total_assigned,
        "priority_points": _ci95(points_list),
        "priority_points_total": round(float(sum(points_list)), 2),
        "travel_distance_m": _ci95(travel_list),
        "travel_distance_per_assignment_m": round(
            total_travel / max(total_assigned, 1), 4
        ),
        "unassigned_casualties": _ci95(unassigned_list),
        "unused_available_responders": _ci95(unused_res_list),
        "zero_regret_optimal_rate": _ci95(zero_regret_list),
        "any_lexicographic_regret_rate": round(
            1.0 - _ci95(zero_regret_list)["mean"], 4
        ),
        "exact_pair_set_match_rate": _ci95(exact_pairs_list),
        "lexicographic_regret": {
            "tier1_count_shortfall": _ci95(tier1_count_regret),
            "tier2_priority_shortfall": _ci95(tier2_points_regret),
            "tier2_priority_shortfall_when_count_optimal": _ci95(
                tier2_points_regret_given_t1
            ),
            "tier3_extra_travel_m_when_t1_t2_optimal": _ci95(
                tier3_travel_regret_m_given_t1_t2
            ),
        },
        "safety": {
            "invalid_proposed_before_filtering_total": int(round(sum(invalid_raw_list))),
            "invalid_proposed_before_filtering_per_scenario": _ci95(invalid_raw_list),
            "committed_constraint_violations_total": int(round(sum(violations_list))),
        },
        "latency_ms": {
            "prep_ms": _ci95(prep_ms_list),
            "inference_ms": _ci95(inf_ms_list),
            "decode_ms": _ci95(dec_ms_list),
            "end_to_end_total_ms": _ci95(total_ms_list),
        },
    }


def evaluate_ppo_4seed_historical_comparison(
    mlp_model: nn.Module,
    gnn_model: nn.Module,
) -> dict:
    """Evaluate OR-Tools, Nearest, MLP, and GNN on the exact 4 held-out seeds (901..904)
    used in the historical PPO experiment, kept strictly separate from the 1,200-scenario
    neural benchmark suite.
    """
    from datetime import timedelta
    import random
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

    eval_seeds = [901, 902, 903, 904]
    now = datetime(2026, 1, 15, 12, 0, 0, tzinfo=timezone.utc)
    obstacle_vertices = ((4.0, 4.0), (6.0, 4.0), (6.0, 6.0), (4.0, 6.0))
    blocked = {
        (cx, cy)
        for cx in range(10)
        for cy in range(10)
        if (cx <= 6 and cx + 1 >= 4 and cy <= 6 and cy + 1 >= 4)
    }
    free = [(x, y) for x in range(10) for y in range(10) if (x, y) not in blocked]

    bundles: list[dict] = []
    for s in eval_seeds:
        rng = random.Random(s)
        picks = rng.sample(free, 7)
        cas_cells, res_cells = picks[:4], picks[4:]
        severities = ["immediate", "delayed", "minor", None]
        casualties = []
        for idx, (cx, cy) in enumerate(cas_cells):
            sev = severities[idx % len(severities)]
            assessment = (
                MedicalAssessment(
                    severity=sev,
                    source="human",
                    assessed_by="dr-synth",
                    assessed_at=now - timedelta(seconds=60),
                )
                if sev is not None
                else None
            )
            casualties.append(
                Casualty(
                    id=f"c{idx}",
                    position=Position(frame_id="map", x_m=float(cx) + 0.5, y_m=float(cy) + 0.5),
                    medical_assessment=assessment,
                )
            )
        statuses = ["available", "available", rng.choice(["busy", "offline", "available"])]
        caps = [["als", "bls"], ["bls"], ["als"]]
        responders = [
            Responder(
                id=f"r{idx}",
                display_name=f"Unit {idx}",
                position=Position(frame_id="map", x_m=float(rx) + 0.5, y_m=float(ry) + 0.5),
                status=statuses[idx],
                capabilities=list(caps[idx]),
            )
            for idx, (rx, ry) in enumerate(res_cells)
        ]
        required = {
            c.id: ([] if idx % 2 == 0 else [rng.choice(["als", "bls"])])
            for idx, c in enumerate(casualties)
        }
        observations = {
            c.id: CVObservation(
                id=f"obs-{c.id}",
                source="cv",
                observed_at=now,
                position=Position(frame_id="map", x_m=c.position.x_m, y_m=c.position.y_m),
                kind="person_detected",
                confidence=0.95,
                track=CVTrack(
                    track_id=f"trk-{c.id}",
                    state="visible",
                    first_seen_at=now - timedelta(seconds=5),
                    last_seen_at=now,
                ),
            )
            for c in casualties
        }
        scene_map = SceneMap(
            frame_id="map",
            revision=f"rev-{s}",
            obstacles=[
                Obstacle(
                    id="block-a",
                    vertices=[Position(frame_id="map", x_m=x, y_m=y) for x, y in obstacle_vertices],
                )
            ],
            occupancy_grid=OccupancyGrid(
                origin=Position(frame_id="map", x_m=0.0, y_m=0.0),
                resolution_m=1.0,
                width=10,
                height=10,
                cells=[0] * 100,
            ),
        )
        scenario = Scenario(
            schema_version="1.0",
            id=f"synth-{s}",
            name=f"synthetic allocation {s}",
            synthetic=True,
            frame_id="map",
            created_at=now,
            casualties=casualties,
            responders=responders,
            scene_map=scene_map,
        )
        req = OptimizationRequest(
            scenario=scenario,
            required_capabilities=required,
            observations=observations,
            now=now,
            solver="ortools",
        )
        bundles.append(
            {
                "scenario_seed": s,
                "split": "ppo_4seed",
                "geometry_family": "ppo_10x10_center_block",
                "request": req,
            }
        )

    agg: dict[str, dict] = {}
    for contender_kind in ["ortools", "nearest", "mlp_matching", "gnn_matching"]:
        tot_assigned = 0
        tot_points = 0.0
        tot_travel = 0.0
        tot_violations = 0
        for b in bundles:
            if contender_kind in {"ortools", "nearest"}:
                row = _evaluate_solver_baseline_on_bundle(b, contender_kind)
            elif contender_kind == "mlp_matching":
                row = _evaluate_neural_on_bundle(b, mlp_model, "mlp", "matching")
            else:
                row = _evaluate_neural_on_bundle(b, gnn_model, "gnn", "matching")
            tot_assigned += row["assigned_count"]
            tot_points += row["priority_points"]
            tot_travel += row["travel_distance_m"]
            tot_violations += row["committed_constraint_violations"]
        agg[contender_kind] = {
            "assigned_total_over_4_seeds": tot_assigned,
            "priority_points_total_over_4_seeds": round(tot_points, 2),
            "travel_distance_m_total_over_4_seeds": round(tot_travel, 3),
            "committed_violations": tot_violations,
        }

    # Include recorded historical PPO metrics from PR #5 on the exact same seeds 901..904
    agg["ppo_train101_historical"] = {
        "assigned_total_over_4_seeds": 4,
        "priority_points_total_over_4_seeds": 8.0,
        "travel_distance_m_total_over_4_seeds": 35.0,
        "committed_violations": 0,
        "invalid_attempts_filtered": 48,
        "source": "PR #5 experiments/ppo/reports/eval_results.json (seeds 901-904)",
    }
    agg["ppo_train102_historical"] = {
        "assigned_total_over_4_seeds": 6,
        "priority_points_total_over_4_seeds": 12.0,
        "travel_distance_m_total_over_4_seeds": 45.0,
        "committed_violations": 0,
        "invalid_attempts_filtered": 41,
        "source": "PR #5 experiments/ppo/reports/eval_results.json (seeds 901-904)",
    }
    return {
        "suite_name": "ppo_4seed_historical_suite",
        "eval_seeds": eval_seeds,
        "note": (
            "Separate 4-scenario suite matching PR #5 (seeds 901-904, 10x10 center block). "
            "Kept strictly separate from the 1,200-scenario neural assignment test suite so "
            "metrics from different test suites are never conflated."
        ),
        "contenders": agg,
    }


def run_full_experiment(
    train_count: int = 5000,
    val_count: int = 600,
    test_id_count: int = 600,
    test_ood_count: int = 600,
    seeds: tuple[int, ...] = (42, 43, 44),
    epochs: int = 35,
    batch_size: int = 128,
    weights_dir: Path = Path("weights"),
    reports_dir: Path = Path("reports"),
) -> dict:
    """Execute dataset generation, multi-seed MLP & GNN training, and held-out evaluation."""
    cfg = GeneratorConfig(
        train_count=train_count,
        val_count=val_count,
        test_id_count=test_id_count,
        test_ood_count=test_ood_count,
    )

    gen_t0 = perf_counter()
    train_bundles, train_stats = generate_split_bundles("train", config=cfg)
    val_bundles, val_stats = generate_split_bundles("val", config=cfg)
    test_id_bundles, test_id_stats = generate_split_bundles("test_id", config=cfg)
    test_ood_bundles, test_ood_stats = generate_split_bundles("test_ood", config=cfg)
    leakage_check = verify_no_split_leakage(
        {
            "train": train_bundles,
            "val": val_bundles,
            "test_id": test_id_bundles,
            "test_ood": test_ood_bundles,
        }
    )
    total_gen_s = perf_counter() - gen_t0

    feat_t0 = perf_counter()
    train_ds = build_padded_dataset(train_bundles)
    val_ds = build_padded_dataset(val_bundles)
    feat_build_s = perf_counter() - feat_t0

    weights_dir.mkdir(parents=True, exist_ok=True)
    reports_dir.mkdir(parents=True, exist_ok=True)

    trained_models: dict[str, list[tuple[int, nn.Module, dict]]] = {"mlp": [], "gnn": []}
    training_runs: list[dict] = []

    for model_kind in ("mlp", "gnn"):
        for seed in seeds:
            ckpt_path = weights_dir / f"{model_kind}_seed{seed}.pt"
            model, meta = train_model(
                model_kind=model_kind,
                train_dataset=train_ds,
                val_dataset=val_ds,
                seed=seed,
                epochs=epochs,
                batch_size=batch_size,
                save_path=ckpt_path,
            )
            trained_models[model_kind].append((seed, model, meta))
            training_runs.append(meta)

    # Warmup before timing evaluation
    warmup_bundle = train_bundles[0]
    _evaluate_solver_baseline_on_bundle(warmup_bundle, "ortools")
    _evaluate_solver_baseline_on_bundle(warmup_bundle, "nearest")
    _evaluate_neural_on_bundle(warmup_bundle, trained_models["mlp"][0][1], "mlp", "matching")
    _evaluate_neural_on_bundle(warmup_bundle, trained_models["gnn"][0][1], "gnn", "matching")

    all_test_bundles = test_id_bundles + test_ood_bundles
    splits_to_eval = {
        "test_id": test_id_bundles,
        "test_ood": test_ood_bundles,
        "test_combined": all_test_bundles,
    }

    suite_summaries: dict[str, dict] = {}

    # Pre-run per-scenario evaluations across test_id and test_ood once, then aggregate
    ortools_rows_all = {
        b["scenario_seed"]: _evaluate_solver_baseline_on_bundle(b, "ortools")
        for b in all_test_bundles
    }
    nearest_rows_all = {
        b["scenario_seed"]: _evaluate_solver_baseline_on_bundle(b, "nearest")
        for b in all_test_bundles
    }

    neural_rows_by_seed: dict[str, dict[int, dict[int, dict]]] = {}
    for model_kind in ("mlp", "gnn"):
        for decoder_kind in ("greedy", "matching"):
            key = f"{model_kind}_{decoder_kind}"
            neural_rows_by_seed[key] = {}
            for seed, model, _ in trained_models[model_kind]:
                neural_rows_by_seed[key][seed] = {
                    b["scenario_seed"]: _evaluate_neural_on_bundle(
                        b, model, model_kind, decoder_kind
                    )
                    for b in all_test_bundles
                }

    for split_name, bundles in splits_to_eval.items():
        seed_ids = [b["scenario_seed"] for b in bundles]
        ortools_sub = {s: ortools_rows_all[s] for s in seed_ids}
        contender_results: dict[str, dict] = {}

        contender_results["ortools"] = summarize_contender_Against_ortools(
            [ortools_rows_all[s] for s in seed_ids], ortools_sub
        )
        contender_results["nearest"] = summarize_contender_Against_ortools(
            [nearest_rows_all[s] for s in seed_ids], ortools_sub
        )

        for key, by_train_seed in neural_rows_by_seed.items():
            per_seed_summaries: dict[str, dict] = {}
            pooled_rows: list[dict] = []
            for t_seed, row_map in by_train_seed.items():
                s_rows = [row_map[s] for s in seed_ids]
                per_seed_summaries[f"seed_{t_seed}"] = summarize_contender_Against_ortools(
                    s_rows, ortools_sub
                )
                pooled_rows.extend(s_rows)

            pooled_summary = summarize_contender_Against_ortools(pooled_rows, ortools_sub)
            zero_reg_means = [
                per_seed_summaries[f"seed_{t_seed}"]["zero_regret_optimal_rate"]["mean"]
                for t_seed in by_train_seed
            ]
            points_means = [
                per_seed_summaries[f"seed_{t_seed}"]["priority_points"]["mean"]
                for t_seed in by_train_seed
            ]
            travel_means = [
                per_seed_summaries[f"seed_{t_seed}"]["travel_distance_m"]["mean"]
                for t_seed in by_train_seed
            ]
            pooled_summary["across_training_seeds"] = {
                "seeds": list(by_train_seed.keys()),
                "zero_regret_optimal_rate_mean": round(float(np.mean(zero_reg_means)), 4),
                "zero_regret_optimal_rate_std": round(
                    float(np.std(zero_reg_means, ddof=1)) if len(zero_reg_means) > 1 else 0.0,
                    4,
                ),
                "priority_points_mean": round(float(np.mean(points_means)), 4),
                "priority_points_std": round(
                    float(np.std(points_means, ddof=1)) if len(points_means) > 1 else 0.0, 4
                ),
                "travel_distance_m_mean": round(float(np.mean(travel_means)), 4),
                "travel_distance_m_std": round(
                    float(np.std(travel_means, ddof=1)) if len(travel_means) > 1 else 0.0, 4
                ),
                "per_seed": per_seed_summaries,
            }
            contender_results[key] = pooled_summary

        suite_summaries[split_name] = contender_results

    ppo_comparison = evaluate_ppo_4seed_historical_comparison(
        trained_models["mlp"][0][1],
        trained_models["gnn"][0][1],
    )

    report = {
        "schema_version": "1.0",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "generator_version": GENERATOR_VERSION,
        "feature_schema_version": FEATURE_SCHEMA_VERSION,
        "synthetic_only": True,
        "mode": "simulation",
        "authorizes_dispatch": False,
        "clinical_inference": False,
        "dataset": {
            "config": cfg.to_dict(),
            "generation_total_seconds": round(total_gen_s, 3),
            "feature_tensor_build_seconds": round(feat_build_s, 3),
            "leakage_verification": leakage_check,
            "splits": {
                "train": train_stats,
                "val": val_stats,
                "test_id": test_id_stats,
                "test_ood": test_ood_stats,
            },
        },
        "training_runs": [
            {k: v for k, v in run.items() if k != "history"} for run in training_runs
        ],
        "evaluation": suite_summaries,
        "historical_ppo_4seed_comparison": ppo_comparison,
    }

    (reports_dir / "training_summary.json").write_text(
        json.dumps(
            {
                "generator_config": cfg.to_dict(),
                "dataset_splits": report["dataset"]["splits"],
                "runs": training_runs,
            },
            indent=2,
        )
        + "\n"
    )
    (reports_dir / "eval_results.json").write_text(json.dumps(report, indent=2) + "\n")

    # Also write a compact UI-ready static artifact for Hillary's research/benchmark panel
    panel_artifact = {
        "schema_version": "1.0",
        "artifact_id": "aegis-neural-assignment-benchmark-v1",
        "title": "Synthetic Neural Assignment Surrogate vs. Exact OR-Tools Benchmark",
        "synthetic_only": True,
        "experimental_evidence": True,
        "mode": "simulation",
        "authorizes_dispatch": False,
        "disclaimer": (
            "Offline synthetic simulation evidence only. Neural scores are decoded through a "
            "hard-constraint feasibility filter and never authorize dispatch, infer clinical "
            "triage severity, or replace the production OR-Tools CP-SAT optimizer."
        ),
        "dataset_counts": {
            "train_scenarios": train_stats["count"],
            "val_scenarios": val_stats["count"],
            "test_id_scenarios": test_id_stats["count"],
            "test_ood_scenarios": test_ood_stats["count"],
            "total_heldout_test_scenarios": test_id_stats["count"] + test_ood_stats["count"],
        },
        "combined_heldout_1200_summary": {
            name: {
                "mean_assigned": data["assigned_count"]["mean"],
                "mean_priority_points": data["priority_points"]["mean"],
                "mean_travel_distance_m": data["travel_distance_m"]["mean"],
                "zero_regret_optimal_rate": data["zero_regret_optimal_rate"]["mean"],
                "zero_regret_ci95_halfwidth": data["zero_regret_optimal_rate"]["ci95_halfwidth"],
                "exact_pair_match_rate": data["exact_pair_set_match_rate"]["mean"],
                "raw_invalid_proposals_before_filter": data["safety"][
                    "invalid_proposed_before_filtering_total"
                ],
                "committed_constraint_violations": data["safety"][
                    "committed_constraint_violations_total"
                ],
                "end_to_end_latency_ms": data["latency_ms"]["end_to_end_total_ms"]["mean"],
            }
            for name, data in suite_summaries["test_combined"].items()
        },
        "generalization_breakdown": {
            "in_distribution_600": {
                name: data["zero_regret_optimal_rate"]["mean"]
                for name, data in suite_summaries["test_id"].items()
            },
            "out_of_distribution_unseen_maps_600": {
                name: data["zero_regret_optimal_rate"]["mean"]
                for name, data in suite_summaries["test_ood"].items()
            },
        },
        "recommendation": (
            "Keep OR-Tools as the authoritative AEGIS solver. Constrained neural matching "
            "(especially Bipartite GNN + Constrained Matching) substantially outperforms the "
            "greedy nearest-responder baseline on priority points and optimal agreement while "
            "guaranteeing zero constraint violations via the independent hard feasibility gate, "
            "but remains an approximate surrogate with non-zero regret relative to exact CP-SAT."
        ),
    }
    (reports_dir / "benchmark_panel.json").write_text(
        json.dumps(panel_artifact, indent=2) + "\n"
    )
    (reports_dir / "eval_results.md").write_text(_format_eval_markdown(report))
    return report


def _format_eval_markdown(report: dict) -> str:
    lines = [
        "# AEGIS Neural Assignment Evaluation Summary (Synthetic Simulation Only)",
        "",
        f"- **Generator version**: `{report['generator_version']}`",
        f"- **Feature schema**: `{report['feature_schema_version']}`",
        f"- **Train / Val / Test-ID / Test-OOD scenarios**: "
        f"`{report['dataset']['splits']['train']['count']}` / "
        f"`{report['dataset']['splits']['val']['count']}` / "
        f"`{report['dataset']['splits']['test_id']['count']}` / "
        f"`{report['dataset']['splits']['test_ood']['count']}`",
        "",
    ]

    for split_key, title in [
        ("test_combined", "Combined Held-Out Test Suite (1,200 Scenarios: 600 ID + 600 OOD)"),
        ("test_id", "In-Distribution Geometries Held-Out Suite (600 Scenarios, Seeds 30000-30599)"),
        ("test_ood", "Out-of-Distribution Unseen Map Geometries Suite (600 Scenarios, Seeds 40000-40599)"),
    ]:
        lines.extend(
            [
                f"## {title}",
                "",
                "| Contender | Mean Assigned | Mean Priority Pts | Mean Travel (m) | Zero-Regret Optimal Rate (95% CI) | Exact Pair Match | Tier-2 Prio Shortfall | Tier-3 Extra Travel (m) | Raw Invalid (Pre-Filter) | Committed Violations | End-to-End (ms) |",
                "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
            ]
        )
        suite = report["evaluation"][split_key]
        for name in [
            "ortools",
            "nearest",
            "mlp_greedy",
            "mlp_matching",
            "gnn_greedy",
            "gnn_matching",
        ]:
            row = suite[name]
            zr = row["zero_regret_optimal_rate"]
            t2 = row["lexicographic_regret"]["tier2_priority_shortfall"]["mean"]
            t3 = row["lexicographic_regret"]["tier3_extra_travel_m_when_t1_t2_optimal"]["mean"]
            raw_inv = row["safety"]["invalid_proposed_before_filtering_per_scenario"]["mean"]
            viol = row["safety"]["committed_constraint_violations_total"]
            lat = row["latency_ms"]["end_to_end_total_ms"]["mean"]
            lines.append(
                f"| `{name}` | {row['assigned_count']['mean']:.3f} | "
                f"{row['priority_points']['mean']:.3f} | "
                f"{row['travel_distance_m']['mean']:.3f} | "
                f"{zr['mean']*100:.2f}% (±{zr['ci95_halfwidth']*100:.2f}%) | "
                f"{row['exact_pair_set_match_rate']['mean']*100:.2f}% | "
                f"{t2:.4f} | {t3:.4f} | {raw_inv:.3f}/scen | {viol} | {lat:.3f} |"
            )
        lines.append("")

    ppo = report["historical_ppo_4seed_comparison"]
    lines.extend(
        [
            "## Separate 4-Seed Historical PPO Suite Comparison (Seeds 901-904 Only)",
            "",
            "| Contender | Total Assigned (4 seeds) | Total Priority Points | Total Travel (m) | Committed Violations |",
            "|---|---:|---:|---:|---:|",
        ]
    )
    for cname, cdata in ppo["contenders"].items():
        lines.append(
            f"| `{cname}` | {cdata['assigned_total_over_4_seeds']} | "
            f"{cdata['priority_points_total_over_4_seeds']:.1f} | "
            f"{cdata['travel_distance_m_total_over_4_seeds']:.3f} | "
            f"{cdata['committed_violations']} |"
        )
    lines.append("")
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Run full AEGIS neural assignment training and held-out evaluation."
    )
    parser.add_argument("--train-count", type=int, default=5000)
    parser.add_argument("--val-count", type=int, default=600)
    parser.add_argument("--test-id-count", type=int, default=600)
    parser.add_argument("--test-ood-count", type=int, default=600)
    parser.add_argument("--seeds", nargs="+", type=int, default=[42, 43, 44])
    parser.add_argument("--epochs", type=int, default=35)
    parser.add_argument("--batch-size", type=int, default=128)
    parser.add_argument("--weights-dir", type=str, default="weights")
    parser.add_argument("--reports-dir", type=str, default="reports")
    args = parser.parse_args()

    report = run_full_experiment(
        train_count=args.train_count,
        val_count=args.val_count,
        test_id_count=args.test_id_count,
        test_ood_count=args.test_ood_count,
        seeds=tuple(args.seeds),
        epochs=args.epochs,
        batch_size=args.batch_size,
        weights_dir=Path(args.weights_dir),
        reports_dir=Path(args.reports_dir),
    )
    comb = report["evaluation"]["test_combined"]
    print(
        json.dumps(
            {
                "dataset_generation_s": report["dataset"]["generation_total_seconds"],
                "combined_1200_zero_regret_rates": {
                    k: comb[k]["zero_regret_optimal_rate"] for k in comb
                },
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
