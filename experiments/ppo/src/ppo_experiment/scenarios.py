"""Deterministic synthetic scenario bundles with fixed train and eval seeds.

Every bundle carries the same core gates for every contender: required
capabilities, responder availability, A* grid routing on a shared scene
map, human assessment priority, and fresh visible tracking observations.
Baselines and the PPO env consume identical Scenario payloads.
"""

from __future__ import annotations

import random
from datetime import datetime, timedelta, timezone

from ppo_experiment import _rootpath as _rp

_rp.ensure_parent_on_path()

from backend.app.models import (  # noqa: E402
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
from optimization.models import OptimizationRequest  # noqa: E402

FRAME_ID = "map"
GRID_W = 10
GRID_H = 10
RESOLUTION_M = 1.0
MAX_CASUALTIES = 4
MAX_RESPONDERS = 3
CAPABILITIES = ("als", "bls")

TRAIN_SEEDS = (101, 102)
EVAL_SEEDS = (901, 902, 903, 904)

NOW = datetime(2026, 1, 15, 12, 0, 0, tzinfo=timezone.utc)

OBSTACLE_VERTICES = ((4.0, 4.0), (6.0, 4.0), (6.0, 6.0), (4.0, 6.0))


def obstacle_cells() -> set[tuple[int, int]]:
    cells: set[tuple[int, int]] = set()
    for cx in range(GRID_W):
        for cy in range(GRID_H):
            touches_x = cx <= 6 and cx + 1 >= 4
            touches_y = cy <= 6 and cy + 1 >= 4
            if touches_x and touches_y:
                cells.add((cx, cy))
    return cells


def priority_points(severity: str | None, assessed_at, now) -> int:
    if severity is None:
        return 2
    if assessed_at is not None and assessed_at > now:
        return 2
    table = {"immediate": 2, "delayed": 1, "minor": 0, "expectant": 2, "deceased": 2}
    return table[severity]


def build_bundle(scenario_seed: int) -> dict:
    rng = random.Random(scenario_seed)
    now = NOW
    blocked = obstacle_cells()
    free = [(x, y) for x in range(GRID_W) for y in range(GRID_H) if (x, y) not in blocked]
    picks = rng.sample(free, MAX_CASUALTIES + MAX_RESPONDERS)
    cas_cells = picks[:MAX_CASUALTIES]
    res_cells = picks[MAX_CASUALTIES:]
    casualties: list[Casualty] = []
    severities: list[str | None] = ["immediate", "delayed", "minor", None]
    for index, (cx, cy) in enumerate(cas_cells):
        severity = severities[index % len(severities)]
        assessment = None
        if severity is not None:
            assessment = MedicalAssessment(
                severity=severity,
                source="human",
                assessed_by="dr-synth",
                assessed_at=now - timedelta(seconds=60),
            )
        casualties.append(
            Casualty(
                id=f"c{index}",
                position=Position(frame_id=FRAME_ID, x_m=float(cx) + 0.5, y_m=float(cy) + 0.5),
                medical_assessment=assessment,
            )
        )
    responders: list[Responder] = []
    statuses: list[str] = ["available", "available", "available"]
    extra = rng.choice(["busy", "offline", "available"])
    statuses[2] = extra
    caps = [["als", "bls"], ["bls"], ["als"]]
    for index, (cx, cy) in enumerate(res_cells):
        responders.append(
            Responder(
                id=f"r{index}",
                display_name=f"Unit {index}",
                position=Position(frame_id=FRAME_ID, x_m=float(cx) + 0.5, y_m=float(cy) + 0.5),
                status=statuses[index % len(statuses)],
                capabilities=list(caps[index % len(caps)]),
            )
        )
    required: dict[str, list[str]] = {}
    for index, casualty in enumerate(casualties):
        if index % 2 == 0:
            required[casualty.id] = []
        else:
            required[casualty.id] = [rng.choice(list(CAPABILITIES))]
    observations: dict[str, CVObservation] = {}
    for casualty in casualties:
        observations[casualty.id] = CVObservation(
            id=f"obs-{casualty.id}",
            source="cv",
            observed_at=now,
            position=Position(
                frame_id=FRAME_ID, x_m=casualty.position.x_m, y_m=casualty.position.y_m
            ),
            kind="person_detected",
            confidence=0.95,
            track=CVTrack(
                track_id=f"trk-{casualty.id}",
                state="visible",
                first_seen_at=now - timedelta(seconds=5),
                last_seen_at=now,
            ),
        )
    vertices = [Position(frame_id=FRAME_ID, x_m=x, y_m=y) for x, y in OBSTACLE_VERTICES]
    scene_map = SceneMap(
        frame_id=FRAME_ID,
        revision=f"rev-{scenario_seed}",
        obstacles=[Obstacle(id="block-a", vertices=vertices)],
        occupancy_grid=OccupancyGrid(
            origin=Position(frame_id=FRAME_ID, x_m=0.0, y_m=0.0),
            resolution_m=RESOLUTION_M,
            width=GRID_W,
            height=GRID_H,
            cells=[0] * (GRID_W * GRID_H),
        ),
    )
    scenario = Scenario(
        schema_version="1.0",
        id=f"synth-{scenario_seed}",
        name=f"synthetic allocation {scenario_seed}",
        synthetic=True,
        frame_id=FRAME_ID,
        created_at=now,
        casualties=casualties,
        responders=responders,
        scene_map=scene_map,
    )
    return {
        "scenario_seed": scenario_seed,
        "scenario": scenario,
        "required_capabilities": required,
        "observations": observations,
        "now": now,
    }


def make_request(bundle: dict, solver: str) -> OptimizationRequest:
    return OptimizationRequest(
        scenario=bundle["scenario"],
        required_capabilities=dict(bundle["required_capabilities"]),
        observations=dict(bundle["observations"]),
        now=bundle["now"],
        solver=solver,
    )
