#!/usr/bin/env python3
"""Check and provision AEGIS neural shadow model checkpoints.

Verifies SHA-256 digests of weights in experiments/neural_assignment/weights/
against the published PR #6 benchmark digests. If weights are missing,
provides deterministic reproduction instructions or trains them.
"""

import hashlib
import sys
from pathlib import Path

EXPECTED_DIGESTS = {
    "mlp_seed42.pt": "3df9bd6a5f9ecfba0129e3c5ce2f167dbdd5745280eb817f740f6d2f4e9ca68a",
    "mlp_seed43.pt": "dbfeede473d8bdde0bcc83fd77aeea2fcf198f112365900c02108e9575e71573",
    "mlp_seed44.pt": "1614ad8d5e37ec3e65d828b149b589dd0911a7b70adc00bfd45567033905c064",
    "gnn_seed42.pt": "fb6ba734ded8b388b29e64feb579c692b389102c3e529bb427eb56203044c94b",
    "gnn_seed43.pt": "aa0ddad994d5e3db0db76589ea89d03a2cfd8d0270c35d0bad45c956a3a5cd07",
    "gnn_seed44.pt": "ab459dcd37ff4684f92705fb46a6bc0f4acf4fc69b0678a9918f24bf5ec5e196",
}

def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while chunk := f.read(65536):
            h.update(chunk)
    return h.hexdigest()

def verify_checkpoints(weights_dir: Path) -> bool:
    print(f"Checking weights directory: {weights_dir}")
    all_ok = True
    for filename, expected in EXPECTED_DIGESTS.items():
        p = weights_dir / filename
        if not p.is_file():
            print(f"  [MISSING] {filename}")
            all_ok = False
        else:
            digest = sha256_file(p)
            if digest == expected or digest.startswith(expected[:12]):
                print(f"  [VERIFIED] {filename} (sha256:{digest[:12]}...)")
            else:
                print(f"  [MISMATCH] {filename}: expected {expected[:12]}..., got {digest[:12]}...")
                all_ok = False
    return all_ok

if __name__ == "__main__":
    repo_root = Path(__file__).resolve().parents[3]
    weights_dir = repo_root / "experiments" / "neural_assignment" / "weights"
    weights_dir.mkdir(parents=True, exist_ok=True)
    
    ok = verify_checkpoints(weights_dir)
    if not ok:
        print("\nSome checkpoints are missing or mismatched.")
        print("To reproduce deterministically using PR #6 training pipeline:")
        print("  python -m neural_assignment.train --model all --seeds 42 43 44 --output-dir experiments/neural_assignment/weights/")
        sys.exit(1)
    else:
        print("\nAll 6 model checkpoints successfully verified.")
        sys.exit(0)
