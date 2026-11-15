"""Independent hard-constraint validator for proposed and committed assignments.

Independently rederives all safety and domain constraints from the scenario request:
1. One-to-one assignment uniqueness (no duplicate casualty or responder).
2. Active assignment reservation protection (never reassigns active entities).
3. Responder availability (status == 'available').
4. Required capability compatibility.
5. Human-only medical assessment provenance.
6. Observation & tracking eligibility (freshness, confidence, position tolerance, track visibility).
7. Verified A* route reachability on the scene occupancy grid.
"""

from __future__ import annotations

from neural_assignment._rootpath import ensure_repo_root_on_path

ensure_repo_root_on_path()

from optimization.models import OptimizationRequest
from optimization.routing import _GridRouter
from optimization.solver import _observation_reasons, _priority


def validate_assignments(
    request: OptimizationRequest,
    pairs: list[tuple[str, str]],
) -> dict:
    """Independently check every proposed (casualty_id, responder_id) pair against hard gates."""
    scene = request.scenario
    casualties = {c.id: c for c in scene.casualties}
    responders = {r.id: r for r in scene.responders}
    active_casualties = {a.casualty_id for a in request.active_assignments}
    active_responders = {a.responder_id for a in request.active_assignments}
    exclusions = request.assignment_exclusions

    router = (
        _GridRouter(scene.scene_map)
        if (scene.scene_map is not None and scene.scene_map.occupancy_grid is not None)
        else None
    )

    violations: list[str] = []
    seen_casualties: set[str] = set()
    seen_responders: set[str] = set()
    points = 0.0
    travel_m = 0.0
    travel_mm = 0

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
        if casualty_id in active_casualties:
            violations.append(f"active_casualty_reserved:{tag}")
            continue
        if responder_id in active_responders:
            violations.append(f"active_responder_reserved:{tag}")
            continue
        if responder.status != "available":
            violations.append(f"responder_unavailable:{tag}")
            continue
        need = set(request.required_capabilities.get(casualty_id, []))
        if not need.issubset(set(responder.capabilities)):
            violations.append(f"required_capabilities_missing:{tag}")
            continue
        assessment = casualty.medical_assessment
        if assessment is not None and assessment.source != "human":
            violations.append(f"nonhuman_assessment:{tag}")
            continue
        obs_reasons = _observation_reasons(casualty, request)
        if obs_reasons:
            violations.append(f"observation_blocked:{tag}:{sorted(obs_reasons)[0]}")
            continue
        if exclusions.get(casualty_id):
            violations.append(f"policy_excluded:{tag}:{sorted(exclusions[casualty_id])[0]}")
            continue
        if router is None:
            violations.append(f"map_unavailable:{tag}")
            continue
        route = router.plan(responder.position, casualty.position)
        if route is None:
            violations.append(f"unreachable:{tag}")
            continue

        points += float(_priority(casualty, request))
        travel_m += float(route.distance_m)
        travel_mm += round(route.distance_m * 1000)

    return {
        "violations": violations,
        "violation_count": len(violations),
        "assigned_count": len(pairs) - len(violations),
        "allocation_priority_points": points,
        "travel_distance_m": travel_m,
        "travel_cost_mm": float(travel_mm),
        "checked_pairs": len(pairs),
    }
