"""Isolated synthetic PPO allocation experiment.

Advisory allocation research only. Outputs are not dispatch orders,
not medical assessments, and never authorize deployment or field use.
"""

from ppo_experiment.env import AllocationEnv
from ppo_experiment.scenarios import TRAIN_SEEDS, EVAL_SEEDS, build_bundle

__all__ = ["AllocationEnv", "TRAIN_SEEDS", "EVAL_SEEDS", "build_bundle"]
