"""Interactive multi-step synthetic allocation environment.

One step proposes one casualty/responder pair or a stop action. The
environment enforces the same hard feasibility gates the core solver
uses: responder availability, capability compatibility, core
observation and tracking reasons, verified A* routes on the shared
scene map, plus one-to-one assignment. An infeasible or repeated
proposal is blocked before commitment and counted as an invalid
attempt. Committed safety violations are counted separately and must
remain zero. Filtering an invalid proposal is not learned safety.

Observation layout is a fixed unit-range vector: per-casualty
position, allocation priority, and assigned flag; per-responder
position, availability flag, and assigned flag; the full pair
feasibility mask with normalized A* route costs on the exact graph
the solver sees; plus the elapsed step fraction. Reward grants
allocation priority points minus scaled travel for each committed
assignment, a small penalty per blocked attempt, and nothing else.
Termination ends an episode when no feasible proposal remains or the
stop action is taken. Truncation ends an episode at the step budget.
They are reported separately.

One instance may cycle a deterministic pool of training scenario
seeds across episodes while heldout seeds stay disjoint and fixed.
"""

from __future__ import annotations

import gymnasium as gym
import numpy as np
from gymnasium import spaces

from ppo_experiment import _rootpath as _rp

_rp.ensure_parent_on_path()

from optimization.routing import _GridRouter
from optimization.solver import _observation_reasons
from ppo_experiment.scenarios import (
    GRID_H,
    GRID_W,
    MAX_CASUALTIES,
    MAX_RESPONDERS,
    build_bundle,
    make_request,
    priority_points,
)

STOP_ACTION = MAX_CASUALTIES * MAX_RESPONDERS
N_ACTIONS = STOP_ACTION + 1
PAIR_COUNT = MAX_CASUALTIES * MAX_RESPONDERS
OBS_DIM = MAX_CASUALTIES * 4 + MAX_RESPONDERS * 4 + PAIR_COUNT * 2 + 1
DEFAULT_MAX_STEPS = MAX_CASUALTIES * MAX_RESPONDERS + 1
INVALID_PENALTY = 0.1
TRAVEL_SCALE = 20.0


class AllocationEnv(gym.Env):
    metadata = {"render_modes": []}

    def __init__(
        self,
        scenario_seed: int = 101,
        train_seeds: tuple[int, ...] | None = None,
        max_steps: int = DEFAULT_MAX_STEPS,
    ):
        super().__init__()
        self.scenario_seed = scenario_seed
        self.scenario_pool = tuple(train_seeds) if train_seeds else (scenario_seed,)
        self.max_steps = max_steps
        self.action_space = spaces.Discrete(N_ACTIONS)
        self.observation_space = spaces.Box(low=0.0, high=1.0, shape=(OBS_DIM,), dtype=np.float32)
        self.episode_index = 0
        self.pair_distance: dict[tuple[int, int], float] = {}
        self.pair_feasible: dict[tuple[int, int], bool] = {}
        self.pair_points: dict[tuple[int, int], int] = {}
        self.bundle = {}
        self.router = None
        self._load_scenario(self.scenario_pool[0])
        self.assigned_casualties: set[int] = set()
        self.assigned_responders: set[int] = set()
        self.committed: list[tuple[int, int]] = []
        self.invalid_attempts = 0
        self.committed_violations = 0
        self.step_count = 0

    def _load_scenario(self, seed: int) -> None:
        self.bundle = build_bundle(seed)
        self.scenario_seed = seed
        self.router = _GridRouter(self.bundle["scenario"].scene_map)
        self._prepare_pairs()

    def _prepare_pairs(self) -> None:
        scenario = self.bundle["scenario"]
        request = make_request(self.bundle, "ortools")
        now = self.bundle["now"]
        self.pair_distance = {}
        self.pair_feasible = {}
        self.pair_points = {}
        for ci, casualty in enumerate(scenario.casualties):
            severity = None
            assessed_at = None
            if casualty.medical_assessment is not None:
                severity = casualty.medical_assessment.severity
                assessed_at = casualty.medical_assessment.assessed_at
            points = priority_points(severity, assessed_at, now)
            blocked = _observation_reasons(casualty, request)
            for ri, responder in enumerate(scenario.responders):
                key = (ci, ri)
                self.pair_points[key] = points
                if blocked:
                    self.pair_feasible[key] = False
                    continue
                if responder.status != "available":
                    self.pair_feasible[key] = False
                    continue
                need = set(request.required_capabilities.get(casualty.id, []))
                if not need.issubset(set(responder.capabilities)):
                    self.pair_feasible[key] = False
                    continue
                route = self.router.plan(responder.position, casualty.position)
                if route is None:
                    self.pair_feasible[key] = False
                    continue
                self.pair_feasible[key] = True
                self.pair_distance[key] = route.distance_m

    def _observation(self) -> np.ndarray:
        scenario = self.bundle["scenario"]
        now = self.bundle["now"]
        vec: list[float] = []
        for ci in range(MAX_CASUALTIES):
            if ci < len(scenario.casualties):
                casualty = scenario.casualties[ci]
                vec.append(casualty.position.x_m / GRID_W)
                vec.append(casualty.position.y_m / GRID_H)
                severity = None
                assessed_at = None
                if casualty.medical_assessment is not None:
                    severity = casualty.medical_assessment.severity
                    assessed_at = casualty.medical_assessment.assessed_at
                vec.append(priority_points(severity, assessed_at, now) / 2.0)
                vec.append(1.0 if ci in self.assigned_casualties else 0.0)
            else:
                vec.extend([0.0, 0.0, 0.0, 1.0])
        for ri in range(MAX_RESPONDERS):
            if ri < len(scenario.responders):
                responder = scenario.responders[ri]
                vec.append(responder.position.x_m / GRID_W)
                vec.append(responder.position.y_m / GRID_H)
                vec.append(1.0 if responder.status == "available" else 0.0)
                vec.append(1.0 if ri in self.assigned_responders else 0.0)
            else:
                vec.extend([0.0, 0.0, 0.0, 1.0])
        for ci in range(MAX_CASUALTIES):
            for ri in range(MAX_RESPONDERS):
                feasible = self.pair_feasible.get((ci, ri), False)
                vec.append(1.0 if feasible else 0.0)
        for ci in range(MAX_CASUALTIES):
            for ri in range(MAX_RESPONDERS):
                if self.pair_feasible.get((ci, ri), False):
                    vec.append(self.pair_distance[(ci, ri)] / TRAVEL_SCALE)
                else:
                    vec.append(0.0)
        vec.append(self.step_count / max(1, self.max_steps))
        return np.asarray(vec, dtype=np.float32)

    def reset(self, *, seed=None, options=None):
        super().reset(seed=seed)
        if len(self.scenario_pool) > 1:
            choice = self.scenario_pool[self.episode_index % len(self.scenario_pool)]
            self.episode_index += 1
            self._load_scenario(choice)
        self.assigned_casualties = set()
        self.assigned_responders = set()
        self.committed = []
        self.invalid_attempts = 0
        self.committed_violations = 0
        self.step_count = 0
        return self._observation(), {}

    def step(self, action):
        index = int(action)
        self.step_count += 1
        terminated = False
        truncated = False
        reward = 0.0
        if index == STOP_ACTION:
            terminated = True
        elif index < 0 or index >= STOP_ACTION:
            self.invalid_attempts += 1
            reward = -INVALID_PENALTY
        else:
            ci = index // MAX_RESPONDERS
            ri = index % MAX_RESPONDERS
            key = (ci, ri)
            if (
                ci in self.assigned_casualties
                or ri in self.assigned_responders
                or not self.pair_feasible.get(key, False)
            ):
                self.invalid_attempts += 1
                reward = -INVALID_PENALTY
            else:
                if not self.pair_feasible.get(key, False):
                    self.committed_violations += 1
                    reward = -INVALID_PENALTY
                else:
                    self.assigned_casualties.add(ci)
                    self.assigned_responders.add(ri)
                    self.committed.append(key)
                    reward = float(self.pair_points[key]) - self.pair_distance[key] / TRAVEL_SCALE
        if not terminated:
            if not self._remaining_feasible():
                terminated = True
            elif self.step_count >= self.max_steps:
                truncated = True
        info = {
            "invalid_attempts": self.invalid_attempts,
            "committed_violations": self.committed_violations,
            "assigned_count": len(self.committed),
            "step_count": self.step_count,
            "scenario_seed": self.scenario_seed,
        }
        return self._observation(), reward, terminated, truncated, info

    def _remaining_feasible(self) -> bool:
        for key, feasible in self.pair_feasible.items():
            if not feasible:
                continue
            ci, ri = key
            if ci not in self.assigned_casualties and ri not in self.assigned_responders:
                return True
        return False

    def committed_pairs(self) -> list[tuple[str, str]]:
        scenario = self.bundle["scenario"]
        return [
            (scenario.casualties[ci].id, scenario.responders[ri].id)
            for ci, ri in self.committed
        ]

    def committed_metrics(self) -> dict:
        points = sum(self.pair_points[key] for key in self.committed)
        travel = sum(self.pair_distance[key] for key in self.committed)
        return {
            "assigned_count": len(self.committed),
            "allocation_priority_points": float(points),
            "travel_distance_m": float(travel),
            "invalid_attempts": self.invalid_attempts,
            "committed_violations": self.committed_violations,
        }
