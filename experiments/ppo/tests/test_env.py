"""Deterministic env tests. No training runs in this suite."""

from __future__ import annotations

import numpy as np

from ppo_experiment.env import N_ACTIONS, OBS_DIM, STOP_ACTION, AllocationEnv
from ppo_experiment.scenarios import EVAL_SEEDS, TRAIN_SEEDS


def test_train_eval_seeds_disjoint():
    assert set(TRAIN_SEEDS).isdisjoint(set(EVAL_SEEDS))


def test_spaces_documented():
    env = AllocationEnv(scenario_seed=TRAIN_SEEDS[0])
    assert env.action_space.n == N_ACTIONS
    assert env.observation_space.shape == (OBS_DIM,)
    assert STOP_ACTION == N_ACTIONS - 1


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
    from ppo_experiment.scenarios import MAX_RESPONDERS

    obs, reward, terminated, truncated, info = env.step(ci * MAX_RESPONDERS + ri)
    assert info["invalid_attempts"] == 1
    assert info["committed_violations"] == 0
    assert info["assigned_count"] == 0
    assert reward < 0


def test_repeat_proposal_counts_invalid_not_violation():
    env = AllocationEnv(scenario_seed=TRAIN_SEEDS[1])
    env.reset(seed=5)
    feasible = [
        key
        for key, ok in env.pair_feasible.items()
        if ok
    ]
    assert feasible, "expected at least one feasible pair for the test scenario"
    ci, ri = feasible[0]
    from ppo_experiment.scenarios import MAX_RESPONDERS

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
    feasible = [
        key
        for key, ok in env.pair_feasible.items()
        if ok
    ]
    if feasible and env._remaining_feasible():
        from ppo_experiment.scenarios import MAX_RESPONDERS

        ci, ri = feasible[0]
        _, _, terminated, truncated, _ = env.step(ci * MAX_RESPONDERS + ri)
        assert isinstance(terminated, bool)
        assert isinstance(truncated, bool)
        assert not (terminated and truncated)
