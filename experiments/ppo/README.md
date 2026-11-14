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
- Observation is a fixed unit-range vector: per-casualty position,
  allocation priority, and assigned flag; per-responder position,
  availability flag, and assigned flag; plus step fraction.
- Action `Discrete(C*R+1)`: each index names one casualty/responder
  pair, the last index is stop.
- Transition commits a feasible pair, blocks anything else.
- Reward is allocation priority points minus scaled A* travel per
  committed assignment, with a small penalty per blocked attempt.
- Termination fires when no feasible proposal remains or stop is
  taken. Truncation fires at the step budget. They are reported
  separately.

Hard safety uses the same gates as the core solver: responder
availability, capability compatibility, and verified A* routes on the
shared scene map, plus one-to-one assignment. Blocked proposals are
counted as invalid attempts before commitment. Committed safety
violations are counted separately and must stay zero. Blocking an
invalid proposal is filtering, not learned safety, and is reported
that way.

## Identical conditions for all contenders

Every contender sees the same fixed scenario bundles (train seeds
disjoint from eval seeds). OR-Tools and nearest run one-shot through
the real `optimization.solver.optimize`. PPO rolls out sequentially
in the env with deterministic actions. Metrics per scenario are
assigned count, allocation priority points, travel distance,
unassigned count, runtime, invalid attempts, and committed
violations. Underperformance by PPO is accepted and reported as-is.

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
PYTHONPATH=src uv run python -m ppo_experiment.train --seed 101 --timesteps 8192 --outdir weights
PYTHONPATH=src uv run python -m ppo_experiment.train --seed 102 --timesteps 8192 --outdir weights
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
