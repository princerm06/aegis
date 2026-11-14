"""One-shot core solver baselines over identical scenario bundles.

Every returned pair passes through the shared independent validator,
so a baseline violation would be reported rather than assumed away.
Prep time covers request construction and timed solve covers the real
core optimizer call.
"""

from __future__ import annotations

from time import perf_counter

from ppo_experiment import _rootpath as _rp

_rp.ensure_parent_on_path()

from optimization.solver import optimize
from ppo_experiment.scenarios import make_request
from ppo_experiment.validator import validate


def run_baseline(bundle: dict, solver: str) -> dict:
    prep_started = perf_counter()
    request = make_request(bundle, solver)
    prep_ms = (perf_counter() - prep_started) * 1000.0
    solve_started = perf_counter()
    result = optimize(request)
    solve_ms = (perf_counter() - solve_started) * 1000.0
    pairs = [(a.casualty_id, a.responder_id) for a in result.assignments]
    check = validate(bundle, pairs)
    values = dict(result.objective_values)
    return {
        "solver": solver,
        "engine": result.engine,
        "assigned_count": int(values.get("new_assigned_count", 0.0)),
        "allocation_priority_points": float(check["allocation_priority_points"]),
        "travel_distance_m": float(check["travel_distance_m"]),
        "unassigned_casualty_ids": list(result.unassigned_casualty_ids),
        "unassigned_count": len(result.unassigned_casualty_ids),
        "assignments": [
            {"casualty_id": a.casualty_id, "responder_id": a.responder_id}
            for a in result.assignments
        ],
        "solver_runtime_ms": float(result.runtime_ms),
        "prep_ms": float(prep_ms),
        "run_ms": float(solve_ms),
        "total_ms": float(prep_ms + solve_ms),
        "committed_violations": int(check["violation_count"]),
        "violation_details": list(check["violations"]),
        "invalid_attempts": 0,
    }
