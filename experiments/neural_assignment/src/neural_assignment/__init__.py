"""AEGIS isolated supervised neural assignment experiment.

Offline simulation and research evaluation only. Never authorizes dispatch,
never infers clinical triage severity, and never replaces the production
OR-Tools solver.
"""

from neural_assignment._rootpath import ensure_repo_root_on_path

ensure_repo_root_on_path()

GENERATOR_VERSION = "aegis-neural-synth-v1.0"
FEATURE_SCHEMA_VERSION = "aegis-pair-features-v1.0"
