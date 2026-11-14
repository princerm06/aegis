"""Pairwise, node, and scene-context feature extraction for neural assignment scoring.

All features are derived strictly from pre-assignment inputs (Scenario,
required_capabilities, active_assignments, observations, now). Post-assignment
solver outputs or teacher labels are never accessed during feature construction.
Hard feasibility is computed explicitly and preserved as a separate binary mask
so the neural model never decides what is safe or legal to dispatch.
"""

from __future__ import annotations

from dataclasses import dataclass
import math
import numpy as np

from neural_assignment import FEATURE_SCHEMA_VERSION
from neural_assignment._rootpath import ensure_repo_root_on_path

ensure_repo_root_on_path()

from backend.app.intelligence.uncertainty import evaluate_uncertainty
from backend.app.models import AStarRoute
from optimization.models import OptimizationRequest
from optimization.routing import _GridRouter
from optimization.solver import _observation_reasons, _priority

PAIR_FEATURE_NAMES: tuple[str, ...] = (
    # Geometry & normalized positions (0..8)
    "cas_x_norm",
    "cas_y_norm",
    "res_x_norm",
    "res_y_norm",
    "dx_norm",
    "dy_norm",
    "euclid_dist_norm",
    "manhattan_dist_norm",
    "grid_scale_norm",
    # Verified A* route & reachability (9..11)
    "astar_reachable",
    "astar_dist_norm",
    "detour_ratio_norm",
    # Human-confirmed priority (12..17)
    "priority_norm",
    "prio_is_2",
    "prio_is_1",
    "prio_is_0",
    "is_unassessed_protected",
    "is_expectant_or_deceased_protected",
    # Responder availability, active reservation & capabilities (18..28)
    "res_is_available",
    "res_is_busy",
    "res_is_offline",
    "cas_reserved_active",
    "res_reserved_active",
    "capability_compatible",
    "req_bls",
    "req_als",
    "req_medevac",
    "res_cap_count_norm",
    "res_surplus_caps_norm",
    # Tracking freshness, confidence & uncertainty flags (29..36)
    "has_cv_observation",
    "raw_confidence",
    "decayed_ranking_score",
    "obs_age_norm",
    "flag_obs_stale",
    "flag_obs_low_conf",
    "flag_obs_pos_mismatch",
    "flag_track_not_visible",
    # Hard feasibility & local/global competition context (37..47)
    "pair_hard_feasible",
    "scene_cas_count_norm",
    "scene_res_count_norm",
    "scene_avail_ratio",
    "cas_feasible_deg_norm",
    "res_feasible_deg_norm",
    "cas_dist_gap_from_best",
    "res_dist_gap_from_best",
    "cas_responder_rank_norm",
    "res_casualty_rank_norm",
    "res_higher_prio_competitor_frac",
)

CASUALTY_NODE_FEATURE_DIM = 18
RESPONDER_NODE_FEATURE_DIM = 12
PAIR_FEATURE_DIM = len(PAIR_FEATURE_NAMES)


@dataclass
class SceneFeatures:
    """Pre-assignment feature tensors and exact feasibility graph for one scenario."""

    schema_version: str
    casualty_ids: list[str]
    responder_ids: list[str]
    pair_features: np.ndarray  # shape: (C, R, PAIR_FEATURE_DIM), float32
    casualty_node_features: np.ndarray  # shape: (C, CASUALTY_NODE_FEATURE_DIM), float32
    responder_node_features: np.ndarray  # shape: (R, RESPONDER_NODE_FEATURE_DIM), float32
    feasible_mask: np.ndarray  # shape: (C, R), bool
    astar_distances_m: np.ndarray  # shape: (C, R), float32 (inf where unreachable)
    priorities: np.ndarray  # shape: (C,), int64
    candidate_routes: dict[tuple[str, str], AStarRoute]


def extract_features_from_request(
    request: OptimizationRequest,
    precomputed_all_routes: dict[tuple[str, str], AStarRoute | None] | None = None,
) -> SceneFeatures:
    """Extract pairwise, node, and competition features purely from an OptimizationRequest."""
    scene = request.scenario
    scene_map = scene.scene_map
    casualties = list(scene.casualties)
    responders = list(scene.responders)
    n_cas = len(casualties)
    n_res = len(responders)

    casualty_ids = [c.id for c in casualties]
    responder_ids = [r.id for r in responders]

    if scene_map is not None and scene_map.occupancy_grid is not None:
        width = float(scene_map.occupancy_grid.width * scene_map.occupancy_grid.resolution_m)
        height = float(scene_map.occupancy_grid.height * scene_map.occupancy_grid.resolution_m)
        router = _GridRouter(scene_map) if precomputed_all_routes is None else None
    else:
        width, height = 12.0, 12.0
        router = None

    diag = math.hypot(width, height)
    manhattan_scale = width + height
    grid_scale_norm = max(width, height) / 16.0

    active_casualties = {a.casualty_id for a in request.active_assignments}
    active_responders = {a.responder_id for a in request.active_assignments}
    exclusions = request.assignment_exclusions

    priorities = np.zeros(n_cas, dtype=np.int64)
    cas_eligible = np.zeros(n_cas, dtype=bool)
    cas_obs_meta: list[dict] = []

    for i, c in enumerate(casualties):
        prio = _priority(c, request)
        priorities[i] = prio
        obs = request.observations.get(c.id)
        reasons = _observation_reasons(c, request)
        unc_report = evaluate_uncertainty(
            obs, request.now, authoritative_position=c.position
        )
        blocked = (
            (c.id in active_casualties)
            or bool(reasons)
            or bool(exclusions.get(c.id))
        )
        cas_eligible[i] = not blocked
        has_obs = obs is not None
        raw_conf = float(obs.confidence) if obs is not None else 1.0
        decayed = (
            float(unc_report.ranking_score)
            if unc_report.ranking_score is not None
            else 1.0
        )
        age_s = (
            float(unc_report.report_age_seconds)
            if unc_report.report_age_seconds is not None
            else 0.0
        )
        age_norm = min(max(age_s, 0.0), 60.0) / 60.0
        reason_set = set(reasons)
        cas_obs_meta.append(
            {
                "has_obs": 1.0 if has_obs else 0.0,
                "raw_conf": raw_conf,
                "decayed": decayed,
                "age_norm": age_norm,
                "stale": 1.0
                if ("observation_stale" in reason_set or "track_stale" in reason_set)
                else 0.0,
                "low_conf": 1.0 if "observation_low_confidence" in reason_set else 0.0,
                "pos_mismatch": 1.0
                if "observation_position_mismatch" in reason_set
                else 0.0,
                "track_not_vis": 1.0
                if ("track_occluded" in reason_set or "track_lost" in reason_set)
                else 0.0,
            }
        )

    astar_distances_m = np.full((n_cas, n_res), np.inf, dtype=np.float32)
    reachable_mask = np.zeros((n_cas, n_res), dtype=bool)
    feasible_mask = np.zeros((n_cas, n_res), dtype=bool)
    cap_compat_mask = np.zeros((n_cas, n_res), dtype=bool)
    candidate_routes: dict[tuple[str, str], AStarRoute] = {}

    for i, c in enumerate(casualties):
        req_set = set(request.required_capabilities.get(c.id, []))
        for j, r in enumerate(responders):
            compat = req_set.issubset(r.capabilities)
            cap_compat_mask[i, j] = compat
            if precomputed_all_routes is not None:
                route = precomputed_all_routes.get((c.id, r.id))
            elif router is not None:
                route = router.plan(r.position, c.position)
            else:
                route = None

            if route is not None:
                reachable_mask[i, j] = True
                astar_distances_m[i, j] = float(route.distance_m)

            is_feas = (
                cas_eligible[i]
                and (r.id not in active_responders)
                and (r.status == "available")
                and compat
                and (route is not None)
            )
            if is_feas and route is not None:
                feasible_mask[i, j] = True
                candidate_routes[(c.id, r.id)] = route

    # Compute degrees, minimum distances, and ranks within the feasible bipartite graph
    cas_deg = feasible_mask.sum(axis=1)  # (C,)
    res_deg = feasible_mask.sum(axis=0)  # (R,)

    cas_min_dist = np.full(n_cas, manhattan_scale, dtype=np.float32)
    for i in range(n_cas):
        feas_j = np.where(feasible_mask[i])[0]
        if len(feas_j) > 0:
            cas_min_dist[i] = float(np.min(astar_distances_m[i, feas_j]))

    res_min_dist = np.full(n_res, manhattan_scale, dtype=np.float32)
    for j in range(n_res):
        feas_i = np.where(feasible_mask[:, j])[0]
        if len(feas_i) > 0:
            res_min_dist[j] = float(np.min(astar_distances_m[feas_i, j]))

    # Rank of responder j for casualty i among feasible responders (0.0 = closest)
    cas_responder_rank = np.ones((n_cas, n_res), dtype=np.float32)
    for i in range(n_cas):
        feas_j = np.where(feasible_mask[i])[0]
        if len(feas_j) > 0:
            order = sorted(feas_j, key=lambda jj: (astar_distances_m[i, jj], responder_ids[jj]))
            denom = max(len(order) - 1, 1)
            for rank_idx, jj in enumerate(order):
                cas_responder_rank[i, jj] = float(rank_idx) / float(denom) if len(order) > 1 else 0.0

    # Rank of casualty i for responder j sorted by (-priority, distance)
    res_casualty_rank = np.ones((n_cas, n_res), dtype=np.float32)
    res_higher_prio_frac = np.ones((n_cas, n_res), dtype=np.float32)
    for j in range(n_res):
        feas_i = np.where(feasible_mask[:, j])[0]
        if len(feas_i) > 0:
            order = sorted(
                feas_i,
                key=lambda ii: (-int(priorities[ii]), astar_distances_m[ii, j], casualty_ids[ii]),
            )
            denom = max(len(order) - 1, 1)
            for rank_idx, ii in enumerate(order):
                res_casualty_rank[ii, j] = float(rank_idx) / float(denom) if len(order) > 1 else 0.0
                higher_count = sum(1 for other_i in feas_i if priorities[other_i] > priorities[ii])
                res_higher_prio_frac[ii, j] = float(higher_count) / float(len(feas_i))

    n_avail_unreserved = sum(
        1 for r in responders if r.status == "available" and r.id not in active_responders
    )
    n_elig_unreserved = int(np.sum(cas_eligible))
    avail_ratio = min(float(n_avail_unreserved) / max(float(n_elig_unreserved), 1.0), 3.0) / 3.0
    cas_count_norm = float(n_cas) / 6.0
    res_count_norm = float(n_res) / 6.0

    pair_features = np.zeros((n_cas, n_res, PAIR_FEATURE_DIM), dtype=np.float32)
    casualty_node_features = np.zeros((n_cas, CASUALTY_NODE_FEATURE_DIM), dtype=np.float32)
    responder_node_features = np.zeros((n_res, RESPONDER_NODE_FEATURE_DIM), dtype=np.float32)

    for i, c in enumerate(casualties):
        cx_norm = float(c.position.x_m) / width
        cy_norm = float(c.position.y_m) / height
        prio = int(priorities[i])
        prio_norm = float(prio) / 2.0
        prio_2 = 1.0 if prio == 2 else 0.0
        prio_1 = 1.0 if prio == 1 else 0.0
        prio_0 = 1.0 if prio == 0 else 0.0
        unassessed = 1.0 if c.medical_assessment is None else 0.0
        exp_or_dec = (
            1.0
            if (
                c.medical_assessment is not None
                and c.medical_assessment.severity in {"expectant", "deceased"}
            )
            else 0.0
        )
        c_act = 1.0 if c.id in active_casualties else 0.0
        req_set = set(request.required_capabilities.get(c.id, []))
        req_bls = 1.0 if "bls" in req_set else 0.0
        req_als = 1.0 if "als" in req_set else 0.0
        req_med = 1.0 if "medevac" in req_set else 0.0
        meta = cas_obs_meta[i]
        c_deg_norm = float(cas_deg[i]) / max(float(n_res), 1.0)
        c_min_d_norm = min(float(cas_min_dist[i]) / manhattan_scale, 1.5)

        casualty_node_features[i] = np.array(
            [
                cx_norm,
                cy_norm,
                prio_norm,
                prio_2,
                prio_1,
                prio_0,
                unassessed,
                exp_or_dec,
                c_act,
                req_bls,
                req_als,
                req_med,
                meta["has_obs"],
                meta["raw_conf"],
                meta["decayed"],
                1.0 if cas_eligible[i] else 0.0,
                c_deg_norm,
                c_min_d_norm,
            ],
            dtype=np.float32,
        )

        for j, r in enumerate(responders):
            rx_norm = float(r.position.x_m) / width
            ry_norm = float(r.position.y_m) / height
            dx_norm = (float(c.position.x_m) - float(r.position.x_m)) / width
            dy_norm = (float(c.position.y_m) - float(r.position.y_m)) / height
            euclid = math.hypot(
                float(c.position.x_m) - float(r.position.x_m),
                float(c.position.y_m) - float(r.position.y_m),
            )
            euclid_norm = euclid / diag
            manhattan_norm = (
                abs(float(c.position.x_m) - float(r.position.x_m))
                + abs(float(c.position.y_m) - float(r.position.y_m))
            ) / manhattan_scale

            is_reach = reachable_mask[i, j]
            if is_reach:
                d_astar = float(astar_distances_m[i, j])
                astar_norm = min(d_astar / manhattan_scale, 1.5)
                detour_norm = min(d_astar / max(euclid, 0.25), 4.0) / 4.0
            else:
                d_astar = manhattan_scale * 1.5
                astar_norm = 1.5
                detour_norm = 1.0

            r_avail = 1.0 if r.status == "available" else 0.0
            r_busy = 1.0 if r.status == "busy" else 0.0
            r_off = 1.0 if r.status == "offline" else 0.0
            r_act = 1.0 if r.id in active_responders else 0.0
            compat = 1.0 if cap_compat_mask[i, j] else 0.0
            r_cap_cnt = float(len(r.capabilities)) / 3.0
            r_surplus = max(0.0, float(len(r.capabilities) - len(req_set))) / 3.0

            is_feas = 1.0 if feasible_mask[i, j] else 0.0
            r_deg_norm = float(res_deg[j]) / max(float(n_cas), 1.0)
            if feasible_mask[i, j]:
                c_gap = min(max(0.0, d_astar - float(cas_min_dist[i])) / manhattan_scale, 1.0)
                r_gap = min(max(0.0, d_astar - float(res_min_dist[j])) / manhattan_scale, 1.0)
            else:
                c_gap = 1.0
                r_gap = 1.0

            pair_features[i, j] = np.array(
                [
                    cx_norm,
                    cy_norm,
                    rx_norm,
                    ry_norm,
                    dx_norm,
                    dy_norm,
                    euclid_norm,
                    manhattan_norm,
                    grid_scale_norm,
                    1.0 if is_reach else 0.0,
                    astar_norm,
                    detour_norm,
                    prio_norm,
                    prio_2,
                    prio_1,
                    prio_0,
                    unassessed,
                    exp_or_dec,
                    r_avail,
                    r_busy,
                    r_off,
                    c_act,
                    r_act,
                    compat,
                    req_bls,
                    req_als,
                    req_med,
                    r_cap_cnt,
                    r_surplus,
                    meta["has_obs"],
                    meta["raw_conf"],
                    meta["decayed"],
                    meta["age_norm"],
                    meta["stale"],
                    meta["low_conf"],
                    meta["pos_mismatch"],
                    meta["track_not_vis"],
                    is_feas,
                    cas_count_norm,
                    res_count_norm,
                    avail_ratio,
                    c_deg_norm,
                    r_deg_norm,
                    c_gap,
                    r_gap,
                    cas_responder_rank[i, j],
                    res_casualty_rank[i, j],
                    res_higher_prio_frac[i, j],
                ],
                dtype=np.float32,
            )

    for j, r in enumerate(responders):
        rx_norm = float(r.position.x_m) / width
        ry_norm = float(r.position.y_m) / height
        r_avail = 1.0 if r.status == "available" else 0.0
        r_busy = 1.0 if r.status == "busy" else 0.0
        r_off = 1.0 if r.status == "offline" else 0.0
        r_act = 1.0 if r.id in active_responders else 0.0
        r_caps = set(r.capabilities)
        has_bls = 1.0 if "bls" in r_caps else 0.0
        has_als = 1.0 if "als" in r_caps else 0.0
        has_med = 1.0 if "medevac" in r_caps else 0.0
        r_cap_cnt = float(len(r_caps)) / 3.0
        r_deg_norm = float(res_deg[j]) / max(float(n_cas), 1.0)
        r_min_d_norm = min(float(res_min_dist[j]) / manhattan_scale, 1.5)

        responder_node_features[j] = np.array(
            [
                rx_norm,
                ry_norm,
                r_avail,
                r_busy,
                r_off,
                r_act,
                has_bls,
                has_als,
                has_med,
                r_cap_cnt,
                r_deg_norm,
                r_min_d_norm,
            ],
            dtype=np.float32,
        )

    return SceneFeatures(
        schema_version=FEATURE_SCHEMA_VERSION,
        casualty_ids=casualty_ids,
        responder_ids=responder_ids,
        pair_features=pair_features,
        casualty_node_features=casualty_node_features,
        responder_node_features=responder_node_features,
        feasible_mask=feasible_mask,
        astar_distances_m=astar_distances_m,
        priorities=priorities,
        candidate_routes=candidate_routes,
    )


def extract_features_from_bundle(bundle: dict) -> SceneFeatures:
    """Extract features from a scenario bundle using only its OptimizationRequest and routes."""
    return extract_features_from_request(
        bundle["request"],
        precomputed_all_routes=bundle.get("all_routes"),
    )
