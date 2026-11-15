"""Simulation request/response schemas. Every payload is explicitly SIMULATION.

These models extend, and never replace, the contract entities in
``backend.app.models``. Simulation approvals are local operator decisions with
no authentication claim and can never satisfy a real dispatch gate.
"""

from typing import Annotated, Any, Literal

from pydantic import AwareDatetime, Field, model_validator

from backend.app.models import (
    AStarRoute,
    Assignment,
    ContractModel,
    CVObservation,
    CVTrack,
    CameraSigns,
    Identifier,
    Position,
    RecommendationMetadata,
    Scenario,
)

INTELLIGENCE_INTERPRETATION = (
    "SIMULATION advisory diagnostics. ranking_score is a deterministic recency-weighted "
    "heuristic for ordering evidence: it is not a calibrated probability, not a clinical "
    "severity, and not a measure of medical urgency. eligible_for_new_assignment covers "
    "temporal CV evidence only; routing, capability, availability and capacity feasibility "
    "are decided separately by the solver. Objective tiers describe the solver's stated "
    "lexicographic objective, not real-world outcome quality. Nothing here approves, "
    "dispatches, or contacts any device.")

SimulationMode = Literal["simulation"]
PositiveRevision = Annotated[int, Field(ge=1, strict=True)]
CapabilityTag = Annotated[str, Field(min_length=1, pattern=r"\S")]


class SimulationRequest(ContractModel):
    """Optional ``mode`` must be ``simulation``; ``real`` is rejected with 422."""

    mode: SimulationMode = "simulation"


class ResetRequest(SimulationRequest):
    """Reset a session. Without ``scenario`` only ``demo`` is loadable.

    A supplied scenario must be synthetic and its ``id`` must match the path.
    ``add_demo_map`` adds the known synthetic 24x20 grid when no map is present.
    """

    scenario: Scenario | None = None
    required_capabilities: dict[Identifier, list[CapabilityTag]] = Field(default_factory=dict)
    add_demo_map: bool = True


class AssociationRequest(SimulationRequest):
    """Bind a CV track to an existing casualty, or create a confirmed casualty."""

    track_id: Identifier
    casualty_id: Identifier
    create_casualty: bool = False
    actor: Identifier


class ResponderUpdateRequest(SimulationRequest):
    position: Position | None = None
    status: Literal["available", "busy", "offline"] | None = None
    capabilities: list[CapabilityTag] | None = None
    reported_at: AwareDatetime | None = None

    @model_validator(mode="after")
    def require_change(self) -> "ResponderUpdateRequest":
        if self.position is None and self.status is None and self.capabilities is None:
            raise ValueError("Provide at least one of position, status, capabilities")
        return self


class RequiredCapabilitiesRequest(SimulationRequest):
    capabilities: list[CapabilityTag]
    actor: Identifier


class ReplanRequest(SimulationRequest):
    actor: Identifier
    reason: Identifier | None = None
    solver: Literal["ortools", "fallback", "nearest"] | None = None


class DecisionRequest(SimulationRequest):
    """Human decision on the exact proposal revision. ``actor`` is the local
    operator ID; it is recorded, not authenticated."""

    proposal_revision: PositiveRevision
    decision: Literal["approve", "reject"]
    actor: Identifier


class DispatchRequest(SimulationRequest):
    proposal_id: Identifier
    proposal_revision: PositiveRevision
    approval_id: Identifier
    actor: Identifier


class SimulatedDeviceEvent(ContractModel):
    """Contract DeviceAssignmentEvent restricted to ``mode: simulation``."""

    id: Identifier
    scenario_id: Identifier
    device_id: Identifier
    dispatch_id: Identifier
    command_id: Identifier
    casualty_id: Identifier
    responder_id: Identifier
    kind: Literal["delivered", "acknowledged", "completed"]
    occurred_at: AwareDatetime
    mode: SimulationMode


class SimulateDeviceRequest(SimulationRequest):
    """Convenience: the backend builds the correlated SimulatedDeviceEvent."""

    kind: Literal["delivered", "acknowledged", "completed"]
    event_id: Identifier | None = None
    occurred_at: AwareDatetime | None = None


NonblankIdentifier = Annotated[str, Field(min_length=1, pattern=r"\S")]
Confidence = Annotated[float, Field(ge=0, le=1, allow_inf_nan=False)]
# Plain http(s) address of the camera's live view; no credentials in the authority.
StreamUrl = Annotated[str, Field(max_length=500, pattern=r"^https?://[^\s/@]+(/\S*)?$")]


class CameraRegistration(ContractModel):
    """SIMULATION camera mapping. ``calibration_ref`` is a human/external
    assertion that a world calibration exists; the backend computes nothing.
    ``stream_url`` is where dashboards can show the camera's live view; unlike
    the mapping it follows the latest registration (a restarted camera may move)."""

    mode: SimulationMode
    actor: Identifier
    mapping_mode: Literal["calibrated_world", "predefined_zones"]
    calibration_ref: NonblankIdentifier | None = None
    zones: dict[NonblankIdentifier, Position] | None = None
    stream_url: StreamUrl | None = None

    @model_validator(mode="after")
    def validate_mapping(self) -> "CameraRegistration":
        if self.mapping_mode == "calibrated_world":
            if self.calibration_ref is None:
                raise ValueError("calibrated_world requires a nonempty calibration_ref")
            if self.zones is not None:
                raise ValueError("calibrated_world cameras must not define zones")
        elif not self.zones:
            raise ValueError("predefined_zones requires at least one named zone Position")
        return self


class CameraObservation(ContractModel):
    """Camera-local detection report; exactly one of position / zone_id.

    No severity or clinical fields exist; ``source``/``kind`` are implied
    (``cv`` / ``person_detected``)."""

    mode: SimulationMode
    id: Identifier
    observed_at: AwareDatetime
    confidence: Confidence
    track: CVTrack
    position: Position | None = None
    zone_id: NonblankIdentifier | None = None
    signs: CameraSigns | None = None

    @model_validator(mode="after")
    def validate_location(self) -> "CameraObservation":
        if (self.position is None) == (self.zone_id is None):
            raise ValueError("Provide exactly one of position or zone_id")
        if self.track.last_seen_at > self.observed_at:
            raise ValueError("last_seen_at must not follow observed_at")
        if self.signs is not None and self.track.state != "visible":
            raise ValueError("signs are only valid on visible tracked observations")
        return self


class CameraRecord(ContractModel):
    mode: SimulationMode = "simulation"
    camera_id: Identifier
    epoch: int
    mapping_mode: Literal["calibrated_world", "predefined_zones"]
    calibration_ref: str | None
    zones: dict[str, Position] | None
    registered_by: Identifier
    registered_at: AwareDatetime
    stream_url: str | None = None


class CameraProvenance(ContractModel):
    """Where a track's latest location came from. ``zone_estimate`` positions
    are the registered zone's reference point, not an observed coordinate."""

    camera_id: Identifier
    local_observation_id: Identifier
    local_track_id: Identifier
    mapping_mode: Literal["calibrated_world", "predefined_zones"]
    location_basis: Literal["calibrated_world", "zone_estimate"]
    zone_id: str | None
    calibration_ref: str | None


class TrackRecord(ContractModel):
    track_id: Identifier
    casualty_id: Identifier | None
    latest_observation: CVObservation
    latest_visible_signs: CameraSigns | None = None
    signs_observed_at: AwareDatetime | None = None
    observation_count: int
    camera: CameraProvenance | None = None


class SimulationProposal(ContractModel):
    """Advisory proposal. ``revision`` is the scene revision it was computed on;
    approval and dispatch must quote it exactly. Contains NEW assignments only."""

    mode: SimulationMode = "simulation"
    id: Identifier
    scenario_id: Identifier
    epoch: int
    revision: PositiveRevision
    status: Literal["pending_approval", "approved", "rejected", "expired", "superseded",
                    "dispatched"]
    assignments: list[Assignment]
    preserved_assignments: list[Assignment]
    unassigned_casualty_ids: list[Identifier]
    reasons: dict[str, list[str]]
    rationale: list[str]
    engine: str
    runtime_ms: float
    map_revision: str | None
    objective_values: dict[str, float]
    recommendation: RecommendationMetadata | None
    location_provenance: dict[str, CameraProvenance] = Field(default_factory=dict)
    created_at: AwareDatetime
    expires_at: AwareDatetime
    approval_id: Identifier | None = None
    dispatch_id: Identifier | None = None
    closed_reason: str | None = None


class SimulationApproval(ContractModel):
    mode: SimulationMode = "simulation"
    id: Identifier
    proposal_id: Identifier
    proposal_revision: PositiveRevision
    decision: Literal["approve", "reject"]
    approved_by: Identifier
    operator_id: Identifier
    authenticated: Literal[False] = False
    decided_at: AwareDatetime
    expires_at: AwareDatetime
    status: Literal["active", "consumed", "invalidated", "expired", "recorded"]
    invalidated_reason: str | None = None


class SimulationDispatch(ContractModel):
    mode: SimulationMode = "simulation"
    id: Identifier
    proposal_id: Identifier
    proposal_revision: PositiveRevision
    approval_id: Identifier
    status: Literal["dispatched"] = "dispatched"
    dispatched_by: Identifier
    dispatched_at: AwareDatetime
    assignment_ids: list[Identifier]
    real_device_effect: Literal[False] = False


class RouteInvalidation(ContractModel):
    """Advisory record: a dispatched route intersecting blocked map cells.

    The dispatched assignment keeps its historical ``route`` unchanged.
    ``alternative_route`` is a fresh A* suggestion on the current map revision
    (or None when infeasible); it requires a new human approval and dispatch
    and is never installed automatically."""

    mode: SimulationMode = "simulation"
    assignment_id: Identifier
    casualty_id: Identifier
    responder_id: Identifier
    map_revision: Identifier
    historical_map_revision: Identifier | None = None
    blocked_cells: list[list[int]]
    alternative_route: AStarRoute | None = None


class SimulatedAssignment(ContractModel):
    mode: SimulationMode = "simulation"
    id: Identifier
    dispatch_id: Identifier
    command_id: Identifier
    device_id: Identifier
    casualty_id: Identifier
    responder_id: Identifier
    route: AStarRoute | None
    status: Literal["dispatched", "delivered", "acknowledged", "completed"]
    created_at: AwareDatetime
    delivered_at: AwareDatetime | None = None
    acknowledged_at: AwareDatetime | None = None
    completed_at: AwareDatetime | None = None
    device_event_ids: list[Identifier] = Field(default_factory=list)
    route_status: Literal["current", "invalidated"] = "current"
    invalidated_reason: str | None = None
    invalidated_blocks: list[list[int]] | None = None


class RecommendationState(ContractModel):
    """Most recent solver outcome, including runs that produced no proposal."""

    scene_revision: int
    computed_at: AwareDatetime
    trigger: str
    proposal_id: Identifier | None
    engine: str | None
    unassigned_casualty_ids: list[Identifier]
    reasons: dict[str, list[str]]
    location_provenance: dict[str, CameraProvenance] = Field(default_factory=dict)
    location_rationale: list[str] = Field(default_factory=list)
    error: str | None = None


class SimulationState(ContractModel):
    """Authoritative snapshot (a copy). Apply only events with a greater sequence."""

    mode: SimulationMode = "simulation"
    schema_version: Literal["1.0"] = "1.0"
    scenario_id: Identifier
    epoch: int
    scene_revision: PositiveRevision
    sequence: int
    generated_at: AwareDatetime
    scenario: Scenario
    required_capabilities: dict[str, list[str]]
    tracks: list[TrackRecord]
    cameras: list[CameraRecord] = Field(default_factory=list)
    resolved_casualty_ids: list[Identifier]
    current_proposal_id: Identifier | None
    proposals: list[SimulationProposal]
    approvals: list[SimulationApproval]
    dispatches: list[SimulationDispatch]
    assignments: list[SimulatedAssignment]
    route_invalidations: list[RouteInvalidation] = Field(default_factory=list)
    last_recommendation: RecommendationState | None


class ActionResponse(ContractModel):
    """Every mutation returns its record plus the post-mutation snapshot.

    ``duplicate`` is true when an idempotent replay or deduplicated ID returned
    the originally stored result.
    """

    mode: SimulationMode = "simulation"
    action: Identifier
    duplicate: bool = False
    result: dict[str, Any] | None
    state: SimulationState


class EventEnvelope(ContractModel):
    schema_version: Literal["1.0"] = "1.0"
    mode: SimulationMode = "simulation"
    event_id: Identifier
    type: Identifier
    scenario_id: Identifier
    occurred_at: AwareDatetime
    sequence: PositiveRevision
    payload: dict[str, Any]


class EventPage(ContractModel):
    mode: SimulationMode = "simulation"
    scenario_id: Identifier
    events: list[EventEnvelope]
    latest_sequence: int
    oldest_retained_sequence: int | None
    gap: bool


ObjectivePolicyName = Literal["distance_first", "stability_first", "bounded_travel_stability"]
HalfLifeSeconds = Annotated[float, Field(gt=0, le=86400, allow_inf_nan=False)]
TravelBudgetMeters = Annotated[float, Field(ge=0, le=100000, allow_inf_nan=False)]


class IntelligenceSettings(ContractModel):
    """Opt-in per-scenario intelligence settings. Every default is legacy behavior.

    ``temporal_gate`` makes the temporal uncertainty model's ineligibility an
    extra exclusion for NEW work only; it never edits CV confidence, positions,
    or human assessments, never touches active work, and can only tighten the
    legacy gates (freshness, minimum confidence and position tolerance stay the
    store's values and are not configurable here). ``decay_gates_eligibility``
    and ``allow_zone_estimates=false`` are further tightenings; without
    ``temporal_gate`` they change diagnostics only. ``stability_preference``
    passes the previous UNAPPROVED recommendation to the solver as a soft
    preference under ``objective_policy``; count and priority always dominate
    and approved active work is always hard reserved.
    """

    temporal_gate: bool = False
    decay_gates_eligibility: bool = False
    allow_zone_estimates: bool = True
    confidence_half_life_seconds: HalfLifeSeconds = 30.0
    stability_preference: bool = False
    objective_policy: ObjectivePolicyName = "distance_first"
    stability_travel_budget_m: TravelBudgetMeters | None = None

    @model_validator(mode="after")
    def validate_objective(self) -> "IntelligenceSettings":
        if self.objective_policy != "distance_first" and not self.stability_preference:
            raise ValueError("objective_policy other than distance_first requires "
                             "stability_preference: true")
        bounded = self.objective_policy == "bounded_travel_stability"
        if bounded != (self.stability_travel_budget_m is not None):
            raise ValueError("stability_travel_budget_m is required for, and only valid with, "
                             "bounded_travel_stability")
        return self


class IntelligencePolicyRequest(IntelligenceSettings):
    """Full replacement (PUT semantics): omitted settings return to defaults."""

    mode: SimulationMode = "simulation"
    actor: Identifier


class IntelligencePolicy(IntelligenceSettings):
    """Current per-scenario policy. ``version`` 0 is the untouched default;
    reset restores it."""

    mode: SimulationMode = "simulation"
    version: int = Field(default=0, ge=0)
    updated_by: Identifier | None = None
    updated_at: AwareDatetime | None = None

    def settings(self) -> IntelligenceSettings:
        return IntelligenceSettings.model_validate(
            self.model_dump(include=set(IntelligenceSettings.model_fields)))


class LegacyThresholds(ContractModel):
    """Hard gates owned by the store; no intelligence policy can relax them."""

    freshness_seconds: float
    min_confidence: float
    position_tolerance_m: float


class TemporalUncertainty(ContractModel):
    """Per-casualty temporal evidence diagnostics at ``evaluated_at``.

    ``location_state``: observed (calibrated or human-known), estimated (zone
    reference point or occluded track), unknown (stale, future-dated or lost).
    ``location_evidence``: cv_calibrated_world, cv_zone_estimate, cv_unspecified,
    human_known, none. ``raw_confidence`` is the detector value unchanged;
    ``ranking_score`` is a heuristic (see ``interpretation``)."""

    evaluated_at: AwareDatetime
    observation_id: str | None
    track_id: str | None
    track_state: Literal["visible", "occluded", "lost"] | None
    location_state: Literal["observed", "estimated", "unknown"]
    location_evidence: Literal["cv_calibrated_world", "cv_zone_estimate", "cv_unspecified",
                               "human_known", "none"]
    last_reported_position: Position | None
    raw_confidence: float | None
    ranking_score: float | None
    report_age_seconds: float | None
    detection_age_seconds: float | None
    reasons: list[str]
    eligible_for_new_assignment: bool
    next_transition_at: AwareDatetime | None


class CasualtyIntelligence(ContractModel):
    """``exclusion_reasons`` are exactly what the temporal gate injected into
    the solver request (empty when the gate is off, the casualty is eligible,
    or it has active work). ``zone_reference_position`` is a registered zone's
    reference point, never an observed coordinate."""

    casualty_id: Identifier
    track_id: str | None
    camera: CameraProvenance | None
    zone_id: str | None
    zone_reference_position: Position | None
    active_assignment_id: Identifier | None
    uncertainty: TemporalUncertainty
    temporal_gate_applied: bool
    exclusion_reasons: list[str]
    warnings: list[str]


class ObjectiveTierView(ContractModel):
    name: str
    direction: Literal["maximize", "minimize"]
    value: float
    proven_optimal: bool


class PreviousAssignmentOutcomeView(ContractModel):
    casualty_id: str
    responder_id: str
    outcome: str
    reasons: list[str]


class ProposalReasoning(ContractModel):
    """Solver objective reasoning recorded for one proposal (copied, not recomputed).

    ``stability_budget_enforced`` is the solver's own guarantee flag: true only
    when the bounded travel stage actually ran and held the budget.
    """

    proposal_id: Identifier
    proposal_revision: PositiveRevision
    status: str
    engine: str
    objective: str | None
    objective_policy: ObjectivePolicyName
    stability_budget_enforced: bool = False
    objective_tiers: list[ObjectiveTierView]
    objective_values: dict[str, float]
    previous_assignment_outcomes: list[PreviousAssignmentOutcomeView]
    applied_assignment_exclusions: dict[str, list[str]]
    unassigned_casualty_ids: list[str]
    reasons: dict[str, list[str]]
    rationale: list[str]


class IntelligenceSnapshot(ContractModel):
    """Read-only view. ``source_valid_until`` is the freshness horizon used to cap
    proposal expiry; ``next_transition_at`` is the earliest instant at which any
    temporal reason changes by time alone (diagnostic even with the gate off)."""

    mode: SimulationMode = "simulation"
    schema_version: Literal["1.0"] = "1.0"
    scenario_id: Identifier
    epoch: int
    scene_revision: PositiveRevision
    sequence: int
    map_revision: str | None
    generated_at: AwareDatetime
    policy: IntelligencePolicy
    thresholds: LegacyThresholds
    casualties: list[CasualtyIntelligence]
    next_transition_at: AwareDatetime | None
    source_valid_until: AwareDatetime | None
    previous_recommendations: list[Assignment]
    previous_recommendation_proposal_id: Identifier | None
    proposal: ProposalReasoning | None
    warnings: list[str]
    interpretation: str = INTELLIGENCE_INTERPRETATION


class ComparisonSource(ContractModel):
    """The immutable snapshot every comparison run was computed from."""

    epoch: int
    scene_revision: PositiveRevision
    sequence: int
    map_revision: str | None
    captured_at: AwareDatetime
    source_valid_until: AwareDatetime | None
    policy: IntelligenceSettings
    policy_version: int
    active_assignment_ids: list[Identifier]
    previous_recommendation_proposal_id: Identifier | None
