#!/usr/bin/env python3
"""AEGIS Neural Checkpoint Verification & Integrity Auditor.

Verifies the cryptographic SHA-256 integrity of trained PyTorch model checkpoints
in experiments/neural_assignment/weights/ against published PR #6 benchmark digests.

IMPORTANT OPERATIONAL NOTICE:
This tool ONLY verifies existing local checkpoints on disk. It does NOT automatically
download files from external remotes or provision missing weights over the network.

OBTAINING CHECKPOINTS ON A FRESH CHECKOUT:
Because trained weights (.pt binary files) are Git-ignored (.gitignore:5:weights/),
a fresh repository clone will not contain pre-packaged weights. To obtain them:
1. Secure Transfer: Copy the validated .pt files from the team's internal release
   store / project artifact cache into experiments/neural_assignment/weights/.
2. Local Deterministic Retraining:
   Execute the PR #6 training pipeline:
     python -m neural_assignment.train --model all --seeds 42 43 44 \
       --output-dir experiments/neural_assignment/weights/
   
   *Hash Reproducibility Note*: While the pipeline enforces deterministic seeds
   (torch.manual_seed, np.random.seed, random.seed) and deterministic PyTorch
   algorithms, cross-platform floating-point variations (e.g. Apple Silicon Accelerate
   vs Linux CUDA/x86_64 MKL/BLAS) and differing PyTorch minor versions can yield
   minor numerical variations in serialized floating-point tensor weights. On the
   intended demonstration machine (macOS aarch64, PyTorch 2.x), seed-42 weights
   produce the exact 64-character SHA-256 hashes listed in EXPECTED_DIGESTS.
   If weights are retrained on a different microarchitecture, audit the newly
   trained checkpoints against validation metrics (MLP F1 >= 0.79, GNN F1 >= 0.92,
   0 hard constraint violations) before updating EXPECTED_DIGESTS.
"""

from __future__ import annotations

import argparse
import hashlib
import sys
from pathlib import Path

# Full, exact 64-character SHA-256 digests from PR #6 validated checkpoints
EXPECTED_DIGESTS = {
    "mlp_seed42.pt": "3df9bd6a5f9ecfba0129e3c5ce2f167dbdd5745280eb817f740f6d2f4e9ca68a",
    "mlp_seed43.pt": "dbfeede473d8bdde0bcc83fd77aeea2fcf198f112365900c02108e9575e71573",
    "mlp_seed44.pt": "1614ad8d5e37ec3e65d828b149b589dd0911a7b70adc00bfd45567033905c064",
    "gnn_seed42.pt": "fb6ba734ded8b388b29e64feb579c692b389102c3e529bb427eb56203044c94b",
    "gnn_seed43.pt": "aa0ddad994d5e3db0db76589ea89d03a2cfd8d0270c35d0bad45c956a3a5cd07",
    "gnn_seed44.pt": "ab459dcd37ff4684f92705fb46a6bc0f4acf4fc69b0678a9918f24bf5ec5e196",
}


def sha256_file(path: Path) -> str:
    """Compute exact full 64-character SHA-256 hex digest of a local file."""
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while chunk := f.read(65536):
            h.update(chunk)
    return h.hexdigest()


def verify_checkpoints(weights_dir: Path, target_seeds: list[int] | None = None) -> bool:
    """Verify presence and exact SHA-256 digest equality for model checkpoints.
    
    Rejects any missing, truncated, or modified checkpoint. Partial matching
    is strictly forbidden.
    """
    print(f"Auditing checkpoint directory: {weights_dir}")
    all_ok = True
    
    items = list(EXPECTED_DIGESTS.items())
    if target_seeds is not None:
        seed_suffixes = tuple(f"seed{s}.pt" for s in target_seeds)
        items = [(k, v) for k, v in items if k.endswith(seed_suffixes)]

    for filename, expected in items:
        p = weights_dir / filename
        if not p.is_file():
            print(f"  [MISSING] {filename}")
            all_ok = False
            continue
            
        digest = sha256_file(p)
        # Strict exact 64-character match requirement — no partial/prefix matching allowed
        if digest == expected:
            print(f"  [VERIFIED] {filename} (sha256:{digest[:16]}...)")
        else:
            print(f"  [MISMATCH/CORRUPT] {filename}:")
            print(f"    Expected: {expected}")
            print(f"    Actual:   {digest}")
            all_ok = False
            
    return all_ok


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Verify cryptographic integrity of AEGIS neural shadow checkpoints."
    )
    parser.add_argument(
        "--weights-dir",
        type=Path,
        default=None,
        help="Path to weights directory (defaults to experiments/neural_assignment/weights)",
    )
    parser.add_argument(
        "--seeds",
        nargs="+",
        type=int,
        default=None,
        help="Specific seeds to check (e.g. --seeds 42)",
    )
    args = parser.parse_args()

    repo_root = Path(__file__).resolve().parents[3]
    weights_dir = args.weights_dir or (repo_root / "experiments" / "neural_assignment" / "weights")

    if not weights_dir.is_dir():
        print(f"Error: Weights directory not found at {weights_dir}")
        print("Note: Checkpoints are not automatically downloaded. Place trained weights here or run training.")
        return 1

    ok = verify_checkpoints(weights_dir, target_seeds=args.seeds)
    if not ok:
        print("\nCheckpoint verification FAILED.")
        print("One or more checkpoints are missing, truncated, or modified.")
        print("To provision weights on a fresh checkout:")
        print("  1. Copy validated .pt files into experiments/neural_assignment/weights/")
        print("  2. Or retrain locally using:")
        print("     python -m neural_assignment.train --model all --seeds 42 43 44 --output-dir experiments/neural_assignment/weights/")
        return 1

    print("\nAll requested model checkpoints verified with exact SHA-256 match.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
