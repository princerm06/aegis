"""Independent feasibility validator shared by PPO rollouts and core baselines.

The validator rederives every hard gate from the scenario bundle and the
committed pairs without trusting either contender: one-to-one
uniqueness, responder availability, capability compatibility, core
observation and tracking reasons, A* route reachability with distances,
and well-formed human assessments. Any breach is a committed safety
violation. Filtering a proposal before commitment never reaches this
validator.
"""

from __future__ import annotations

from ppo_experiment import _rootpath as _rp

_rp.ensure_parent_on_path()

from optimization.routing import _GridRouter
from optimization.solver import _observation_reasons, _priority
from optimization.models import OptimizationRequest


def validate(bundle: dict, pairs: list[tuple[str, str]]) -> dict:
    scenario = bundle["scenario"]
    required = bundle.get("required_capabilities", {})
    observations = bundle.get("observations", {})
    casualties = {c.id: c for c in scenario.casualties}
    responders = {r.id: r for r in scenario.responders}
    request = OptimizationRequest(
        scenario=scenario,
        required_capabilities=dict(required),
        observations=dict(observations),
        now=bundle["now"],
    )
    router = _GridRouter(scenario.scene_map)
    violations: list[str] = []
    seen_casualties: set[str] = set()
    seen_responders: set[str] = set()
    points = 0.0
    travel = 0.0
    for casualty_id, responder_id in pairs:
        tag = f"{casualty_id}/{responder_id}"
        if casualty_id in seen_casualties:
            violations.append(f"duplicate_casualty:{tag}")
            continue
        if responder_id in seen_responders:
            violations.append(f"duplicate_responder:{tag}")
            continue
        seen_casualties.add(casualty_id)
        seen_responders.add(responder_id)
        casualty = casualties.get(casualty_id)
        responder = responders.get(responder_id)
        if casualty is None or responder is None:
            violations.append(f"unknown_entity:{tag}")
            continue
        if responder.status != "available":
            violations.append(f"responder_unavailable:{tag}")
            continue
        need = set(required.get(casualty_id, []))
        if not need.issubset(set(responder.capabilities)):
            violations.append(f"required_capabilities_missing:{tag}")
            continue
        assessment = casualty.medical_assessment
        if assessment is not None and assessment.source != "human":
            violations.append(f"nonhuman_assessment:{tag}")
            continue
        reasons = _observation_reasons(casualty, request)
        if reasons:
            violations.append(f"observation_blocked:{tag}:{sorted(reasons)[0]}")
            continue
        route = router.plan(responder.position, casualty.position)
        if route is None:
            violations.append(f"unreachable:{tag}")
            continue
        points += float(_priority(casualty, request))
        travel += float(route.distance_m)
    return {
        "violations": violations,
        "violation_count": len(violations),
        "allocation_priority_points": points,
        "travel_distance_m": travel,
        "checked_pairs": len(pairs),
    }
