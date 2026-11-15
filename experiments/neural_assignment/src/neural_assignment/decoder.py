"""Constrained assignment decoding from neural pairwise scores.

Enforces all hard safety and operational gates independently of the neural model:
- One-to-one assignment
- Responder availability
- Capability compatibility
- Valid observation & tracking eligibility
- Verified A* route reachability
- Active assignment reservation protection

Provides both:
1. Constrained Greedy Decoder (`greedy`)
2. Constrained Maximum-Cardinality Neural-Score Bipartite Matching Decoder (`matching`)

Also measures unfiltered raw proposals before hard-constraint filtering as an
explicit safety diagnostic. The model never authorizes dispatch.
"""

from __future__ import annotations

from dataclasses import dataclass
import math
import numpy as np

from neural_assignment.features import SceneFeatures, extract_features_from_request
from neural_assignment.validator import validate_assignments
from optimization.models import OptimizationRequest


@dataclass(frozen=True)
class DecodedAssignmentResult:
    decoder: str
    selected_pairs: list[tuple[str, str]]
    unassigned_casualty_ids: list[str]
    assigned_count: int
    allocation_priority_points: float
    travel_distance_m: float
    travel_cost_mm: float
    invalid_proposed_before_filtering: int
    committed_constraint_violations: int
    violation_details: list[str]
    authorizes_dispatch: bool = False


def _sigmoid(x: np.ndarray) -> np.ndarray:
    clipped = np.clip(x, -30.0, 30.0)
    return 1.0 / (1.0 + np.exp(-clipped))


def count_unfiltered_invalid_proposals(
    logits: np.ndarray,
    scene_features: SceneFeatures,
    threshold: float = 0.0,
) -> int:
    """Count how many invalid pairs a naive unfiltered greedy selector would propose.

    This diagnostic selects one-to-one pairs from raw logits > threshold without
    consulting `feasible_mask`, and counts how many selected pairs violate hard
    feasibility constraints.
    """
    n_cas, n_res = logits.shape
    candidates: list[tuple[float, int, int]] = []
    for i in range(n_cas):
        for j in range(n_res):
            score = float(logits[i, j])
            if score >= threshold:
                candidates.append((score, i, j))
    candidates.sort(
        key=lambda item: (
            -item[0],
            scene_features.casualty_ids[item[1]],
            scene_features.responder_ids[item[2]],
        )
    )
    used_c: set[int] = set()
    used_r: set[int] = set()
    invalid_count = 0
    for _, i, j in candidates:
        if i in used_c or j in used_r:
            continue
        used_c.add(i)
        used_r.add(j)
        if not bool(scene_features.feasible_mask[i, j]):
            invalid_count += 1
    return invalid_count


def decode_constrained_greedy(
    logits: np.ndarray,
    scene_features: SceneFeatures,
) -> list[tuple[str, str]]:
    """Greedily decode one-to-one assignments strictly over feasible pairs."""
    n_cas, n_res = logits.shape
    feasible_candidates: list[tuple[float, str, str, int, int]] = []
    for i in range(n_cas):
        cid = scene_features.casualty_ids[i]
        for j in range(n_res):
            if not bool(scene_features.feasible_mask[i, j]):
                continue
            rid = scene_features.responder_ids[j]
            feasible_candidates.append((float(logits[i, j]), cid, rid, i, j))

    feasible_candidates.sort(key=lambda item: (-item[0], item[1], item[2]))
    used_c: set[int] = set()
    used_r: set[int] = set()
    selected: list[tuple[str, str]] = []

    for _, cid, rid, i, j in feasible_candidates:
        if i in used_c or j in used_r:
            continue
        used_c.add(i)
        used_r.add(j)
        selected.append((cid, rid))

    return sorted(selected)


def decode_constrained_matching(
    logits: np.ndarray,
    scene_features: SceneFeatures,
) -> list[tuple[str, str]]:
    """Solve exact maximum-cardinality, maximum-neural-score bipartite matching on feasible edges.

    Every feasible edge receives weight (CARDINALITY_BOOST + bounded_score) so that:
    1. Only hard-feasible edges can ever be chosen.
    2. Feasible assignment cardinality is maximized first.
    3. Among maximum-cardinality matchings, total learned neural score is maximized.
    """
    n_cas, n_res = logits.shape
    if n_cas == 0 or n_res == 0 or not np.any(scene_features.feasible_mask):
        return []

    probs = _sigmoid(logits)
    # Bounded smooth score in (0, 2.0) preserving monotonic logit ordering even for large logits
    bounded_score = probs + 0.5 * (np.tanh(logits / 6.0) + 1.0)
    cardinality_boost = 100.0

    # Pre-build adjacency list of feasible edges with deterministic tie-break offsets
    adj: list[list[tuple[int, float]]] = [[] for _ in range(n_cas)]
    for i in range(n_cas):
        for j in range(n_res):
            if bool(scene_features.feasible_mask[i, j]):
                tie_break = -1e-7 * float(i * max(n_res, 1) + j)
                weight = cardinality_boost + float(bounded_score[i, j]) + tie_break
                adj[i].append((j, weight))

    # Exact bitmask DP over responder subsets (since R <= 10, 2^R <= 1024 states)
    num_masks = 1 << n_res
    dp = np.full((n_cas + 1, num_masks), -1.0, dtype=np.float64)
    parent_mask = np.full((n_cas + 1, num_masks), -1, dtype=np.int32)
    parent_res = np.full((n_cas + 1, num_masks), -1, dtype=np.int32)
    dp[0, 0] = 0.0

    for i in range(n_cas):
        # Transition 1: leave casualty i unassigned
        dp[i + 1] = dp[i]
        for m in range(num_masks):
            if dp[i, m] >= 0.0:
                parent_mask[i + 1, m] = m
                parent_res[i + 1, m] = -1

        # Transition 2: assign casualty i to a feasible free responder j
        for j, weight in adj[i]:
            bit = 1 << j
            for m in range(num_masks):
                if (m & bit) or dp[i, m] < 0.0:
                    continue
                next_m = m | bit
                cand = dp[i, m] + weight
                if cand > dp[i + 1, next_m]:
                    dp[i + 1, next_m] = cand
                    parent_mask[i + 1, next_m] = m
                    parent_res[i + 1, next_m] = j

    best_mask = int(np.argmax(dp[n_cas]))
    if dp[n_cas, best_mask] <= 0.0:
        return []

    selected: list[tuple[str, str]] = []
    curr_mask = best_mask
    for i in range(n_cas, 0, -1):
        chosen_j = int(parent_res[i, curr_mask])
        prev_m = int(parent_mask[i, curr_mask])
        if chosen_j >= 0:
            cid = scene_features.casualty_ids[i - 1]
            rid = scene_features.responder_ids[chosen_j]
            selected.append((cid, rid))
        curr_mask = prev_m

    return sorted(selected)


def decode_and_validate(
    logits: np.ndarray,
    request: OptimizationRequest,
    scene_features: SceneFeatures | None = None,
    decoder: str = "matching",
) -> DecodedAssignmentResult:
    """Decode assignments from neural logits and verify through the independent validator."""
    sf = scene_features or extract_features_from_request(request)
    invalid_unfiltered = count_unfiltered_invalid_proposals(logits, sf, threshold=0.0)

    if decoder == "greedy":
        pairs = decode_constrained_greedy(logits, sf)
    elif decoder in {"matching", "hungarian"}:
        pairs = decode_constrained_matching(logits, sf)
    else:
        raise ValueError(f"Unsupported decoder: {decoder}")

    check = validate_assignments(request, pairs)
    active_casualties = {a.casualty_id for a in request.active_assignments}
    assigned_casualties = {cid for cid, _ in pairs}
    unassigned = sorted(
        c.id
        for c in request.scenario.casualties
        if c.id not in assigned_casualties and c.id not in active_casualties
    )

    return DecodedAssignmentResult(
        decoder=decoder,
        selected_pairs=pairs,
        unassigned_casualty_ids=unassigned,
        assigned_count=int(check["assigned_count"]),
        allocation_priority_points=float(check["allocation_priority_points"]),
        travel_distance_m=float(check["travel_distance_m"]),
        travel_cost_mm=float(check["travel_cost_mm"]),
        invalid_proposed_before_filtering=invalid_unfiltered,
        committed_constraint_violations=int(check["violation_count"]),
        violation_details=list(check["violations"]),
        authorizes_dispatch=False,
    )
