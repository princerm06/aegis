"""Heldout evaluation of trained PPO policies against core solver baselines.

Each heldout scenario is fixed by seed and disjoint from training
seeds. Every contender sees the identical Scenario payload with the
same capability, availability, routing, assessment, and tracking
gates. Baselines solve the same bundle one-shot through the real core
optimizer. PPO rolls out sequentially in the env with deterministic
actions. All committed pairs pass through the shared independent
validator.

Timing compares prep plus run totals after a warmup that moves
imports, model loading, and first-call optimizer setup outside the
measured region. No speed claim is made from cold-start effects.
"""

from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path
from time import perf_counter

from ppo_experiment import _rootpath as _rp

_rp.ensure_parent_on_path()

from ppo_experiment.baselines import run_baseline
from ppo_experiment.env import AllocationEnv
from ppo_experiment.scenarios import EVAL_SEEDS, TRAIN_SEEDS, build_bundle
from ppo_experiment.validator import validate


def rollout_ppo(eval_seed: int, weights: str, model) -> dict:
    prep_started = perf_counter()
    env = AllocationEnv(scenario_seed=eval_seed)
    obs, _ = env.reset(seed=eval_seed)
    prep_ms = (perf_counter() - prep_started) * 1000.0
    rollout_started = perf_counter()
    terminated = False
    truncated = False
    while not (terminated or truncated):
        action, _ = model.predict(obs, deterministic=True)
        obs, _, terminated, truncated, _ = env.step(int(action))
    rollout_ms = (perf_counter() - rollout_started) * 1000.0
    check = validate(env.bundle, env.committed_pairs())
    scenario = env.bundle["scenario"]
    unassigned = [
        c.id for i, c in enumerate(scenario.casualties) if i not in env.assigned_casualties
    ]
    return {
        "solver": "ppo",
        "engine": "ppo-sequential",
        "assigned_count": len(env.committed),
        "allocation_priority_points": float(check["allocation_priority_points"]),
        "travel_distance_m": float(check["travel_distance_m"]),
        "unassigned_casualty_ids": sorted(unassigned),
        "unassigned_count": len(unassigned),
        "assignments": [
            {"casualty_id": c, "responder_id": r} for c, r in env.committed_pairs()
        ],
        "prep_ms": float(prep_ms),
        "run_ms": float(rollout_ms),
        "total_ms": float(prep_ms + rollout_ms),
        "committed_violations": int(check["violation_count"]),
        "violation_details": list(check["violations"]),
        "invalid_attempts": env.invalid_attempts,
    }


def evaluate(weights_list: list[str], eval_seeds: list[int], outdir: str) -> dict:
    from stable_baselines3 import PPO

    warmup_bundle = build_bundle(TRAIN_SEEDS[0])
    run_baseline(warmup_bundle, "ortools")
    run_baseline(warmup_bundle, "nearest")
    models = {w: PPO.load(w, device="cpu") for w in weights_list}
    warmup_env = AllocationEnv(scenario_seed=TRAIN_SEEDS[0])
    warmup_obs, _ = warmup_env.reset(seed=TRAIN_SEEDS[0])
    models[weights_list[0]].predict(warmup_obs, deterministic=True)
    rows: list[dict] = []
    for eval_seed in eval_seeds:
        bundle = build_bundle(eval_seed)
        ortools_row = run_baseline(bundle, "ortools")
        ortools_row["eval_seed"] = eval_seed
        rows.append(ortools_row)
        nearest_row = run_baseline(bundle, "nearest")
        nearest_row["eval_seed"] = eval_seed
        rows.append(nearest_row)
        for weights in weights_list:
            ppo_row = rollout_ppo(eval_seed, weights, models[weights])
            ppo_row["eval_seed"] = eval_seed
            ppo_row["weights"] = Path(weights).name
            rows.append(ppo_row)
    report = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "eval_seeds": list(eval_seeds),
        "weights": [Path(w).name for w in weights_list],
        "timing_note": (
            "Totals compare prep plus run after warmup moved imports, model "
            "loading, and first-call optimizer setup outside measurement. "
            "No speed ranking is claimed from cold-start effects."
        ),
        "rows": rows,
    }
    out = Path(outdir)
    out.mkdir(parents=True, exist_ok=True)
    (out / "eval_results.json").write_text(json.dumps(report, indent=2))
    lines = [
        "eval_seed,contender,assigned,points,travel_m,unassigned,prep_ms,run_ms,total_ms,violations,invalid_attempts",
    ]
    for row in rows:
        name = row["solver"] if row["solver"] != "ppo" else f"ppo[{row.get('weights', '')}]"
        lines.append(
            f"{row['eval_seed']},{name},{row['assigned_count']},"
            f"{row['allocation_priority_points']},{row['travel_distance_m']:.3f},"
            f"{row['unassigned_count']},{row['prep_ms']:.2f},{row['run_ms']:.2f},"
            f"{row['total_ms']:.2f},"
            f"{row['committed_violations']},{row['invalid_attempts']}"
        )
    (out / "eval_results.md").write_text("\n".join(lines) + "\n")
    return report


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--weights", type=str, nargs="+", required=True)
    parser.add_argument("--eval-seeds", type=int, nargs="+", default=list(EVAL_SEEDS))
    parser.add_argument("--outdir", type=str, required=True)
    args = parser.parse_args()
    report = evaluate(args.weights, args.eval_seeds, args.outdir)
    print(json.dumps({k: v for k, v in report.items() if k != "rows"}, indent=2))


if __name__ == "__main__":
    main()
