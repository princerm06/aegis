# Isolated synthetic PPO allocation experiment

Advisory research only. This experiment trains a PPO policy on small
synthetic allocation scenarios and compares it against the real core
optimizer. Outputs are not dispatch orders, not medical assessments,
and never authorize deployment, dispatch, or field approvals.

## Isolation

- Owns only `experiments/ppo/`. Never edits core, root deps, live
  APIs, or teammate directories.
- Own `pyproject.toml` / `uv.lock` and own `.venv` inside this folder.
- Import direction is experiment -> parent core only, via explicit
  path setup in `src/ppo_experiment/_rootpath.py`. Backend code must
  never import this package.
- Trained weights live in `weights/` (git-ignored). Only JSON/Markdown
  reports are committed.

## Environment

`AllocationEnv` is a multi-step Gymnasium environment over a fixed
synthetic scenario bundle:

- Reset builds assignment state from the scenario seed and calls
  `super().reset(seed=seed)`.
- Observation is a fixed 53-dim unit-range vector: per-casualty
  position, priority, and assigned flag; per-responder position,
  availability flag, and assigned flag; the full pair feasibility
  mask with normalized A* route costs on the exact graph the solver
  sees; plus step fraction. The policy has no
  partial-observability excuse for any gap to the optimizer.
- Episodes cycle a deterministic pool of training scenario seeds
  while heldout seeds stay disjoint and fixed.
- Action `Discrete(C*R+1)`: each index names one casualty/responder
  pair, the last index is stop.
- Transition commits a feasible pair, blocks anything else.
- Reward is allocation priority points minus scaled A* travel per
  committed assignment, with a small penalty per blocked attempt.
- Termination fires when no feasible proposal remains or stop is
  taken. Truncation fires at the step budget. They are reported
  separately.

Hard safety uses the same gates as the core solver: responder
availability, capability compatibility, core observation and
tracking reasons (stale, lost, low-confidence, position mismatch),
and verified A* routes on the shared scene map, plus one-to-one
assignment. Blocked proposals are
counted as invalid attempts before commitment. Committed safety
violations are counted separately and must stay zero. Blocking an
invalid proposal is filtering, not learned safety, and is reported
that way.

## Identical conditions for all contenders

Every contender sees the same fixed scenario bundles. Each PPO
model trains over a deterministic pool of scenario seeds (101-104)
that cycles episode by episode, while heldout eval seeds 901-904
stay disjoint from every training pool. OR-Tools and nearest run
one-shot through the real `optimization.solver.optimize`. PPO rolls
out sequentially in the env with deterministic actions. All
committed pairs pass through the shared independent validator
(`validator.py`), so baseline violations would be reported rather
than assumed away. Metrics per scenario are assigned count,
allocation priority points, travel distance, unassigned count,
prep/run/total timing, invalid attempts, and committed violations.
Timing compares prep-plus-run totals after a warmup moves imports,
model loading, and first-call optimizer setup outside measurement;
no speed ranking is claimed from cold-start effects.
Underperformance by PPO is accepted and reported as-is.

## Setup

```sh
cd experiments/ppo
uv sync
```

## Tests (no training)

```sh
cd experiments/ppo
PYTHONPATH=src uv run pytest
```

## Train (bounded CPU, 2 seeds)

```sh
cd experiments/ppo
PYTHONPATH=src uv run python -m ppo_experiment.train --seed 101 --train-seeds 101 102 103 104 --timesteps 8192 --outdir weights
PYTHONPATH=src uv run python -m ppo_experiment.train --seed 102 --train-seeds 101 102 103 104 --timesteps 8192 --outdir weights
```

## Evaluate heldout scenarios

```sh
cd experiments/ppo
PYTHONPATH=src uv run python -m ppo_experiment.evaluate \
  --weights weights/ppo_allocation_train101.zip weights/ppo_allocation_train102.zip \
  --eval-seeds 901 902 903 904 \
  --outdir reports
```

## Library use

```python
from ppo_experiment import AllocationEnv  # with PYTHONPATH=src

env = AllocationEnv(scenario_seed=901)
obs, info = env.reset(seed=0)
obs, reward, terminated, truncated, info = env.step(0)
```

## Measured results (2026-11-14)

Two PPO models (seeds 101/102, 8192 CPU steps each, scenario pool
101-104, obs dim 53) vs OR-Tools and nearest on heldout seeds
901-904. Full rows in `reports/eval_results.md`, config and weight
SHAs in `reports/training.json`.

- OR-Tools: 8 assigned / 16.0 points over 4 seeds, 0 violations.
- Nearest: 8 assigned / 14.0 points, 0 violations.
- PPO seed 101: 4 assigned / 8.0 points, 0 violations,
  48 invalid attempts.
- PPO seed 102: 6 assigned / 12.0 points, 0 violations,
  41 invalid attempts.
- Weights: `a5f66cbb...` (train101), `3d7106d0...` (train102);
  binaries stay local, SHAs committed.

The optimizer leads on every heldout seed. PPO trails without a
single committed violation; eval seeds were not tuned.

## Limitations

- Tiny synthetic grids only: no responder motion, no mid-episode
  casualties, no sensor noise beyond gate fixtures.
- Blocked proposals are filtered before commitment; invalid-attempt
  counts measure filtering, not learned safety.
- PPO sees the full feasibility mask and route costs, so the gap to
  the optimizer is not a partial-observability artifact.
- Reward is a scalar (points minus distance/20) while the optimizer
  is lexicographic (count, then points, then travel): reported
  metrics are comparable, training objectives are not equal.
- Only prep-plus-run timing totals are comparable: PPO prep holds
  the all-pairs A* precompute while baseline route costs sit inside
  the timed solve, so prep/run buckets must not be ranked.
- Bounded compute (8192 steps, CPU, default PPO hyperparameters);
  larger budgets might narrow the gap and were not tried.
- Timing compares research-harness totals, not deployment latency.
  Nothing here authorizes operational use.
