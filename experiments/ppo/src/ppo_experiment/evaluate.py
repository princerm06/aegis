"""Heldout evaluation of trained PPO policies against core solver baselines.

Each heldout scenario is fixed by seed and disjoint from training
seeds. Every contender sees the identical Scenario payload with the
same capability, availability, routing, assessment, and tracking
gates. PPO rolls out sequentially in the env. Baselines solve the
same bundle one-shot through the real core optimizer.
"""

from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path
from time import perf_counter

from ppo_experiment import _rootpath as _rp

_rp.ensure_parent_on_path()

from ppo_experiment.baselines import run_baseline  # noqa: E402
from ppo_experiment.env import AllocationEnv  # noqa: E402
from ppo_experiment.scenarios import EVAL_SEEDS, build_bundle  # noqa: E402


def rollout_ppo(eval_seed: int, weights: str) -> dict:
    from stable_baselines3 import PPO

    env = AllocationEnv(scenario_seed=eval_seed)
    model = PPO.load(weights, device="cpu")
    obs, _ = env.reset(seed=eval_seed)
    started = perf_counter()
    terminated = False
    truncated = False
    while not (terminated or truncated):
        action, _ = model.predict(obs, deterministic=True)
        obs, _, terminated, truncated, _ = env.step(int(action))
    wall_ms = (perf_counter() - started) * 1000.0
    metrics = env.committed_metrics()
    scenario = env.bundle["scenario"]
    unassigned = [
        c.id for i, c in enumerate(scenario.casualties) if i not in env.assigned_casualties
    ]
    return {
        "solver": "ppo",
        "engine": "ppo-sequential",
        "assigned_count": metrics["assigned_count"],
        "allocation_priority_points": metrics["allocation_priority_points"],
        "travel_distance_m": metrics["travel_distance_m"],
        "unassigned_casualty_ids": sorted(unassigned),
        "unassigned_count": len(unassigned),
        "assignments": [
            {"casualty_id": c, "responder_id": r} for c, r in env.committed_pairs()
        ],
        "wall_runtime_ms": float(wall_ms),
        "committed_violations": metrics["committed_violations"],
        "invalid_attempts": metrics["invalid_attempts"],
    }


def evaluate(weights_list: list[str], eval_seeds: list[int], outdir: str) -> dict:
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
            ppo_row = rollout_ppo(eval_seed, weights)
            ppo_row["eval_seed"] = eval_seed
            ppo_row["weights"] = Path(weights).name
            rows.append(ppo_row)
    report = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "eval_seeds": list(eval_seeds),
        "weights": [Path(w).name for w in weights_list],
        "rows": rows,
    }
    out = Path(outdir)
    out.mkdir(parents=True, exist_ok=True)
    (out / "eval_results.json").write_text(json.dumps(report, indent=2))
    lines = [
        "eval_seed,contender,assigned,points,travel_m,unassigned,runtime_ms,violations,invalid_attempts",
    ]
    for row in rows:
        name = row["solver"] if row["solver"] != "ppo" else f"ppo[{row.get('weights', '')}]"
        runtime = row.get("wall_runtime_ms", row.get("solver_runtime_ms", 0.0))
        lines.append(
            f"{row['eval_seed']},{name},{row['assigned_count']},"
            f"{row['allocation_priority_points']},{row['travel_distance_m']:.3f},"
            f"{row['unassigned_count']},{runtime:.2f},"
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
