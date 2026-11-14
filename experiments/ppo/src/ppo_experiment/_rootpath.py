"""Explicit path setup so experiment code can import the parent solver.

Backend code must never import this experiment package. The import
direction is experiment -> parent core only.
"""

import sys
from pathlib import Path


def repo_root() -> Path:
    return Path(__file__).resolve().parents[4]


def ensure_parent_on_path() -> Path:
    root = repo_root()
    text = str(root)
    if text not in sys.path:
        sys.path.insert(0, text)
    return root
