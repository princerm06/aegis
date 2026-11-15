"""AEGIS Neural Shadow Advisory Intelligence (MLP + Bipartite GNN).

Provides live shadow/advisory inference running alongside authoritative OR-Tools CP-SAT.
Neither neural model ever authorizes dispatch or overrides hard feasibility constraints.
Feature flag: AEGIS_NEURAL_SHADOW_ENABLED (default: false).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import logging
import os
from pathlib import Path
import sys
from time import perf_counter
from typing import Any, Literal

from pydantic import AwareDatetime, Field

from backend.app.models import Assignment, ContractModel, Identifier
from backend.app.simulation.models import SimulationMode
from optimization.models import OptimizationRequest, OptimizationResult

logger = logging.getLogger(__name__)

# Ensure experiments/neural_assignment/src is importable
_REPO_ROOT = Path(__file__).resolve().parents[3]
_NEURAL_SRC = _REPO_ROOT / "experiments" / "neural_assignment" / "src"
if _NEURAL_SRC.is_dir() and str(_NEURAL_SRC) not in sys.path:
    sys.path.insert(0, str(_NEURAL_SRC))

DEFAULT_WEIGHTS_DIR = _REPO_ROOT / "experiments" / "neural_assignment" / "weights"


def is_neural_shadow_enabled() -> bool:
    """Return whether the neural shadow advisory feature is enabled."""
    val = os.getenv("AEGIS_NEURAL_SHADOW_ENABLED", "false").strip().lower()
    return val in ("true", "1", "yes", "on")


def get_weights_directory() -> Path:
    """Return configured or default weights directory."""
    env_dir = os.getenv("AEGIS_NEURAL_WEIGHTS_DIR")
    if env_dir:
        p = Path(env_dir)
        if p.is_dir():
            return p
    return DEFAULT_WEIGHTS_DIR


def get_file_sha256(path: Path) -> str:
    """Compute SHA-256 digest of a local file."""
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while chunk := f.read(65536):
            h.update(chunk)
    return h.hexdigest()


# -----------------------------------------------------------------------------
# Pydantic Schemas for Neural Shadow Comparison Endpoint
# -----------------------------------------------------------------------------


class ConstraintValidationSummary(ContractModel):
    """Result from independent hard-constraint validator."""

    valid: bool
    violation_count: int
    violations: list[str] = Field(default_factory=list)
    checked_pairs: int


class ModelComparisonVsOrtools(ContractModel):
    """Objective differences between a neural model and authoritative OR-Tools."""

    count_diff: int
    priority_points_diff: float
    distance_diff_m: float
    matches_ortools_pairs: bool


class NeuralModelPrediction(ContractModel):
    """Real decoded prediction and diagnostics for one neural contender."""

    model_name: str
    checkpoint_file: str
    checkpoint_digest: str
    available: bool
    status: Literal["success", "unavailable", "failed"]
    selected_pairs: list[list[str]] = Field(default_factory=list)
    unassigned_casualty_ids: list[str] = Field(default_factory=list)
    assigned_count: int = 0
    priority_points: float = 0.0
    travel_distance_m: float = 0.0
    travel_cost_mm: float = 0.0
    prep_duration_ms: float = 0.0
    inference_duration_ms: float = 0.0
    decoding_duration_ms: float = 0.0
    total_duration_ms: float = 0.0
    hard_constraint_validation: ConstraintValidationSummary
    unfiltered_invalid_proposals: int = 0
    comparison_vs_ortools: ModelComparisonVsOrtools | None = None
    error: str | None = None
    authorizes_dispatch: Literal[False] = False


class DifferingAssignment(ContractModel):
    """Specific entity with differing assignments across models."""

    casualty_id: str
    ortools_responder_id: str | None
    mlp_responder_id: str | None
    gnn_responder_id: str | None


class DisagreementAnalysis(ContractModel):
    """Cross-model agreement breakdown."""

    models_agree_with_each_other: bool
    models_agree_with_ortools: bool
    differing_assignments: list[DifferingAssignment] = Field(default_factory=list)
    summary: str


class OrtoolsSummary(ContractModel):
    """Authoritative OR-Tools benchmark numbers for current request."""

    engine: str = "ortools"
    authoritative: Literal[True] = True
    assigned_count: int
    priority_points: float
    travel_distance_m: float
    travel_cost_mm: float
    selected_pairs: list[list[str]]
    runtime_ms: float
    hard_constraint_validation: ConstraintValidationSummary


class NeuralComparisonReport(ContractModel):
    """Read-only response from GET /api/v1/simulations/{scenario_id}/neural-comparison.

    Contains live shadow predictions from Pairwise MLP and Bipartite GNN on the exact
    authoritative proposal request, independently validated and compared against OR-Tools.
    OR-Tools remains the sole authoritative dispatch engine.
    """

    mode: SimulationMode = "simulation"
    schema_version: Literal["1.0"] = "1.0"
    scenario_id: Identifier
    scene_revision: int
    epoch: int
    proposal_id: Identifier | None = None
    read_only: Literal[True] = True
    authorizes_dispatch: Literal[False] = False
    authoritative_engine: Literal["ortools"] = "ortools"
    status: Literal[
        "available",
        "disabled",
        "no_proposal",
        "checkpoints_unavailable",
        "inference_failed",
        "stale",
    ]
    available: bool
    context: Literal["current", "stale"]
    stale_reasons: list[str] = Field(default_factory=list)
    evaluated_at: AwareDatetime
    message: str
    ortools_summary: OrtoolsSummary | None = None
    mlp_prediction: NeuralModelPrediction | None = None
    gnn_prediction: NeuralModelPrediction | None = None
    disagreement_analysis: DisagreementAnalysis | None = None
    voice_summary: str | None = None
    limitations: list[str] = Field(
        default_factory=lambda: [
            "Neural models are live advisory shadow predictors only; they never authorize dispatch.",
            "OR-Tools CP-SAT is the sole authoritative assignment solver in AEGIS.",
            "All committed assignments are guaranteed valid strictly by the independent hard-constraint gate, not neural weights.",
            "When context is stale, live scene has moved on: never act on stale comparisons.",
        ]
    )


# -----------------------------------------------------------------------------
# Neural Shadow Service Implementation
# -----------------------------------------------------------------------------


class NeuralShadowService:
    """Singleton/service managing model loading, live inference, and comparisons."""

    def __init__(self, weights_dir: Path | None = None, seed: int = 42) -> None:
        self.weights_dir = weights_dir or get_weights_directory()
        self.seed = int(os.getenv("AEGIS_NEURAL_SHADOW_SEED", str(seed)))
        self._mlp_model: Any = None
        self._gnn_model: Any = None
        self._mlp_meta: dict[str, Any] = {}
        self._gnn_meta: dict[str, Any] = {}
        self._loaded = False
        self._load_error: str | None = None

    def _ensure_loaded(self) -> None:
        if self._loaded:
            return
        try:
            import torch
            from neural_assignment.train import load_trained_model

            mlp_path = self.weights_dir / f"mlp_seed{self.seed}.pt"
            gnn_path = self.weights_dir / f"gnn_seed{self.seed}.pt"

            if not mlp_path.is_file():
                self._load_error = f"MLP checkpoint not found at {mlp_path}"
                return
            if not gnn_path.is_file():
                self._load_error = f"GNN checkpoint not found at {gnn_path}"
                return

            mlp_digest = get_file_sha256(mlp_path)
            gnn_digest = get_file_sha256(gnn_path)

            mlp_model, mlp_ck = load_trained_model(mlp_path)
            gnn_model, gnn_ck = load_trained_model(gnn_path)

            self._mlp_model = mlp_model
            self._gnn_model = gnn_model
            self._mlp_meta = {
                "file": mlp_path.name,
                "digest": mlp_digest,
                "checkpoint": mlp_ck,
            }
            self._gnn_meta = {
                "file": gnn_path.name,
                "digest": gnn_digest,
                "checkpoint": gnn_ck,
            }
            self._loaded = True
            self._load_error = None
            logger.info(
                f"Neural shadow models loaded successfully from {self.weights_dir} (seed={self.seed})"
            )
        except Exception as exc:
            self._load_error = f"Failed to load neural models: {type(exc).__name__}: {exc}"
            logger.warning(self._load_error)

    def is_available(self) -> tuple[bool, str | None]:
        self._ensure_loaded()
        if self._loaded and self._mlp_model is not None and self._gnn_model is not None:
            return True, None
        return False, self._load_error

    def run_inference(
        self,
        request: OptimizationRequest,
        ortools_assignments: list[Assignment],
        ortools_runtime_ms: float = 0.0,
        epoch: int = 1,
        revision: int = 1,
        proposal_id: str | None = None,
    ) -> NeuralComparisonReport:
        """Execute live genuine MLP and GNN inference on the request and compare against OR-Tools."""
        now = datetime.now(timezone.utc)
        avail, error_msg = self.is_available()
        if not avail:
            return NeuralComparisonReport(
                scenario_id=request.scenario.id,
                scene_revision=revision,
                epoch=epoch,
                proposal_id=proposal_id,
                status="checkpoints_unavailable",
                available=False,
                context="current",
                evaluated_at=now,
                message=f"Neural checkpoints unavailable: {error_msg}",
            )

        import torch
        from neural_assignment.decoder import (
            count_unfiltered_invalid_proposals,
            decode_and_validate,
        )
        from neural_assignment.features import extract_features_from_request
        from neural_assignment.validator import validate_assignments

        # 1. Authoritative OR-Tools summary
        ortools_pairs = sorted((a.casualty_id, a.responder_id) for a in ortools_assignments)
        ot_val = validate_assignments(request, ortools_pairs)
        ot_summary = OrtoolsSummary(
            engine="ortools",
            authoritative=True,
            assigned_count=int(ot_val["assigned_count"]),
            priority_points=float(ot_val["allocation_priority_points"]),
            travel_distance_m=float(ot_val["travel_distance_m"]),
            travel_cost_mm=float(ot_val["travel_cost_mm"]),
            selected_pairs=[[c, r] for c, r in ortools_pairs],
            runtime_ms=round(ortools_runtime_ms, 3),
            hard_constraint_validation=ConstraintValidationSummary(
                valid=ot_val["violation_count"] == 0,
                violation_count=int(ot_val["violation_count"]),
                violations=list(ot_val["violations"]),
                checked_pairs=int(ot_val["checked_pairs"]),
            ),
        )

        # 2. Shared feature extraction
        t_prep_start = perf_counter()
        sf = extract_features_from_request(request)
        p_tensor = torch.from_numpy(sf.pair_features)
        c_tensor = torch.from_numpy(sf.casualty_node_features)
        r_tensor = torch.from_numpy(sf.responder_node_features)
        f_tensor = torch.from_numpy(sf.feasible_mask)
        prep_ms = (perf_counter() - t_prep_start) * 1000.0

        # 3. PairwiseAssignmentMLP inference & decoding
        try:
            t_inf_mlp = perf_counter()
            with torch.no_grad():
                mlp_logits_t = self._mlp_model(p_tensor, c_tensor, r_tensor, f_tensor)
                mlp_logits = mlp_logits_t.cpu().numpy()
            mlp_inf_ms = (perf_counter() - t_inf_mlp) * 1000.0

            t_dec_mlp = perf_counter()
            mlp_decoded = decode_and_validate(
                mlp_logits, request, scene_features=sf, decoder="matching"
            )
            mlp_dec_ms = (perf_counter() - t_dec_mlp) * 1000.0

            mlp_pred = NeuralModelPrediction(
                model_name="PairwiseAssignmentMLP",
                checkpoint_file=self._mlp_meta["file"],
                checkpoint_digest=self._mlp_meta["digest"],
                available=True,
                status="success",
                selected_pairs=[[c, r] for c, r in mlp_decoded.selected_pairs],
                unassigned_casualty_ids=mlp_decoded.unassigned_casualty_ids,
                assigned_count=mlp_decoded.assigned_count,
                priority_points=mlp_decoded.allocation_priority_points,
                travel_distance_m=mlp_decoded.travel_distance_m,
                travel_cost_mm=mlp_decoded.travel_cost_mm,
                prep_duration_ms=round(prep_ms, 3),
                inference_duration_ms=round(mlp_inf_ms, 3),
                decoding_duration_ms=round(mlp_dec_ms, 3),
                total_duration_ms=round(prep_ms + mlp_inf_ms + mlp_dec_ms, 3),
                hard_constraint_validation=ConstraintValidationSummary(
                    valid=mlp_decoded.committed_constraint_violations == 0,
                    violation_count=mlp_decoded.committed_constraint_violations,
                    violations=mlp_decoded.violation_details,
                    checked_pairs=len(mlp_decoded.selected_pairs),
                ),
                unfiltered_invalid_proposals=mlp_decoded.invalid_proposed_before_filtering,
                comparison_vs_ortools=ModelComparisonVsOrtools(
                    count_diff=mlp_decoded.assigned_count - ot_summary.assigned_count,
                    priority_points_diff=round(
                        mlp_decoded.allocation_priority_points - ot_summary.priority_points, 4
                    ),
                    distance_diff_m=round(
                        mlp_decoded.travel_distance_m - ot_summary.travel_distance_m, 4
                    ),
                    matches_ortools_pairs=(mlp_decoded.selected_pairs == ortools_pairs),
                ),
                authorizes_dispatch=False,
            )
        except Exception as exc:
            logger.warning(f"MLP shadow inference error: {exc}")
            mlp_pred = NeuralModelPrediction(
                model_name="PairwiseAssignmentMLP",
                checkpoint_file=self._mlp_meta.get("file", ""),
                checkpoint_digest=self._mlp_meta.get("digest", ""),
                available=False,
                status="failed",
                hard_constraint_validation=ConstraintValidationSummary(
                    valid=False, violation_count=0, checked_pairs=0
                ),
                error=f"{type(exc).__name__}: {exc}",
                authorizes_dispatch=False,
            )

        # 4. BipartiteAssignmentGNN inference & decoding
        try:
            t_inf_gnn = perf_counter()
            with torch.no_grad():
                gnn_logits_t = self._gnn_model(p_tensor, c_tensor, r_tensor, f_tensor)
                gnn_logits = gnn_logits_t.cpu().numpy()
            gnn_inf_ms = (perf_counter() - t_inf_gnn) * 1000.0

            t_dec_gnn = perf_counter()
            gnn_decoded = decode_and_validate(
                gnn_logits, request, scene_features=sf, decoder="matching"
            )
            gnn_dec_ms = (perf_counter() - t_dec_gnn) * 1000.0

            gnn_pred = NeuralModelPrediction(
                model_name="BipartiteAssignmentGNN",
                checkpoint_file=self._gnn_meta["file"],
                checkpoint_digest=self._gnn_meta["digest"],
                available=True,
                status="success",
                selected_pairs=[[c, r] for c, r in gnn_decoded.selected_pairs],
                unassigned_casualty_ids=gnn_decoded.unassigned_casualty_ids,
                assigned_count=gnn_decoded.assigned_count,
                priority_points=gnn_decoded.allocation_priority_points,
                travel_distance_m=gnn_decoded.travel_distance_m,
                travel_cost_mm=gnn_decoded.travel_cost_mm,
                prep_duration_ms=round(prep_ms, 3),
                inference_duration_ms=round(gnn_inf_ms, 3),
                decoding_duration_ms=round(gnn_dec_ms, 3),
                total_duration_ms=round(prep_ms + gnn_inf_ms + gnn_dec_ms, 3),
                hard_constraint_validation=ConstraintValidationSummary(
                    valid=gnn_decoded.committed_constraint_violations == 0,
                    violation_count=gnn_decoded.committed_constraint_violations,
                    violations=gnn_decoded.violation_details,
                    checked_pairs=len(gnn_decoded.selected_pairs),
                ),
                unfiltered_invalid_proposals=gnn_decoded.invalid_proposed_before_filtering,
                comparison_vs_ortools=ModelComparisonVsOrtools(
                    count_diff=gnn_decoded.assigned_count - ot_summary.assigned_count,
                    priority_points_diff=round(
                        gnn_decoded.allocation_priority_points - ot_summary.priority_points, 4
                    ),
                    distance_diff_m=round(
                        gnn_decoded.travel_distance_m - ot_summary.travel_distance_m, 4
                    ),
                    matches_ortools_pairs=(gnn_decoded.selected_pairs == ortools_pairs),
                ),
                authorizes_dispatch=False,
            )
        except Exception as exc:
            logger.warning(f"GNN shadow inference error: {exc}")
            gnn_pred = NeuralModelPrediction(
                model_name="BipartiteAssignmentGNN",
                checkpoint_file=self._gnn_meta.get("file", ""),
                checkpoint_digest=self._gnn_meta.get("digest", ""),
                available=False,
                status="failed",
                hard_constraint_validation=ConstraintValidationSummary(
                    valid=False, violation_count=0, checked_pairs=0
                ),
                error=f"{type(exc).__name__}: {exc}",
                authorizes_dispatch=False,
            )

        # 5. Agreement & Disagreement Analysis
        mlp_map = {c: r for c, r in (mlp_pred.selected_pairs if mlp_pred.available else [])}
        gnn_map = {c: r for c, r in (gnn_pred.selected_pairs if gnn_pred.available else [])}
        ot_map = {c: r for c, r in ortools_pairs}

        all_cas_ids = sorted(set(ot_map.keys()) | set(mlp_map.keys()) | set(gnn_map.keys()))
        diffs: list[DifferingAssignment] = []
        for cid in all_cas_ids:
            ot_r = ot_map.get(cid)
            m_r = mlp_map.get(cid)
            g_r = gnn_map.get(cid)
            if not (ot_r == m_r == g_r):
                diffs.append(
                    DifferingAssignment(
                        casualty_id=cid,
                        ortools_responder_id=ot_r,
                        mlp_responder_id=m_r,
                        gnn_responder_id=g_r,
                    )
                )

        agree_each_other = bool(mlp_pred.available and gnn_pred.available and mlp_map == gnn_map)
        agree_ortools = bool(
            mlp_pred.available
            and gnn_pred.available
            and mlp_map == ot_map
            and gnn_map == ot_map
        )

        if agree_ortools:
            summary_text = (
                "Complete consensus: OR-Tools, MLP, and GNN all select the exact same assignments."
            )
        elif agree_each_other:
            summary_text = (
                "Neural consensus: MLP and GNN agree with each other, but diverge from OR-Tools "
                f"on {len(diffs)} assignment(s)."
            )
        else:
            summary_text = (
                f"Divergent recommendations across models ({len(diffs)} casualty differences detected). "
                "OR-Tools CP-SAT remains the authoritative proposal."
            )

        disagree_analysis = DisagreementAnalysis(
            models_agree_with_each_other=agree_each_other,
            models_agree_with_ortools=agree_ortools,
            differing_assignments=diffs,
            summary=summary_text,
        )

        # 6. Structured voice assistant summary
        voice_text = self._build_voice_summary(ot_summary, mlp_pred, gnn_pred, disagree_analysis)

        return NeuralComparisonReport(
            scenario_id=request.scenario.id,
            scene_revision=revision,
            epoch=epoch,
            proposal_id=proposal_id,
            status="available",
            available=True,
            context="current",
            evaluated_at=now,
            message="Live MLP and GNN shadow predictions evaluated and validated against OR-Tools.",
            ortools_summary=ot_summary,
            mlp_prediction=mlp_pred,
            gnn_prediction=gnn_pred,
            disagreement_analysis=disagree_analysis,
            voice_summary=voice_text,
        )

    def _build_voice_summary(
        self,
        ot: OrtoolsSummary,
        mlp: NeuralModelPrediction,
        gnn: NeuralModelPrediction,
        dis: DisagreementAnalysis,
    ) -> str:
        """Create a clear, spoken-friendly synthesis for voice assistant interactions."""
        if dis.models_agree_with_ortools:
            return (
                f"All models agree with authoritative OR-Tools on {ot.assigned_count} assignments "
                f"totaling {ot.priority_points:.0f} priority points."
            )
        parts = []
        if gnn.available and gnn.status == "success":
            gnn_diff = gnn.comparison_vs_ortools
            if gnn_diff and gnn_diff.matches_ortools_pairs:
                parts.append("GNN matches OR-Tools exactly.")
            elif gnn_diff:
                p_diff = gnn_diff.priority_points_diff
                d_diff = gnn_diff.distance_diff_m
                pts_phrase = (
                    f"{abs(p_diff):.1f} {'fewer' if p_diff < 0 else 'more'} priority points"
                    if abs(p_diff) > 0.01
                    else "the same priority points"
                )
                dist_phrase = (
                    f"{abs(d_diff):.1f}m {'longer' if d_diff > 0 else 'shorter'}"
                    if abs(d_diff) > 0.1
                    else "comparable distance"
                )
                parts.append(f"GNN suggests an alternative with {pts_phrase} and {dist_phrase}.")

        if mlp.available and mlp.status == "success":
            mlp_diff = mlp.comparison_vs_ortools
            if mlp_diff and mlp_diff.matches_ortools_pairs:
                parts.append("MLP matches OR-Tools.")
            elif mlp_diff:
                parts.append(
                    f"MLP differs on {len(dis.differing_assignments)} assignment(s) compared to OR-Tools."
                )

        parts.append("OR-Tools remains authoritative for approval and dispatch.")
        return " ".join(parts)


# Module-level default singleton
_DEFAULT_SERVICE: NeuralShadowService | None = None


def get_neural_shadow_service() -> NeuralShadowService:
    global _DEFAULT_SERVICE
    if _DEFAULT_SERVICE is None:
        _DEFAULT_SERVICE = NeuralShadowService()
    return _DEFAULT_SERVICE
