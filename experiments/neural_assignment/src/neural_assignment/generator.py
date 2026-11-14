"""Reproducible synthetic scenario and OR-Tools teacher dataset generator.

Generates AEGIS scenarios using the authoritative backend/optimization schemas,
partitioned across disjoint seed ranges and distinct obstacle geometry families
to prevent train/validation/test leakage. Labels scenarios using the real
OR-Tools CP-SAT solver and records solver status, proven optimality, and
tie-equivalent optimal pairs.
"""

from __future__ import annotations

import hashlib
import json
import math
import random
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from time import perf_counter
from typing import Literal

from neural_assignment import GENERATOR_VERSION
from neural_assignment._rootpath import ensure_repo_root_on_path

ensure_repo_root_on_path()

from backend.app.models import (
    AStarRoute,
    Assignment,
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
from optimization.models import OptimizationRequest, OptimizationResult
from optimization.routing import _GridRouter
from optimization.solver import _observation_reasons, _priority, optimize

SplitName = Literal["train", "val", "test_id", "test_ood"]
GeometryFamily = Literal[
    "open_scatter",
    "central_wall_ns",
    "twin_rooms_ew",
    "diagonal_choke",
    "u_shaped_courtyard",
    "four_quadrant_cross",
]

FRAME_ID = "map"
RESOLUTION_M = 1.0
REFERENCE_NOW = datetime(2026, 11, 14, 12, 0, 0, tzinfo=timezone.utc)
CAPABILITY_POOL = ("bls", "als", "medevac")

TRAIN_GEOMETRY_FAMILIES: tuple[GeometryFamily, ...] = (
    "open_scatter",
    "central_wall_ns",
    "twin_rooms_ew",
)
VAL_GEOMETRY_FAMILIES: tuple[GeometryFamily, ...] = ("diagonal_choke",)
OOD_TEST_GEOMETRY_FAMILIES: tuple[GeometryFamily, ...] = (
    "u_shaped_courtyard",
    "four_quadrant_cross",
)

SPLIT_SEED_BASES: dict[SplitName, int] = {
    "train": 10_000,
    "val": 20_000,
    "test_id": 30_000,
    "test_ood": 40_000,
}

SPLIT_FAMILIES: dict[SplitName, tuple[GeometryFamily, ...]] = {
    "train": TRAIN_GEOMETRY_FAMILIES,
    "val": VAL_GEOMETRY_FAMILIES,
    "test_id": TRAIN_GEOMETRY_FAMILIES,
    "test_ood": OOD_TEST_GEOMETRY_FAMILIES,
}


@dataclass(frozen=True)
class GeneratorConfig:
    version: str = GENERATOR_VERSION
    train_count: int = 5000
    val_count: int = 600
    test_id_count: int = 600
    test_ood_count: int = 600
    min_casualties: int = 2
    max_casualties: int = 6
    min_responders: int = 1
    max_responders: int = 5
    require_proven_optimal: bool = True

    def to_dict(self) -> dict:
        return {
            "version": self.version,
            "train_count": self.train_count,
            "val_count": self.val_count,
            "test_id_count": self.test_id_count,
            "test_ood_count": self.test_ood_count,
            "min_casualties": self.min_casualties,
            "max_casualties": self.max_casualties,
            "min_responders": self.min_responders,
            "max_responders": self.max_responders,
            "require_proven_optimal": self.require_proven_optimal,
            "split_seed_bases": dict(SPLIT_SEED_BASES),
            "split_families": {k: list(v) for k, v in SPLIT_FAMILIES.items()},
        }


def _rect_obstacle(obs_id: str, x0: float, y0: float, x1: float, y1: float) -> Obstacle:
    return Obstacle(
        id=obs_id,
        vertices=[
            Position(frame_id=FRAME_ID, x_m=float(x0), y_m=float(y0)),
            Position(frame_id=FRAME_ID, x_m=float(x1), y_m=float(y0)),
            Position(frame_id=FRAME_ID, x_m=float(x1), y_m=float(y1)),
            Position(frame_id=FRAME_ID, x_m=float(x0), y_m=float(y1)),
        ],
    )


def _build_geometry(
    family: GeometryFamily, rng: random.Random, scenario_seed: int
) -> tuple[SceneMap, int, int]:
    """Construct a SceneMap from one of the 6 geometry families."""
    if family == "open_scatter":
        width, height = 12, 12
        cells = [0] * (width * height)
        # 1 or 2 scattered interior rectangular pillars
        px1 = rng.randint(2, 4)
        py1 = rng.randint(2, 8)
        obstacles = [_rect_obstacle("pillar-a", px1, py1, px1 + 2, py1 + 2)]
        if rng.random() < 0.65:
            px2 = rng.randint(7, 9)
            py2 = rng.randint(2, 8)
            obstacles.append(_rect_obstacle("pillar-b", px2, py2, px2 + 2, py2 + 2))
    elif family == "central_wall_ns":
        width, height = 12, 12
        cells = [0] * (width * height)
        wall_x = rng.choice([5, 6])
        gap_mode = rng.choice(["north", "center", "south"])
        if gap_mode == "north":
            obstacles = [_rect_obstacle("wall-ns", wall_x, 3, wall_x + 1, 11)]
        elif gap_mode == "south":
            obstacles = [_rect_obstacle("wall-ns", wall_x, 1, wall_x + 1, 9)]
        else:
            obstacles = [
                _rect_obstacle("wall-ns-bot", wall_x, 1, wall_x + 1, 4),
                _rect_obstacle("wall-ns-top", wall_x, 8, wall_x + 1, 11),
            ]
    elif family == "twin_rooms_ew":
        width, height = 12, 12
        cells = [0] * (width * height)
        wall_y = rng.choice([5, 6])
        gap_x = rng.choice([2, 5, 8])
        obstacles = []
        if gap_x > 1:
            obstacles.append(_rect_obstacle("wall-ew-left", 1, wall_y, gap_x, wall_y + 1))
        if gap_x + 3 < 11:
            obstacles.append(
                _rect_obstacle("wall-ew-right", gap_x + 3, wall_y, 11, wall_y + 1)
            )
        if not obstacles:
            obstacles.append(_rect_obstacle("wall-ew-mid", 3, wall_y, 8, wall_y + 1))
    elif family == "diagonal_choke":
        width, height = 14, 14
        cells = [0] * (width * height)
        shift = rng.choice([0, 1])
        obstacles = [
            _rect_obstacle("diag-lower", 3 + shift, 1, 5 + shift, 8),
            _rect_obstacle("diag-upper", 8 + shift, 6, 10 + shift, 13),
        ]
        # Mark two corner cells as unknown (-1) to test strict unknown-cell avoidance
        cells[0] = -1
        cells[width * height - 1] = -1
    elif family == "u_shaped_courtyard":
        width, height = 14, 14
        cells = [0] * (width * height)
        orient = rng.choice(["open_north", "open_south", "open_east"])
        if orient == "open_north":
            obstacles = [
                _rect_obstacle("u-base", 4, 3, 10, 4),
                _rect_obstacle("u-left", 3, 3, 4, 10),
                _rect_obstacle("u-right", 10, 3, 11, 10),
            ]
        elif orient == "open_south":
            obstacles = [
                _rect_obstacle("u-top", 4, 10, 10, 11),
                _rect_obstacle("u-left", 3, 4, 4, 11),
                _rect_obstacle("u-right", 10, 4, 11, 11),
            ]
        else:
            obstacles = [
                _rect_obstacle("u-spine", 3, 4, 4, 10),
                _rect_obstacle("u-bot", 4, 3, 10, 4),
                _rect_obstacle("u-top", 4, 10, 10, 11),
            ]
    elif family == "four_quadrant_cross":
        width, height = 16, 16
        cells = [0] * (width * height)
        obstacles = [
            _rect_obstacle("cross-n", 7, 10, 9, 14),
            _rect_obstacle("cross-s", 7, 2, 9, 6),
            _rect_obstacle("cross-w", 2, 7, 6, 9),
            _rect_obstacle("cross-e", 10, 7, 14, 9),
        ]
    else:
        raise ValueError(f"Unsupported geometry family: {family}")

    scene_map = SceneMap(
        frame_id=FRAME_ID,
        revision=f"rev-{family}-{scenario_seed}",
        obstacles=obstacles,
        occupancy_grid=OccupancyGrid(
            origin=Position(frame_id=FRAME_ID, x_m=0.0, y_m=0.0),
            resolution_m=RESOLUTION_M,
            width=width,
            height=height,
            cells=cells,
        ),
    )
    return scene_map, width, height


def _find_all_optimal_edges(
    routes: dict[tuple[str, str], AStarRoute],
    priorities: dict[str, int],
    target_count: int,
    target_points: int,
    target_travel_mm: int,
) -> tuple[set[tuple[str, str]], int]:
    """Find every feasible pair that participates in at least one optimal matching.

    Because OR-Tools breaks ties at stage 4 using arbitrary entity IDs, two matchings
    with identical (assigned_count, priority_points, travel_cost_mm) are equally
    optimal under the domain objective. This exact search identifies all edges that
    belong to any matching achieving (target_count, target_points, target_travel_mm).
    """
    if target_count == 0 or not routes:
        return set(), 1

    by_casualty: dict[str, list[tuple[str, int]]] = {}
    for (cid, rid), route in routes.items():
        by_casualty.setdefault(cid, []).append((rid, round(route.distance_m * 1000)))
    casualties = sorted(by_casualty.keys(), key=lambda c: (-priorities[c], c))
    n_cas = len(casualties)

    # Suffix upper bounds for fast pruning
    suffix_points = [0] * (n_cas + 1)
    for idx in range(n_cas - 1, -1, -1):
        suffix_points[idx] = suffix_points[idx + 1] + priorities[casualties[idx]]

    optimal_edges: set[tuple[str, str]] = set()
    optimal_matching_count = 0
    used_responders: set[str] = set()
    current_pairs: list[tuple[str, str]] = []

    def dfs(idx: int, count_so_far: int, points_so_far: int, travel_so_far: int) -> None:
        nonlocal optimal_matching_count
        remaining = n_cas - idx
        if count_so_far + remaining < target_count:
            return
        if count_so_far > target_count:
            return
        if points_so_far + suffix_points[idx] < target_points:
            return
        if travel_so_far > target_travel_mm:
            return
        if idx == n_cas:
            if (
                count_so_far == target_count
                and points_so_far == target_points
                and travel_so_far == target_travel_mm
            ):
                optimal_matching_count += 1
                optimal_edges.update(current_pairs)
            return

        cid = casualties[idx]
        c_prio = priorities[cid]
        # Branch 1: assign cid to one of its feasible unused responders
        if count_so_far < target_count:
            for rid, cost_mm in by_casualty[cid]:
                if rid in used_responders:
                    continue
                if travel_so_far + cost_mm > target_travel_mm:
                    continue
                used_responders.add(rid)
                current_pairs.append((cid, rid))
                dfs(idx + 1, count_so_far + 1, points_so_far + c_prio, travel_so_far + cost_mm)
                current_pairs.pop()
                used_responders.remove(rid)

        # Branch 2: leave cid unassigned
        dfs(idx + 1, count_so_far, points_so_far, travel_so_far)

    dfs(0, 0, 0, 0)
    return optimal_edges, optimal_matching_count


def generate_scenario_bundle(
    scenario_seed: int,
    split: SplitName = "train",
    family: GeometryFamily | None = None,
    config: GeneratorConfig | None = None,
) -> dict:
    """Deterministically generate one synthetic scenario bundle with OR-Tools teacher labels."""
    cfg = config or GeneratorConfig()
    rng = random.Random(scenario_seed)
    allowed_families = SPLIT_FAMILIES[split]
    if family is None:
        family = allowed_families[scenario_seed % len(allowed_families)]
    elif family not in allowed_families:
        raise ValueError(
            f"Geometry family {family!r} is not permitted in split {split!r} "
            f"(allowed: {allowed_families})"
        )

    scene_map, width, height = _build_geometry(family, rng, scenario_seed)
    router = _GridRouter(scene_map)
    free_cells = [
        (cx, cy)
        for cx in range(width)
        for cy in range(height)
        if not router._is_blocked((cx, cy))
    ]

    n_casualties = rng.randint(cfg.min_casualties, cfg.max_casualties)
    n_responders = rng.randint(cfg.min_responders, cfg.max_responders)

    # Optionally seal one free cell with obstacle walls (~12% of scenarios) to create
    # an unreachable pocket for a casualty while keeping the cell itself free
    sealed_cell: tuple[int, int] | None = None
    if rng.random() < 0.12 and len(free_cells) > n_casualties + n_responders + 8:
        interior_candidates = [
            (cx, cy)
            for (cx, cy) in free_cells
            if 1 <= cx < width - 1 and 1 <= cy < height - 1
        ]
        if interior_candidates:
            sx, sy = rng.choice(interior_candidates)
            neighbors = {(sx - 1, sy), (sx + 1, sy), (sx, sy - 1), (sx, sy + 1)}
            grid = scene_map.occupancy_grid
            for nx, ny in neighbors:
                grid.cells[ny * width + nx] = 100
            router = _GridRouter(scene_map)
            free_cells = [
                (cx, cy)
                for cx in range(width)
                for cy in range(height)
                if not router._is_blocked((cx, cy))
            ]
            if (sx, sy) in free_cells:
                sealed_cell = (sx, sy)

    sampled_cells = rng.sample(free_cells, n_casualties + n_responders)
    if sealed_cell is not None and sealed_cell not in sampled_cells[:n_casualties]:
        sampled_cells[0] = sealed_cell

    cas_cells = sampled_cells[:n_casualties]
    res_cells = sampled_cells[n_casualties:]

    now = REFERENCE_NOW
    severity_choices: tuple[str | None, ...] = (
        "immediate",
        "immediate",
        "delayed",
        "delayed",
        "minor",
        "expectant",
        "deceased",
        None,
    )
    req_cap_choices: tuple[tuple[str, ...], ...] = (
        (),
        (),
        ("bls",),
        ("als",),
        ("als", "bls"),
        ("medevac",),
    )
    res_cap_choices: tuple[tuple[str, ...], ...] = (
        ("bls",),
        ("als",),
        ("als", "bls"),
        ("als", "bls"),
        ("als", "bls", "medevac"),
    )

    casualties: list[Casualty] = []
    required_capabilities: dict[str, list[str]] = {}
    observations: dict[str, CVObservation] = {}

    for idx, (cx, cy) in enumerate(cas_cells):
        cid = f"c{idx}"
        sev = rng.choice(severity_choices)
        assessment = None
        if sev is not None:
            assessment = MedicalAssessment(
                severity=sev,
                source="human",
                assessed_by="triage-lead",
                assessed_at=now - timedelta(seconds=rng.randint(15, 180)),
            )
        pos = Position(frame_id=FRAME_ID, x_m=float(cx) + 0.5, y_m=float(cy) + 0.5)
        casualties.append(Casualty(id=cid, position=pos, medical_assessment=assessment))
        req = list(rng.choice(req_cap_choices))
        if req:
            required_capabilities[cid] = req

        # Observation profiles:
        # 0..64%: fresh visible high-confidence
        # 65..77%: missing observation (human_known position still eligible)
        # 77..84%: stale observation (> 30s)
        # 84..90%: low confidence (< 0.5)
        # 90..96%: occluded or lost track
        # 96..100%: position mismatch (> 0.25m)
        roll = rng.random()
        if roll < 0.65:
            age_s = round(rng.uniform(0.5, 24.0), 2)
            conf = round(rng.uniform(0.58, 0.99), 3)
            observations[cid] = CVObservation(
                id=f"obs-{cid}",
                source="cv",
                observed_at=now - timedelta(seconds=age_s),
                position=pos,
                kind="person_detected",
                confidence=conf,
                track=CVTrack(
                    track_id=f"trk-{cid}",
                    state="visible",
                    first_seen_at=now - timedelta(seconds=age_s + 10.0),
                    last_seen_at=now - timedelta(seconds=age_s),
                ),
            )
        elif roll < 0.77:
            # Missing CV observation -> relies on authoritative human-known position
            pass
        elif roll < 0.84:
            age_s = round(rng.uniform(32.0, 75.0), 2)
            conf = round(rng.uniform(0.65, 0.95), 3)
            observations[cid] = CVObservation(
                id=f"obs-{cid}",
                source="cv",
                observed_at=now - timedelta(seconds=age_s),
                position=pos,
                kind="person_detected",
                confidence=conf,
                track=CVTrack(
                    track_id=f"trk-{cid}",
                    state="visible",
                    first_seen_at=now - timedelta(seconds=age_s + 15.0),
                    last_seen_at=now - timedelta(seconds=age_s),
                ),
            )
        elif roll < 0.90:
            age_s = round(rng.uniform(2.0, 20.0), 2)
            conf = round(rng.uniform(0.15, 0.46), 3)
            observations[cid] = CVObservation(
                id=f"obs-{cid}",
                source="cv",
                observed_at=now - timedelta(seconds=age_s),
                position=pos,
                kind="person_detected",
                confidence=conf,
                track=CVTrack(
                    track_id=f"trk-{cid}",
                    state="visible",
                    first_seen_at=now - timedelta(seconds=age_s + 8.0),
                    last_seen_at=now - timedelta(seconds=age_s),
                ),
            )
        elif roll < 0.96:
            age_s = round(rng.uniform(2.0, 20.0), 2)
            conf = round(rng.uniform(0.60, 0.92), 3)
            state = rng.choice(["occluded", "lost"])
            observations[cid] = CVObservation(
                id=f"obs-{cid}",
                source="cv",
                observed_at=now - timedelta(seconds=age_s),
                position=pos,
                kind="person_detected",
                confidence=conf,
                track=CVTrack(
                    track_id=f"trk-{cid}",
                    state=state,
                    first_seen_at=now - timedelta(seconds=age_s + 12.0),
                    last_seen_at=now - timedelta(seconds=age_s),
                ),
            )
        else:
            age_s = round(rng.uniform(2.0, 18.0), 2)
            conf = round(rng.uniform(0.70, 0.96), 3)
            offset_pos = Position(
                frame_id=FRAME_ID,
                x_m=min(float(width) - 0.1, pos.x_m + 0.65),
                y_m=pos.y_m,
            )
            observations[cid] = CVObservation(
                id=f"obs-{cid}",
                source="cv",
                observed_at=now - timedelta(seconds=age_s),
                position=offset_pos,
                kind="person_detected",
                confidence=conf,
                track=CVTrack(
                    track_id=f"trk-{cid}",
                    state="visible",
                    first_seen_at=now - timedelta(seconds=age_s + 8.0),
                    last_seen_at=now - timedelta(seconds=age_s),
                ),
            )

    responders: list[Responder] = []
    for idx, (rx, ry) in enumerate(res_cells):
        rid = f"r{idx}"
        # Keep first responder usually available, vary others across available/busy/offline
        if idx == 0 and rng.random() < 0.85:
            status = "available"
        else:
            status = rng.choices(
                ["available", "busy", "offline"], weights=[0.74, 0.16, 0.10], k=1
            )[0]
        caps = list(rng.choice(res_cap_choices))
        responders.append(
            Responder(
                id=rid,
                display_name=f"Unit {idx}",
                position=Position(
                    frame_id=FRAME_ID, x_m=float(rx) + 0.5, y_m=float(ry) + 0.5
                ),
                status=status,
                capabilities=caps,
            )
        )

    scenario = Scenario(
        schema_version="1.0",
        id=f"neural-{split}-{scenario_seed}",
        name=f"Neural synth {split} {scenario_seed} ({family})",
        synthetic=True,
        frame_id=FRAME_ID,
        created_at=now,
        casualties=casualties,
        responders=responders,
        scene_map=scene_map,
    )

    # Optionally include 1 active assignment reservation (~14% of scenarios with >=2 responders)
    active_assignments: list[Assignment] = []
    if n_casualties >= 3 and n_responders >= 2 and rng.random() < 0.14:
        c0 = casualties[-1]
        r0 = responders[-1]
        active_route = router.plan(r0.position, c0.position)
        if active_route is not None:
            active_assignments.append(
                Assignment(casualty_id=c0.id, responder_id=r0.id, route=active_route)
            )

    request = OptimizationRequest(
        scenario=scenario,
        required_capabilities=required_capabilities,
        active_assignments=active_assignments,
        observations=observations,
        now=now,
        solver="ortools",
    )

    teacher_result: OptimizationResult = optimize(request)

    # Reconstruct exact candidate routes & priorities for label analysis
    active_casualties = {a.casualty_id for a in active_assignments}
    active_responders = {a.responder_id for a in active_assignments}
    priorities = {c.id: _priority(c, request) for c in casualties}
    candidate_routes: dict[tuple[str, str], AStarRoute] = {}
    all_routes: dict[tuple[str, str], AStarRoute | None] = {}

    for c in casualties:
        obs_reasons = _observation_reasons(c, request)
        c_blocked = (c.id in active_casualties) or bool(obs_reasons)
        req_set = set(required_capabilities.get(c.id, []))
        for r in responders:
            route = router.plan(r.position, c.position)
            all_routes[(c.id, r.id)] = route
            if c_blocked:
                continue
            if r.id in active_responders or r.status != "available":
                continue
            if not req_set.issubset(r.capabilities):
                continue
            if route is not None:
                candidate_routes[(c.id, r.id)] = route

    tiers = teacher_result.objective_tiers
    proven_optimal = (
        teacher_result.engine == "ortools"
        and bool(tiers)
        and all(t.proven_optimal for t in tiers)
    )
    teacher_pairs = {(a.casualty_id, a.responder_id) for a in teacher_result.assignments}
    target_count = int(round(teacher_result.objective_values["new_assigned_count"]))
    target_points = int(round(teacher_result.objective_values["allocation_priority_points"]))
    target_travel_mm = int(round(teacher_result.objective_values["new_travel_cost_mm"]))

    any_optimal_pairs, optimal_matching_count = _find_all_optimal_edges(
        candidate_routes,
        priorities,
        target_count,
        target_points,
        target_travel_mm,
    )
    if not any_optimal_pairs and teacher_pairs:
        any_optimal_pairs = set(teacher_pairs)
        optimal_matching_count = 1
    tie_alternative_pairs = any_optimal_pairs - teacher_pairs

    return {
        "generator_version": cfg.version,
        "scenario_seed": scenario_seed,
        "split": split,
        "geometry_family": family,
        "grid_width": width,
        "grid_height": height,
        "scenario": scenario,
        "required_capabilities": required_capabilities,
        "active_assignments": active_assignments,
        "observations": observations,
        "now": now,
        "request": request,
        "teacher_result": teacher_result,
        "teacher_engine": teacher_result.engine,
        "teacher_status": teacher_result.metadata.status,
        "proven_optimal": proven_optimal,
        "teacher_pairs": sorted(teacher_pairs),
        "any_optimal_pairs": sorted(any_optimal_pairs),
        "tie_alternative_pairs": sorted(tie_alternative_pairs),
        "optimal_matching_count": optimal_matching_count,
        "feasible_pairs": sorted(candidate_routes.keys()),
        "candidate_routes": candidate_routes,
        "all_routes": all_routes,
        "priorities": priorities,
        "objective_values": dict(teacher_result.objective_values),
    }


def generate_split_bundles(
    split: SplitName,
    count: int | None = None,
    config: GeneratorConfig | None = None,
) -> tuple[list[dict], dict]:
    """Generate a complete split of validated scenarios and return (bundles, stats)."""
    cfg = config or GeneratorConfig()
    if count is None:
        count = {
            "train": cfg.train_count,
            "val": cfg.val_count,
            "test_id": cfg.test_id_count,
            "test_ood": cfg.test_ood_count,
        }[split]

    started = perf_counter()
    base_seed = SPLIT_SEED_BASES[split]
    bundles: list[dict] = []
    excluded_non_optimal = 0
    status_counts: dict[str, int] = {"feasible": 0, "partial": 0, "infeasible": 0}
    family_counts: dict[str, int] = {}
    tie_scenarios = 0
    total_pairs = 0
    total_feasible_pairs = 0
    total_positive_pairs = 0
    total_tie_alt_pairs = 0

    offset = 0
    while len(bundles) < count:
        seed = base_seed + offset
        offset += 1
        bundle = generate_scenario_bundle(seed, split=split, config=cfg)
        if cfg.require_proven_optimal and not bundle["proven_optimal"]:
            excluded_non_optimal += 1
            continue
        bundles.append(bundle)
        st = bundle["teacher_status"]
        status_counts[st] = status_counts.get(st, 0) + 1
        fam = bundle["geometry_family"]
        family_counts[fam] = family_counts.get(fam, 0) + 1
        if bundle["optimal_matching_count"] > 1:
            tie_scenarios += 1
        n_c = len(bundle["scenario"].casualties)
        n_r = len(bundle["scenario"].responders)
        total_pairs += n_c * n_r
        total_feasible_pairs += len(bundle["feasible_pairs"])
        total_positive_pairs += len(bundle["teacher_pairs"])
        total_tie_alt_pairs += len(bundle["tie_alternative_pairs"])

    elapsed_s = perf_counter() - started
    seed_digest = hashlib.sha256(
        json.dumps([b["scenario_seed"] for b in bundles]).encode()
    ).hexdigest()

    stats = {
        "split": split,
        "generator_version": cfg.version,
        "count": len(bundles),
        "attempted_seeds": offset,
        "excluded_non_optimal": excluded_non_optimal,
        "seed_range": [bundles[0]["scenario_seed"], bundles[-1]["scenario_seed"]]
        if bundles
        else [],
        "seed_list_sha256": seed_digest,
        "geometry_families": family_counts,
        "solver_status_counts": status_counts,
        "tie_scenario_count": tie_scenarios,
        "total_candidate_pairs": total_pairs,
        "total_feasible_pairs": total_feasible_pairs,
        "total_teacher_positive_pairs": total_positive_pairs,
        "total_tie_alternative_pairs": total_tie_alt_pairs,
        "positive_pair_fraction_all": total_positive_pairs / max(total_pairs, 1),
        "positive_pair_fraction_feasible": total_positive_pairs
        / max(total_feasible_pairs, 1),
        "generation_Seconds": round(elapsed_s, 3),
    }
    return bundles, stats


def verify_no_split_leakage(splits: dict[str, list[dict]]) -> dict:
    """Assert zero seed, scenario-ID, map-revision, or held-out family leakage across splits."""
    seen_seeds: dict[int, str] = {}
    seen_ids: dict[str, str] = {}
    seen_revisions: dict[str, str] = {}
    families_by_split: dict[str, set[str]] = {}

    for split_name, bundles in splits.items():
        fam_set: set[str] = set()
        for b in bundles:
            seed = b["scenario_seed"]
            sid = b["scenario"].id
            rev = b["scenario"].scene_map.revision
            fam = b["geometry_family"]
            if seed in seen_seeds:
                raise AssertionError(
                    f"Seed leakage detected: seed {seed} in {split_name} and {seen_seeds[seed]}"
                )
            if sid in seen_ids:
                raise AssertionError(
                    f"Scenario ID leakage: {sid} in {split_name} and {seen_ids[sid]}"
                )
            if rev in seen_revisions:
                raise AssertionError(
                    f"Map revision leakage: {rev} in {split_name} and {seen_revisions[rev]}"
                )
            seen_seeds[seed] = split_name
            seen_ids[sid] = split_name
            seen_revisions[rev] = split_name
            fam_set.add(fam)
        families_by_split[split_name] = fam_set

    train_fams = families_by_split.get("train", set())
    val_fams = families_by_split.get("val", set())
    ood_fams = families_by_split.get("test_ood", set())

    if train_fams & val_fams:
        raise AssertionError(f"Validation geometry families leaked into train: {train_fams & val_fams}")
    if train_fams & ood_fams:
        raise AssertionError(f"OOD test geometry families leaked into train: {train_fams & ood_fams}")
    if val_fams & ood_fams:
        raise AssertionError(f"OOD test geometry families leaked into val: {val_fams & ood_fams}")

    return {
        "leakage_free": True,
        "total_unique_seeds": len(seen_seeds),
        "families_by_split": {k: sorted(v) for k, v in families_by_split.items()},
    }
