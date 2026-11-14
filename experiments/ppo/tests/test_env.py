"""Deterministic env tests. No training runs in this suite."""

from __future__ import annotations

from datetime import timedelta

import numpy as np

from ppo_experiment import env as env_module
from ppo_experiment import _rootpath as _rp

_rp.ensure_parent_on_path()

from optimization.solver import optimize
from ppo_experiment.env import N_ACTIONS, OBS_DIM, PAIR_COUNT, STOP_ACTION, AllocationEnv
from ppo_experiment.scenarios import (
    EVAL_SEEDS,
    MAX_CASUALTIES,
    MAX_RESPONDERS,
    TRAIN_SEEDS,
    build_bundle,
    make_request,
)
from ppo_experiment.validator import validate


def stale_bundle(seed: int) -> dict:
    bundle = build_bundle(seed)
    first = bundle["scenario"].casualties[0]
    old = bundle["observations"][first.id]
    moment = bundle["now"] - timedelta(seconds=3600)
    track = old.track.model_copy(
        update={"first_seen_at": moment - timedelta(seconds=5), "last_seen_at": moment}
    )
    bundle["observations"][first.id] = old.model_copy(
        update={"observed_at": moment, "track": track}
    )
    return bundle


def lost_track_bundle(seed: int) -> dict:
    bundle = build_bundle(seed)
    first = bundle["scenario"].casualties[0]
    old = bundle["observations"][first.id]
    track = old.track.model_copy(update={"state": "lost"})
    bundle["observations"][first.id] = old.model_copy(update={"track": track})
    return bundle


def low_confidence_bundle(seed: int) -> dict:
    bundle = build_bundle(seed)
    first = bundle["scenario"].casualties[0]
    old = bundle["observations"][first.id]
    bundle["observations"][first.id] = old.model_copy(update={"confidence": 0.1})
    return bundle


def moved_observation_bundle(seed: int) -> dict:
    bundle = build_bundle(seed)
    first = bundle["scenario"].casualties[0]
    old = bundle["observations"][first.id]
    position = old.position.model_copy(update={"x_m": old.position.x_m + 5.0})
    bundle["observations"][first.id] = old.model_copy(update={"position": position})
    return bundle


def env_over(bundle: dict) -> AllocationEnv:
    original = env_module.build_bundle
    env_module.build_bundle = lambda seed: bundle
    try:
        return AllocationEnv(scenario_seed=bundle["scenario_seed"])
    finally:
        env_module.build_bundle = original


def test_train_eval_seeds_disjoint():
    assert set(TRAIN_SEEDS).isdisjoint(set(EVAL_SEEDS))


def test_spaces_documented():
    env = AllocationEnv(scenario_seed=TRAIN_SEEDS[0])
    assert env.action_space.n == N_ACTIONS
    assert env.observation_space.shape == (OBS_DIM,)
    assert OBS_DIM == MAX_CASUALTIES * 4 + MAX_RESPONDERS * 4 + PAIR_COUNT * 2 + 1
    assert STOP_ACTION == N_ACTIONS - 1


def test_observation_carries_feasibility_and_cost():
    env = AllocationEnv(scenario_seed=TRAIN_SEEDS[0])
    obs, _ = env.reset(seed=7)
    base = MAX_CASUALTIES * 4 + MAX_RESPONDERS * 4
    mask = obs[base : base + PAIR_COUNT]
    costs = obs[base + PAIR_COUNT : base + PAIR_COUNT * 2]
    assert set(np.unique(mask)).issubset({0.0, 1.0})
    index = 0
    for ci in range(MAX_CASUALTIES):
        for ri in range(MAX_RESPONDERS):
            feasible = env.pair_feasible.get((ci, ri), False)
            assert float(mask[index]) == (1.0 if feasible else 0.0)
            if feasible:
                assert float(costs[index]) > 0.0
            else:
                assert float(costs[index]) == 0.0
            index += 1


def test_check_env():
    from stable_baselines3.common.env_checker import check_env

    env = AllocationEnv(scenario_seed=TRAIN_SEEDS[0])
    check_env(env, warn=True)


def test_reset_deterministic():
    first = AllocationEnv(scenario_seed=EVAL_SEEDS[0])
    second = AllocationEnv(scenario_seed=EVAL_SEEDS[0])
    obs_a, _ = first.reset(seed=7)
    obs_b, _ = second.reset(seed=7)
    np.testing.assert_allclose(obs_a, obs_b)


def test_episode_pool_cycles_deterministically():
    pool = (TRAIN_SEEDS[0], TRAIN_SEEDS[1])
    runs = []
    for _ in range(2):
        env = AllocationEnv(scenario_seed=pool[0], train_seeds=pool)
        trace = []
        for episode in range(4):
            obs, _ = env.reset(seed=21)
            trace.append((env.scenario_seed, obs.tobytes()))
            done = False
            while not done:
                obs, _, terminated, truncated, _ = env.step(STOP_ACTION)
                done = terminated or truncated
                trace.append(obs.tobytes())
        runs.append(trace)
    assert runs[0] == runs[1]
    seeds = [item[0] for item in runs[0] if isinstance(item, tuple)]
    assert seeds == [pool[0], pool[1], pool[0], pool[1]]


def test_step_sequence_deterministic():
    actions = [0, 5, STOP_ACTION]
    runs = []
    for _ in range(2):
        env = AllocationEnv(scenario_seed=EVAL_SEEDS[1])
        env.reset(seed=11)
        trace = []
        for action in actions:
            obs, reward, terminated, truncated, info = env.step(action)
            trace.append((obs.tobytes(), reward, terminated, truncated))
            if terminated or truncated:
                break
        runs.append(trace)
    assert len(runs[0]) == len(runs[1])
    for left, right in zip(runs[0], runs[1]):
        assert left[0] == right[0]
        assert left[1] == right[1]
        assert left[2] == right[2]
        assert left[3] == right[3]


def test_invalid_attempts_not_violations():
    env = AllocationEnv(scenario_seed=TRAIN_SEEDS[0])
    env.reset(seed=3)
    blocked = [key for key, ok in env.pair_feasible.items() if not ok]
    assert blocked, "expected at least one infeasible pair for the test scenario"
    ci, ri = blocked[0]
    obs, reward, terminated, truncated, info = env.step(ci * MAX_RESPONDERS + ri)
    assert info["invalid_attempts"] == 1
    assert info["committed_violations"] == 0
    assert info["assigned_count"] == 0
    assert reward < 0


def test_repeat_proposal_counts_invalid_not_violation():
    env = AllocationEnv(scenario_seed=TRAIN_SEEDS[1])
    env.reset(seed=5)
    feasible = [key for key, ok in env.pair_feasible.items() if ok]
    assert feasible, "expected at least one feasible pair for the test scenario"
    ci, ri = feasible[0]
    action = ci * MAX_RESPONDERS + ri
    env.step(action)
    _, _, _, _, info = env.step(action)
    assert info["invalid_attempts"] == 1
    assert info["committed_violations"] == 0
    assert info["assigned_count"] == 1


def test_terminated_truncated_never_both():
    env = AllocationEnv(scenario_seed=EVAL_SEEDS[2], max_steps=2)
    env.reset(seed=9)
    for _ in range(10):
        _, _, terminated, truncated, _ = env.step(STOP_ACTION)
        assert not (terminated and truncated)
        if terminated or truncated:
            break


def test_truncation_at_step_budget():
    env = AllocationEnv(scenario_seed=EVAL_SEEDS[3], max_steps=1)
    env.reset(seed=13)
    feasible = [key for key, ok in env.pair_feasible.items() if ok]
    if feasible and env._remaining_feasible():
        ci, ri = feasible[0]
        _, _, terminated, truncated, _ = env.step(ci * MAX_RESPONDERS + ri)
        assert isinstance(terminated, bool)
        assert isinstance(truncated, bool)
        assert not (terminated and truncated)


def test_solver_assigned_pairs_are_env_feasible():
    for seed in (TRAIN_SEEDS[0], EVAL_SEEDS[0]):
        bundle = build_bundle(seed)
        result = optimize(make_request(bundle, "ortools"))
        env = env_over(bundle)
        scenario = bundle["scenario"]
        casualty_index = {c.id: i for i, c in enumerate(scenario.casualties)}
        responder_index = {r.id: i for i, r in enumerate(scenario.responders)}
        for assignment in result.assignments:
            key = (casualty_index[assignment.casualty_id], responder_index[assignment.responder_id])
            assert env.pair_feasible[key] is True


def test_observation_gates_match_solver():
    cases = [
        (stale_bundle, "observation_stale"),
        (lost_track_bundle, "track_lost"),
        (low_confidence_bundle, "observation_low_confidence"),
        (moved_observation_bundle, "observation_position_mismatch"),
    ]
    for make_bundle, reason in cases:
        bundle = make_bundle(TRAIN_SEEDS[0])
        result = optimize(make_request(bundle, "ortools"))
        first_id = bundle["scenario"].casualties[0].id
        assert first_id in result.unassigned_casualty_ids
        assert reason in result.reasons[first_id]
        env = env_over(bundle)
        for (ci, _ri), feasible in env.pair_feasible.items():
            if ci == 0:
                assert feasible is False


def test_validator_accepts_solver_output():
    bundle = build_bundle(EVAL_SEEDS[0])
    result = optimize(make_request(bundle, "ortools"))
    pairs = [(a.casualty_id, a.responder_id) for a in result.assignments]
    check = validate(bundle, pairs)
    assert check["violation_count"] == 0
    assert check["checked_pairs"] == len(pairs)
    assert check["allocation_priority_points"] == float(
        result.objective_values["allocation_priority_points"]
    )
    assert check["travel_distance_m"] == float(result.objective_values["new_travel_distance_m"])


def test_validator_catches_induced_violations():
    bundle = build_bundle(TRAIN_SEEDS[0])
    scenario = bundle["scenario"]
    by_id_casualty = {c.id: c for c in scenario.casualties}
    assert by_id_casualty, "expected casualties in the test scenario"
    good = validate(bundle, [("c0", "r0")])
    assert good["violation_count"] == 0
    duplicated = validate(bundle, [("c0", "r0"), ("c2", "r0")])
    assert duplicated["violation_count"] == 1
    assert duplicated["violations"][0].startswith("duplicate_responder")
    incapable = validate(bundle, [("c1", "r1")])
    assert incapable["violation_count"] >= 1
    assert incapable["violations"][0].startswith("required_capabilities_missing")
    busy = validate(bundle, [("c0", "r2")])
    assert busy["violation_count"] >= 1
    assert busy["violations"][0].startswith("responder_unavailable")
    stale = stale_bundle(TRAIN_SEEDS[0])
    blocked = validate(stale, [("c0", "r0")])
    assert blocked["violation_count"] == 1
    assert blocked["violations"][0].startswith("observation_blocked")
