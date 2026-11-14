"""Bounded CPU PPO training over the synthetic allocation env."""

from __future__ import annotations

import argparse
import hashlib
import json
import platform
from datetime import datetime, timezone
from pathlib import Path

from ppo_experiment import _rootpath as _rp

_rp.ensure_parent_on_path()

from ppo_experiment.env import AllocationEnv  # noqa: E402

DEFAULT_TIMESTEPS = 8192


def train(seed: int, timesteps: int, outdir: str) -> dict:
    import stable_baselines3
    import torch
    from stable_baselines3 import PPO

    started = datetime.now(timezone.utc)
    wall_start = __import__("time").perf_counter()
    env = AllocationEnv(scenario_seed=seed)
    model = PPO(
        "MlpPolicy",
        env,
        seed=seed,
        device="cpu",
        n_steps=512,
        batch_size=64,
        n_epochs=4,
        gamma=0.99,
        learning_rate=3e-4,
        verbose=0,
    )
    model.learn(total_timesteps=timesteps)
    out = Path(outdir)
    out.mkdir(parents=True, exist_ok=True)
    path = out / f"ppo_allocation_train{seed}.zip"
    model.save(str(path.with_suffix("")))
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    record = {
        "training_seed": seed,
        "scenario_seed": seed,
        "total_timesteps": timesteps,
        "policy": "MlpPolicy",
        "n_steps": 512,
        "batch_size": 64,
        "n_epochs": 4,
        "gamma": 0.99,
        "learning_rate": 3e-4,
        "device": "cpu",
        "sb3_version": stable_baselines3.__version__,
        "torch_version": torch.__version__,
        "platform": platform.platform(),
        "python": platform.python_version(),
        "weights_file": path.name,
        "weights_sha256": digest,
        "started_at": started.isoformat(),
        "wall_seconds": __import__("time").perf_counter() - wall_start,
    }
    (out / f"train_{seed}.json").write_text(json.dumps(record, indent=2))
    return record


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--seed", type=int, required=True)
    parser.add_argument("--timesteps", type=int, default=DEFAULT_TIMESTEPS)
    parser.add_argument("--outdir", type=str, required=True)
    args = parser.parse_args()
    record = train(args.seed, args.timesteps, args.outdir)
    print(json.dumps(record, indent=2))


if __name__ == "__main__":
    main()
