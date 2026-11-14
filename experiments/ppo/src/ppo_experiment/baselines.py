"""One-shot core solver baselines over the identical scenario bundles."""

from __future__ import annotations

from time import perf_counter

from ppo_experiment import _rootpath as _rp

_rp.ensure_parent_on_path()

from optimization.solver import optimize  # noqa: E402
from ppo_experiment.scenarios import make_request  # noqa: E402


def run_baseline(bundle: dict, solver: str) -> dict:
    request = make_request(bundle, solver)
    started = perf_counter()
    result = optimize(request)
    wall_ms = (perf_counter() - started) * 1000.0
    values = dict(result.objective_values)
    return {
        "solver": solver,
        "engine": result.engine,
        "assigned_count": int(values.get("new_assigned_count", 0.0)),
        "allocation_priority_points": float(values.get("allocation_priority_points", 0.0)),
        "travel_distance_m": float(values.get("new_travel_distance_m", 0.0)),
        "unassigned_casualty_ids": list(result.unassigned_casualty_ids),
        "unassigned_count": len(result.unassigned_casualty_ids),
        "assignments": [
            {"casualty_id": a.casualty_id, "responder_id": a.responder_id}
            for a in result.assignments
        ],
        "solver_runtime_ms": float(result.runtime_ms),
        "wall_runtime_ms": float(wall_ms),
        "committed_violations": 0,
        "invalid_attempts": 0,
    }
