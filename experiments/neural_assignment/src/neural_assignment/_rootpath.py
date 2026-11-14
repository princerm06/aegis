"""Explicit path bootstrap from experiments/neural_assignment into the repo root.

Import direction is strictly one-way: neural_assignment -> core optimization/backend models.
Production backend code never imports from experiments/neural_assignment.
"""

from __future__ import annotations

import sys
from pathlib import Path


def ensure_repo_root_on_path() -> Path:
    repo_root = Path(__file__).resolve().parents[4]
    root_str = str(repo_root)
    if root_str not in sys.path:
        sys.path.insert(0, root_str)
    return repo_root
