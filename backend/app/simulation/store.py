import logging
"""Locked in-memory SIMULATION state. Never contacts devices or real dispatch.

Lock ordering: the store lock guards only the session table and is never
acquired while a session lock is held. Each session lock guards all of that
session's state. The solver always runs outside every lock; its result is
installed only if the scene revision and epoch are unchanged (compare-and-set).
Every returned object is a deep copy.

Time is part of the scene: the freshness classification (fresh/stale/future)
of every associated observation is a "freshness signature". Whenever a sweep
(any mutation, snapshot, tick, or replan) finds that it changed, the scene
revision bumps exactly as for any other impactful change. Proposals also expire
no later than the next freshness horizon, so an approval can never be dispatched
against observations that went stale after it was granted.
"""

import hashlib
import json
import secrets

logger = logging.getLogger(__name__)
import threading
from collections import OrderedDict
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, TypeVar

from pydantic import BaseModel

from backend.app.models import (
    Assignment,
    Casualty,
    CVObservation,
    CameraSigns,
    MedicalAssessment,
    Obstacle,
    OccupancyGrid,
    Position,
    Responder,
    Scenario,
    SceneMap,
)
from backend.app.services.scenarios import load_demo_scenario
from backend.app.simulation.events import EventLog
from backend.app.simulation.models import (
    ActionResponse,
    AssociationRequest,
    CameraObservation,
    CameraProvenance,
    CameraRecord,
    CameraRegistration,
    CasualtyIntelligence,
    ComparisonSource,
    DecisionRequest,
    DispatchRequest,
    EventEnvelope,
    EventPage,
    IntelligencePolicy,
    IntelligencePolicyRequest,
    IntelligenceSettings,
    IntelligenceSnapshot,
    LegacyThresholds,
    ObjectiveTierView,
    PreviousAssignmentOutcomeView,
    ProposalReasoning,
    RecommendationState,
    ReplanRequest,
    RequiredCapabilitiesRequest,
    ResetRequest,
    ResponderUpdateRequest,
    RouteInvalidation,
    SimulatedAssignment,
    SimulatedDeviceEvent,
    SimulateDeviceRequest,
    SimulationApproval,
    SimulationDispatch,
    SimulationProposal,
    SimulationState,
    TemporalUncertainty,
    TrackRecord,
)

Clock = Callable[[], datetime]
Solver = Callable[[Any], Any]

DEMO_SCENARIO_ID = "demo"
DEMO_MAP_REVISION = "sim-demo-map-1"
OPEN_PROPOSAL_STATUSES = ("pending_approval", "approved")
MILESTONE_RANK = {"dispatched": 0, "delivered": 1, "acknowledged": 2, "completed": 3}
MAX_IDEMPOTENCY_KEYS = 4096
EXPLICIT_REPLAN_ATTEMPTS = 3
CAMERA_PREFIX = "sim-camera:"
POSITION_TOLERANCE_M = 0.25
TEMPORAL_REASON_PREFIX = "temporal_gate:"


class SimulationError(Exception):
    def __init__(self, status_code: int, detail: str):
        super().__init__(detail)
        self.status_code = status_code
        self.detail = detail


def _not_found(detail: str) -> SimulationError:
    return SimulationError(404, detail)


def _conflict(detail: str) -> SimulationError:
    return SimulationError(409, detail)


def _invalid(detail: str) -> SimulationError:
    return SimulationError(422, detail)


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _dump(model: BaseModel) -> dict[str, Any]:
    return model.model_dump(mode="json")


_ViewT = TypeVar("_ViewT", bound=BaseModel)


def _view(model: type[_ViewT], source: BaseModel) -> _ViewT:
    return model.model_validate(source.model_dump())


def _fingerprint(*parts: Any) -> str:
    raw = json.dumps(parts, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(raw.encode()).hexdigest()


def _camera_id(camera_id: str, local_id: str) -> str:
    """Collision-free namespacing: the length prefix makes the split unambiguous."""
    return f"{CAMERA_PREFIX}{len(camera_id)}:{camera_id}:{local_id}"


def _map_fingerprint(scene_map: SceneMap) -> str:
    return _fingerprint(_dump(scene_map))


def _blocked_cells_on_route(scene_map: SceneMap, route: Any) -> list[list[int]]:
    """Sorted [col, row] cells where a historical route crosses blocked space.

    Uses the same A* grid router (occupancy + obstacle polygons) that new
    proposals use, over interpolated waypoints. Returns [] when the route or
    grid is missing or geometry cannot be evaluated.
    """
    import math

    if route is None or scene_map.occupancy_grid is None:
        return []
    grid = scene_map.occupancy_grid
    try:
        from optimization.routing import _GridRouter

        router = _GridRouter(scene_map)
    except ValueError:
        return []
    cells: set[tuple[int, int]] = set()
    try:
        for first, second in zip(route.waypoints, route.waypoints[1:]):
            distance = math.hypot(second.x_m - first.x_m, second.y_m - first.y_m)
            steps = max(1, math.ceil(distance / (grid.resolution_m / 4)))
            for step in range(steps + 1):
                fraction = step / steps
                x = first.x_m + fraction * (second.x_m - first.x_m)
                y = first.y_m + fraction * (second.y_m - first.y_m)
                cells.add((math.floor((x - grid.origin.x_m) / grid.resolution_m),
                           math.floor((y - grid.origin.y_m) / grid.resolution_m)))
    except (ArithmeticError, ValueError):
        return []
    return [[col, row] for col, row in sorted(
        cell for cell in cells if router._is_blocked(cell))]


def _advisory_alternative(scene_map: SceneMap, start: Any, goal: Any) -> Any | None:
    """Fresh A* route on the current map, or None when infeasible/invalid."""
    try:
        from optimization.routing import plan_route
    except ImportError:
        return None
    try:
        return plan_route(scene_map, start, goal)
    except ValueError:
        return None


def _default_solver(request: Any) -> Any:
    from optimization.solver import optimize

    return optimize(request)


def demo_scene_map(frame_id: str) -> SceneMap:
    """Known synthetic 24x20 grid at 1 m: free except one wall at x=8..9, y=2..13."""
    width, height = 24, 20
    cells = [0] * (width * height)
    for row in range(2, 13):
        cells[row * width + 8] = 100

    def point(x: float, y: float) -> Position:
        return Position(frame_id=frame_id, x_m=x, y_m=y)

    return SceneMap(
        frame_id=frame_id, revision=DEMO_MAP_REVISION,
        obstacles=[Obstacle(id="sim-wall-a",
                            vertices=[point(8, 2), point(9, 2), point(9, 13), point(8, 13)])],
        occupancy_grid=OccupancyGrid(origin=point(0, 0), resolution_m=1.0,
                                     width=width, height=height, cells=cells),
    )


@dataclass
class _Track:
    track_id: str
    latest: CVObservation
    count: int = 1
    casualty_id: str | None = None
    camera: CameraProvenance | None = None
    latest_visible_signs: CameraSigns | None = None
    signs_observed_at: datetime | None = None


@dataclass
class _Idempotent:
    fingerprint: str
    response: ActionResponse | None


@dataclass
class _SolverJob:
    epoch: int
    revision: int
    trigger: str
    request: Any
    now: datetime
    location_provenance: dict[str, CameraProvenance] = field(default_factory=dict)

    def location_rationale(self) -> list[str]:
        notes = []
        for casualty_id, source in sorted(self.location_provenance.items()):
            if source.location_basis == "zone_estimate":
                notes.append(
                    f"At scene revision {self.revision}, {casualty_id} location is a zone_estimate: "
                    f"the registered reference point of zone {source.zone_id} for SIMULATION camera "
                    f"{source.camera_id} (local track {source.local_track_id}). It is not a precise "
                    "observed coordinate; route distance is measured to that reference point.")
            else:
                notes.append(
                    f"At scene revision {self.revision}, {casualty_id} location comes from SIMULATION "
                    f"camera {source.camera_id} (local track {source.local_track_id}) under "
                    f"calibrated_world mapping. calibration_ref {source.calibration_ref} is an "
                    "external assertion; the backend does not compute or verify calibration.")
        return notes


@dataclass
class _Session:
    scenario_id: str
    events: EventLog
    lock: threading.RLock = field(default_factory=threading.RLock)
    epoch: int = 0
    scene_revision: int = 0
    counters: dict[str, int] = field(default_factory=dict)
    idempotency: "OrderedDict[str, _Idempotent]" = field(default_factory=OrderedDict)
    meta: Scenario | None = None
    casualties: dict[str, Casualty] = field(default_factory=dict)
    responders: dict[str, Responder] = field(default_factory=dict)
    scene_map: SceneMap | None = None
    required_capabilities: dict[str, list[str]] = field(default_factory=dict)
    tracks: dict[str, _Track] = field(default_factory=dict)
    casualty_tracks: dict[str, str] = field(default_factory=dict)
    observation_fingerprints: dict[str, str] = field(default_factory=dict)
    responder_reported_at: dict[str, datetime] = field(default_factory=dict)
    responder_preferences: dict[str, str] = field(default_factory=dict)
    resolved: set[str] = field(default_factory=set)
    proposals: dict[str, SimulationProposal] = field(default_factory=dict)
    proposal_context: dict[str, tuple[Scenario, Any]] = field(default_factory=dict)
    current_proposal_id: str | None = None
    approvals: dict[str, SimulationApproval] = field(default_factory=dict)
    dispatches: dict[str, SimulationDispatch] = field(default_factory=dict)
    assignments: dict[str, SimulatedAssignment] = field(default_factory=dict)
    route_invalidations: list[RouteInvalidation] = field(default_factory=list)
    device_events: dict[str, SimulatedDeviceEvent] = field(default_factory=dict)
    map_revisions: dict[str, str] = field(default_factory=dict)
    cameras: dict[str, CameraRecord] = field(default_factory=dict)
    freshness_signature: tuple = ()
    last_recommendation: RecommendationState | None = None
    intelligence_policy: IntelligencePolicy = field(default_factory=IntelligencePolicy)
    previous_recommendations: list[Assignment] = field(default_factory=list)
    previous_recommendation_proposal_id: str | None = None
    neural_comparisons: dict[str, Any] = field(default_factory=dict)
    current_neural_comparison: Any | None = None

    def next_id(self, kind: str) -> str:
        self.counters[kind] = self.counters.get(kind, 0) + 1
        return f"sim-{kind}-{self.counters[kind]:04d}"

    def emit(self, event_type: str, payload: dict[str, Any], now: datetime) -> None:
        self.events.append(event_type, payload, now)

    def active_assignments(self) -> list[SimulatedAssignment]:
        return [item for item in self.assignments.values() if item.status != "completed"]

    def scenario(self, *, include_resolved: bool = True) -> Scenario:
        assert self.meta is not None
        casualties = [casualty.model_copy(deep=True) for casualty in self.casualties.values()
                      if include_resolved or casualty.id not in self.resolved]
        return Scenario(
            schema_version="1.0", id=self.meta.id, name=self.meta.name, synthetic=True,
            frame_id=self.meta.frame_id, created_at=self.meta.created_at,
            casualties=casualties,
            responders=[responder.model_copy(deep=True) for responder in self.responders.values()],
            scene_map=self.scene_map.model_copy(deep=True) if self.scene_map else None,
        )


class SimulationStore:
    """Authoritative SIMULATION state for all scenarios in this process.

    ``clock`` and ``solver`` are injectable for deterministic tests. The solver
    receives an ``optimization.models.OptimizationRequest`` and must return an
    ``OptimizationResult``; only assignments for casualties/responders without
    active work are treated as new proposals (preserved work is never re-proposed).
    """

    def __init__(
        self,
        *,
        clock: Clock | None = None,
        solver: Solver | None = None,
        solver_name: str = "ortools",
        auto_recommend: bool = True,
        proposal_ttl_seconds: float = 120.0,
        freshness_seconds: float = 30.0,
        min_confidence: float = 0.5,
        clock_skew_seconds: float = 5.0,
        max_events: int = 1000,
        neural_shadow_enabled: bool | None = None,
        neural_shadow_service: Any | None = None,
    ):
        self.clock = clock or _utcnow
        self.solver = solver or _default_solver
        self.solver_name = solver_name
        self.auto_recommend = auto_recommend
        self.proposal_ttl = timedelta(seconds=proposal_ttl_seconds)
        self.freshness_seconds = freshness_seconds
        self.min_confidence = min_confidence
        self.skew = timedelta(seconds=clock_skew_seconds)
        self.max_events = max_events
        self._lock = threading.Lock()
        self._sessions: dict[str, _Session] = {}
        self._nonce = secrets.token_hex(4)
        if neural_shadow_enabled is None:
            from backend.app.intelligence.neural_shadow import is_neural_shadow_enabled

            self.neural_shadow_enabled = is_neural_shadow_enabled()
        else:
            self.neural_shadow_enabled = neural_shadow_enabled
        self.neural_shadow_service = neural_shadow_service


    def _now(self) -> datetime:
        now = self.clock()
        if now.tzinfo is None:
            raise RuntimeError("Simulation clock must return timezone-aware datetimes")
        return now

    def _get_or_create(self, scenario_id: str) -> _Session:
        with self._lock:
            session = self._sessions.get(scenario_id)
            if session is None:
                session = _Session(scenario_id=scenario_id,
                                   events=EventLog(scenario_id, self._nonce, self.max_events))
                self._sessions[scenario_id] = session
            return session

    def _session(self, scenario_id: str) -> _Session:
        """Existing loaded session; ``demo`` is loaded lazily on first use."""
        with self._lock:
            session = self._sessions.get(scenario_id)
        if session is not None and session.meta is not None:
            return session
        if scenario_id != DEMO_SCENARIO_ID:
            raise _not_found(f"Simulation {scenario_id} is not loaded; POST its reset first")
        self.reset(scenario_id, ResetRequest(), only_if_unloaded=True)
        return self._get_or_create(scenario_id)


    def tick(self, scenario_id: str) -> None:
        """Apply time-driven transitions (expiry, observation freshness) and replan once."""
        session = self._session(scenario_id)
        with session.lock:
            now = self._now()
            job = None
            if self._sweep(session, now) and self.auto_recommend:
                job = self._prepare(session, now, "observation_freshness_changed")
        if job is not None:
            self._execute(session, job)

    def snapshot(self, scenario_id: str) -> SimulationState:
        self.tick(scenario_id)
        session = self._session(scenario_id)
        with session.lock:
            return self._state(session, self._now())

    def snapshot_envelope(self, scenario_id: str) -> EventEnvelope:
        """Resync message: not stored; ``sequence`` is the last included event."""
        state = self.snapshot(scenario_id)
        return EventEnvelope(
            event_id=f"sim-{scenario_id}-{self._nonce}-snapshot-{secrets.token_hex(6)}",
            type="simulation.snapshot", scenario_id=scenario_id,
            occurred_at=state.generated_at, sequence=max(state.sequence, 1),
            payload=_dump(state),
        )

    def events(self, scenario_id: str, after_sequence: int = 0, limit: int = 200) -> EventPage:
        session = self._session(scenario_id)
        with session.lock:
            events, gap = session.events.after(after_sequence, limit)
            return EventPage(scenario_id=scenario_id, events=events,
                             latest_sequence=session.events.sequence,
                             oldest_retained_sequence=session.events.oldest_sequence, gap=gap)

    def explanation(self, scenario_id: str, proposal_id: str) -> dict[str, Any]:
        session = self._session(scenario_id)
        with session.lock:
            context = session.proposal_context.get(proposal_id)
            if context is None:
                raise _not_found(f"Unknown proposal {proposal_id}")
            proposal = session.proposals[proposal_id].model_copy(deep=True)
            scenario, result = context
            scenario, result = scenario.model_copy(deep=True), result.model_copy(deep=True)
        from backend.app.services.advisory import explain, explanation_input, selector_from_env

        explanation = explain(explanation_input(scenario, result), selector_from_env())
        return {"mode": "simulation", "proposal_id": proposal.id,
                "proposal_revision": proposal.revision, "proposal_status": proposal.status,
                "explanation": _dump(explanation)}

    def intelligence(self, scenario_id: str) -> IntelligenceSnapshot:
        """Read-only temporal and objective diagnostics after the normal freshness tick."""
        self.tick(scenario_id)
        session = self._session(scenario_id)
        with session.lock:
            now = self._now()
            policy = session.intelligence_policy
            reports = self._temporal_reports(session, now, policy)
            exclusions = self._temporal_exclusions(session, reports) if policy.temporal_gate else {}
            active = {item.casualty_id: item.id for item in session.active_assignments()}
            casualties = []
            warnings = []
            if policy.decay_gates_eligibility and not policy.temporal_gate:
                warnings.append("decay_gates_eligibility changes diagnostics only while "
                                "temporal_gate is off")
            for casualty_id, report in reports.items():
                track_id = session.casualty_tracks.get(casualty_id)
                camera = session.tracks[track_id].camera if track_id is not None else None
                zone = camera is not None and camera.location_basis == "zone_estimate"
                notes = []
                if casualty_id in active and not report.eligible_for_new_assignment:
                    notes.append(f"active assignment {active[casualty_id]} is preserved; temporal "
                                 f"reasons {', '.join(report.reasons)} gate new work only")
                elif not report.eligible_for_new_assignment and not policy.temporal_gate:
                    notes.append("temporal_gate is off: temporal reasons are diagnostic only; "
                                 "legacy solver gates still apply")
                if zone:
                    notes.append(f"location is the registered reference point of zone "
                                 f"{camera.zone_id} on camera {camera.camera_id}, not an observed "
                                 "coordinate")
                warnings.extend(f"{casualty_id}: {note}" for note in notes)
                casualties.append(CasualtyIntelligence(
                    casualty_id=casualty_id, track_id=track_id,
                    camera=camera.model_copy() if camera is not None else None,
                    zone_id=camera.zone_id if zone else None,
                    zone_reference_position=report.last_reported_position if zone else None,
                    active_assignment_id=active.get(casualty_id),
                    uncertainty=_view(TemporalUncertainty, report),
                    temporal_gate_applied=casualty_id in exclusions,
                    exclusion_reasons=exclusions.get(casualty_id, []),
                    warnings=notes,
                ))
            transitions = [report.next_transition_at for report in reports.values()
                           if report.next_transition_at is not None]
            return IntelligenceSnapshot(
                scenario_id=session.scenario_id, epoch=session.epoch,
                scene_revision=session.scene_revision, sequence=session.events.sequence,
                map_revision=session.scene_map.revision if session.scene_map else None,
                generated_at=now, policy=policy.model_copy(deep=True),
                thresholds=LegacyThresholds(freshness_seconds=self.freshness_seconds,
                                            min_confidence=self.min_confidence,
                                            position_tolerance_m=POSITION_TOLERANCE_M),
                casualties=casualties,
                next_transition_at=min(transitions) if transitions else None,
                source_valid_until=self._freshness_horizon(session, now),
                previous_recommendations=self._stability_source(session),
                previous_recommendation_proposal_id=(
                    session.current_proposal_id
                    if session.current_proposal_id is not None
                    and session.proposals[session.current_proposal_id].status == "pending_approval"
                    else session.previous_recommendation_proposal_id
                    if session.current_proposal_id is None else None),
                proposal=self._reasoning(session), warnings=warnings,
            )

    @staticmethod
    def _reasoning(session: _Session) -> ProposalReasoning | None:
        proposal_id = session.current_proposal_id or next(reversed(session.proposal_context), None)
        if proposal_id is None:
            return None
        proposal = session.proposals[proposal_id]
        _, result = session.proposal_context[proposal_id]
        return ProposalReasoning(
            proposal_id=proposal.id, proposal_revision=proposal.revision, status=proposal.status,
            engine=result.engine, objective=result.metadata.objective,
            objective_policy=getattr(result, "objective_policy", "distance_first"),
            stability_budget_enforced=getattr(result, "stability_budget_enforced", False),
            objective_tiers=[_view(ObjectiveTierView, tier)
                             for tier in getattr(result, "objective_tiers", [])],
            objective_values=dict(result.objective_values),
            previous_assignment_outcomes=[
                _view(PreviousAssignmentOutcomeView, outcome)
                for outcome in getattr(result, "previous_assignment_outcomes", [])],
            applied_assignment_exclusions={
                key: list(value)
                for key, value in getattr(result, "applied_assignment_exclusions", {}).items()},
            unassigned_casualty_ids=list(proposal.unassigned_casualty_ids),
            reasons={key: list(value) for key, value in proposal.reasons.items()},
            rationale=list(proposal.rationale),
        )

    def set_intelligence_policy(self, scenario_id: str, request: IntelligencePolicyRequest,
                                idempotency_key: str | None = None) -> ActionResponse:
        """Replace the scenario's intelligence policy.

        An actual change bumps the scene revision (superseding open proposals and
        invalidating unconsumed approvals), emits
        ``simulation.intelligence_policy_updated``, then runs the normal bounded
        auto-replan. An identical policy is a duplicate with no effect.
        """
        session = self._session(scenario_id)
        settings = IntelligenceSettings.model_validate(
            request.model_dump(include=set(IntelligenceSettings.model_fields)))

        def apply(now: datetime):
            current = session.intelligence_policy
            if current.settings() == settings:
                return {"policy": _dump(current), "changed": False}, None, True
            updated = IntelligencePolicy(**settings.model_dump(), version=current.version + 1,
                                         updated_by=request.actor, updated_at=now)
            session.intelligence_policy = updated
            payload = {"policy": _dump(updated), "previous_policy": _dump(current),
                       "actor": request.actor, "scene_revision": session.scene_revision + 1}
            session.emit("simulation.intelligence_policy_updated", payload, now)
            return {**payload, "changed": True}, f"intelligence_policy:{updated.version}", False

        return self._run(session, "intelligence_policy", _dump(request), apply, idempotency_key)

    def compare(self, scenario_id: str, request: Any) -> Any:
        """Read-only counterfactuals on one captured request; installs nothing.

        No tick runs here, so no sweep, event, revision, or proposal can occur
        even when time already crossed a freshness boundary: the base request is
        captured detached at the current instant with its horizon (never built
        by ``_prepare``), the solver runs unlocked on detached copies, and the
        result is labelled stale if the epoch or revision changed or
        ``source_valid_until`` passed while solving.
        """
        from pydantic import ValidationError

        from backend.app.simulation.comparisons import ComparisonReport
        from optimization.analysis import (
            AnalysisInputError, CounterfactualRequest, analyze_counterfactuals,
        )

        session = self._session(scenario_id)
        with session.lock:
            now = self._now()
            policy = session.intelligence_policy
            settings = request.policy or policy.settings()
            base, _ = self._build_request(session, now, request.solver, settings)
            horizons = [self._freshness_horizon(session, now)]
            if settings.temporal_gate:
                horizons.append(self._temporal_horizon(session, now, settings))
            if settings.stability_preference:
                horizons.extend(proposal.expires_at for proposal in session.proposals.values()
                                if proposal.status in OPEN_PROPOSAL_STATUSES)
            instants = [instant for instant in horizons if instant is not None]
            valid_until = min(instants) if instants else None
            source = ComparisonSource(
                epoch=session.epoch, scene_revision=session.scene_revision,
                sequence=session.events.sequence,
                map_revision=session.scene_map.revision if session.scene_map else None,
                captured_at=now, source_valid_until=valid_until, policy=settings,
                policy_version=policy.version,
                active_assignment_ids=sorted(item.id for item in session.active_assignments()),
                previous_recommendation_proposal_id=session.previous_recommendation_proposal_id,
            )
        try:
            counterfactual = CounterfactualRequest(base=base, scenarios=request.scenarios,
                                                   expires_at=valid_until)
            analysis = analyze_counterfactuals(counterfactual)
        except (AnalysisInputError, ValidationError) as error:
            raise _invalid(str(error)) from None
        with session.lock:
            evaluated = self._now()
            stale = []
            if session.epoch != source.epoch:
                stale.append("epoch_changed")
            if session.scene_revision != source.scene_revision:
                stale.append("scene_revision_changed")
            if valid_until is not None and evaluated >= valid_until:
                stale.append("source_validity_horizon_reached")
        return ComparisonReport(
            scenario_id=scenario_id, source=source, evaluated_at=evaluated,
            context="stale" if stale else "current", stale_reasons=stale, analysis=analysis)

    def evaluations(self, scenario_id: str) -> Any:
        """Committed synthetic evaluation artifacts; requires a loaded scenario."""
        from backend.app.services.evaluation_reports import evaluation_index

        self._session(scenario_id)
        return evaluation_index()

    def neural_comparison(self, scenario_id: str) -> Any:
        """Read-only live shadow comparison (OR-Tools vs Pairwise MLP vs Bipartite GNN)."""
        from backend.app.intelligence.neural_shadow import (
            NeuralComparisonReport,
            get_neural_shadow_service,
        )

        session = self._session(scenario_id)
        with session.lock:
            now = self._now()
            if not self.neural_shadow_enabled:
                return NeuralComparisonReport(
                    scenario_id=scenario_id,
                    scene_revision=session.scene_revision,
                    epoch=session.epoch,
                    available=False,
                    status="disabled",
                    context="current",
                    evaluated_at=now,
                    message=(
                        "Neural shadow advisory is disabled (AEGIS_NEURAL_SHADOW_ENABLED=false). "
                        "OR-Tools CP-SAT is the authoritative optimization engine."
                    ),
                )

            proposal_id = session.current_proposal_id
            if proposal_id is None and session.current_neural_comparison is not None:
                comp = session.current_neural_comparison
                stale_reasons = []
                if session.epoch != comp.epoch:
                    stale_reasons.append("epoch_changed")
                if session.scene_revision != comp.scene_revision:
                    stale_reasons.append("scene_revision_changed")
                stale_reasons.append("proposal_superseded")
                return comp.model_copy(
                    update={
                        "context": "stale",
                        "status": "stale",
                        "stale_reasons": stale_reasons,
                        "evaluated_at": now,
                    }
                )

            if proposal_id is None or proposal_id not in session.proposals:
                return NeuralComparisonReport(
                    scenario_id=scenario_id,
                    scene_revision=session.scene_revision,
                    epoch=session.epoch,
                    available=False,
                    status="no_proposal",
                    context="current",
                    evaluated_at=now,
                    message="No optimization proposal has been generated for this scenario yet.",
                )

            proposal = session.proposals[proposal_id]
            comparison = session.neural_comparisons.get(proposal_id)

            if comparison is None:
                context_entry = session.proposal_context.get(proposal_id)
                if context_entry is not None:
                    scenario, ortools_result = context_entry
                    req, _ = self._build_request(session, now, self.solver_name, session.intelligence_policy)
                    req = req.model_copy(update={"scenario": scenario, "scene_revision": proposal.revision})
                    service = self.neural_shadow_service or get_neural_shadow_service()
                    comparison = service.run_inference(
                        req,
                        ortools_assignments=getattr(ortools_result, "assignments", []),
                        ortools_runtime_ms=getattr(ortools_result, "runtime_ms", 0.0),
                        epoch=proposal.epoch,
                        revision=proposal.revision,
                        proposal_id=proposal_id,
                    )
                    session.neural_comparisons[proposal_id] = comparison
                    session.current_neural_comparison = comparison
                else:
                    return NeuralComparisonReport(
                        scenario_id=scenario_id,
                        scene_revision=session.scene_revision,
                        epoch=session.epoch,
                        proposal_id=proposal_id,
                        available=False,
                        status="inference_failed",
                        context="current",
                        evaluated_at=now,
                        message="Neural comparison could not be derived for the current proposal.",
                    )

            stale_reasons: list[str] = []
            if session.epoch != comparison.epoch:
                stale_reasons.append("epoch_changed")
            if session.scene_revision != comparison.scene_revision:
                stale_reasons.append("scene_revision_changed")
            if proposal.status not in OPEN_PROPOSAL_STATUSES:
                stale_reasons.append(f"proposal_{proposal.status}")

            if stale_reasons:
                return comparison.model_copy(
                    update={
                        "context": "stale",
                        "status": "stale",
                        "stale_reasons": stale_reasons,
                        "evaluated_at": now,
                    }
                )

            return comparison.model_copy(update={"context": "current", "evaluated_at": now})


    def _run(self, session: _Session, action: str, body: Any,
             apply: Callable[[datetime], tuple[dict[str, Any] | None, str | None, bool]],
             idempotency_key: str | None = None, *, replan: bool = True) -> ActionResponse:
        """Apply under lock; ``apply`` returns (result, impact_cause, duplicate).

        A non-null impact cause bumps the scene revision, supersedes open
        proposals and invalidates unconsumed approvals, then auto-replans once.
        """
        fingerprint = _fingerprint(action, body)
        job = None
        failure: SimulationError | None = None
        response = None
        with session.lock:
            replay = self._replay(session, idempotency_key, fingerprint)
            if replay is not None:
                return replay
            now = self._now()
            swept = self._sweep(session, now)
            cause = None
            try:
                result, cause, duplicate = apply(now)
            except SimulationError as error:
                failure = error
            if failure is None:
                signature = self._freshness_signature(session, now)
                if signature != session.freshness_signature:
                    session.freshness_signature = signature
                    cause = cause or "observation_freshness_changed"
                if cause is not None:
                    self._bump(session, cause, now)
            if (swept or cause is not None) and replan and self.auto_recommend:
                job = self._prepare(session, now, cause or "observation_freshness_changed")
            if failure is None:
                response = ActionResponse(action=action, duplicate=duplicate, result=result,
                                          state=self._state(session, now))
                if idempotency_key is not None:
                    self._remember(session, idempotency_key, fingerprint, response)
        if job is not None:
            self._execute(session, job)
        if failure is not None:
            raise failure
        assert response is not None
        if job is not None:
            with session.lock:
                response = response.model_copy(
                    update={"state": self._state(session, self._now())}, deep=True)
                if idempotency_key is not None:
                    self._remember(session, idempotency_key, fingerprint, response)
        return response

    @staticmethod
    def _replay(session: _Session, key: str | None, fingerprint: str) -> ActionResponse | None:
        """Stored response for a reused key; 409 for a different body or in-flight key."""
        if key is None:
            return None
        stored = session.idempotency.get(key)
        if stored is None:
            return None
        if stored.fingerprint != fingerprint:
            raise _conflict("Idempotency-Key was already used with a different request")
        if stored.response is None:
            raise _conflict("A request with this Idempotency-Key is still in progress; retry later")
        return stored.response.model_copy(update={"duplicate": True}, deep=True)

    @staticmethod
    def _remember(session: _Session, key: str, fingerprint: str,
                  response: ActionResponse | None) -> None:
        session.idempotency[key] = _Idempotent(
            fingerprint, response.model_copy(deep=True) if response is not None else None)
        session.idempotency.move_to_end(key)
        while len(session.idempotency) > MAX_IDEMPOTENCY_KEYS:
            session.idempotency.popitem(last=False)

    def _bump(self, session: _Session, cause: str, now: datetime) -> None:
        session.scene_revision += 1
        for proposal in session.proposals.values():
            if proposal.status in OPEN_PROPOSAL_STATUSES:
                self._close(session, proposal, "superseded", cause, now)
        session.current_proposal_id = None

    def _close(self, session: _Session, proposal: SimulationProposal, status: str,
               reason: str, now: datetime) -> None:
        """Close an open proposal. A still-unapproved one is kept as the
        continuity source for opt-in stability; anything else clears it."""
        if proposal.status == "pending_approval":
            session.previous_recommendations = [
                Assignment(casualty_id=item.casualty_id, responder_id=item.responder_id)
                for item in proposal.assignments]
            session.previous_recommendation_proposal_id = proposal.id
        else:
            self._forget_previous(session)
        proposal.status = status
        proposal.closed_reason = reason
        session.emit(f"simulation.proposal_{status}",
                     {"proposal_id": proposal.id, "proposal_revision": proposal.revision,
                      "reason": reason}, now)
        approval = session.approvals.get(proposal.approval_id or "")
        if approval is not None and approval.status == "active":
            approval.status = "expired" if status == "expired" else "invalidated"
            approval.invalidated_reason = reason
            session.emit("simulation.approval_invalidated",
                         {"approval_id": approval.id, "proposal_id": proposal.id,
                          "status": approval.status, "reason": reason}, now)
        if session.current_proposal_id == proposal.id:
            session.current_proposal_id = None

    def _sweep(self, session: _Session, now: datetime) -> bool:
        """Time-driven transitions. Returns True if the scene revision bumped."""
        if session.meta is None:
            return False
        bumped = False
        signature = self._freshness_signature(session, now)
        if signature != session.freshness_signature:
            session.freshness_signature = signature
            self._bump(session, "observation_freshness_changed", now)
            bumped = True
        for proposal in session.proposals.values():
            if proposal.status in OPEN_PROPOSAL_STATUSES and now >= proposal.expires_at:
                reason = ("freshness_horizon_reached"
                          if proposal.expires_at < proposal.created_at + self.proposal_ttl
                          else "proposal_ttl_elapsed")
                self._close(session, proposal, "expired", reason, now)
        return bumped

    def _associated_observations(self, session: _Session) -> list[tuple[str, CVObservation]]:
        return [(casualty_id, session.tracks[track_id].latest)
                for casualty_id, track_id in sorted(session.casualty_tracks.items())
                if casualty_id not in session.resolved]

    def _classify(self, instant: datetime, now: datetime) -> str:
        """Mirrors the solver: age < 0 future, age > freshness stale."""
        age = (now - instant).total_seconds()
        return "future" if age < 0 else "stale" if age > self.freshness_seconds else "fresh"

    def _future_assessments(self, session: _Session) -> list[tuple[str, datetime]]:
        return [(casualty.id, casualty.medical_assessment.assessed_at)
                for casualty in sorted(session.casualties.values(), key=lambda c: c.id)
                if casualty.medical_assessment is not None and casualty.id not in session.resolved]

    def _freshness_signature(self, session: _Session, now: datetime) -> tuple:
        signature = []
        for casualty_id, observation in self._associated_observations(session):
            entry = ["observation", casualty_id, self._classify(observation.observed_at, now)]
            if observation.track is not None:
                entry.append(self._classify(observation.track.last_seen_at, now))
            signature.append(tuple(entry))
        for casualty_id, assessed_at in self._future_assessments(session):
            signature.append(("assessment", casualty_id,
                              "future" if assessed_at > now else "effective"))
        settings = session.intelligence_policy
        if settings.temporal_gate:
            exclusions = self._temporal_exclusions(
                session, self._temporal_reports(session, now, settings))
            signature.extend(("temporal", casualty_id, tuple(reasons))
                             for casualty_id, reasons in exclusions.items())
        return tuple(signature)

    def _freshness_horizon(self, session: _Session, now: datetime) -> datetime | None:
        """Earliest future instant at which any freshness classification can change."""
        window = timedelta(seconds=self.freshness_seconds)
        instants = []
        for _, observation in self._associated_observations(session):
            stamps = [observation.observed_at]
            if observation.track is not None:
                stamps.append(observation.track.last_seen_at)
            instants.extend(stamp + offset for stamp in stamps for offset in (timedelta(0), window))
        instants.extend(assessed_at for _, assessed_at in self._future_assessments(session))
        if session.intelligence_policy.temporal_gate:
            temporal = self._temporal_horizon(session, now, session.intelligence_policy)
            if temporal is not None:
                instants.append(temporal)
        upcoming = [instant for instant in instants if instant > now]
        return min(upcoming) if upcoming else None

    @staticmethod
    def _forget_previous(session: _Session) -> None:
        session.previous_recommendations = []
        session.previous_recommendation_proposal_id = None

    def _uncertainty_policy(self, settings: IntelligenceSettings) -> Any:
        from backend.app.intelligence.uncertainty import UncertaintyPolicy

        return UncertaintyPolicy(
            freshness_seconds=self.freshness_seconds, min_confidence=self.min_confidence,
            position_tolerance_m=POSITION_TOLERANCE_M,
            confidence_half_life_seconds=settings.confidence_half_life_seconds,
            decay_gates_eligibility=settings.decay_gates_eligibility,
            allow_zone_estimates=settings.allow_zone_estimates,
        )

    def _temporal_reports(self, session: _Session, now: datetime,
                          settings: IntelligenceSettings) -> dict[str, Any]:
        """Pure temporal evaluation of every unresolved casualty; mutates nothing.

        A casualty without an associated track is evaluated from its human-known
        scenario position: absence of CV evidence is never inferred as unknown.
        """
        from backend.app.intelligence.uncertainty import evaluate_uncertainty

        policy = self._uncertainty_policy(settings)
        reports = {}
        for casualty_id in sorted(session.casualties):
            if casualty_id in session.resolved:
                continue
            track_id = session.casualty_tracks.get(casualty_id)
            record = session.tracks.get(track_id) if track_id is not None else None
            reports[casualty_id] = evaluate_uncertainty(
                record.latest if record is not None else None, now, policy,
                location_basis=record.camera.location_basis
                if record is not None and record.camera is not None else None,
                authoritative_position=session.casualties[casualty_id].position,
            )
        return reports

    @staticmethod
    def _temporal_exclusions(session: _Session, reports: dict[str, Any]) -> dict[str, list[str]]:
        """Prefixed reasons for ineligible casualties without active work."""
        active = {item.casualty_id for item in session.active_assignments()}
        return {casualty_id: [f"{TEMPORAL_REASON_PREFIX}{reason}" for reason in report.reasons]
                for casualty_id, report in sorted(reports.items())
                if not report.eligible_for_new_assignment and casualty_id not in active}

    def _temporal_horizon(self, session: _Session, now: datetime,
                          settings: IntelligenceSettings) -> datetime | None:
        """Earliest time-only change of a gate-relevant temporal reason."""
        active = {item.casualty_id for item in session.active_assignments()}
        instants = [report.next_transition_at
                    for casualty_id, report in self._temporal_reports(session, now, settings).items()
                    if casualty_id not in active and report.next_transition_at is not None
                    and report.next_transition_at > now]
        return min(instants) if instants else None

    @staticmethod
    def _stability_source(session: _Session) -> list[Assignment]:
        """Previous UNAPPROVED recommendation: the open pending proposal if any,
        otherwise the last pending proposal closed without a decision."""
        current = session.proposals.get(session.current_proposal_id or "")
        if current is not None and current.status == "pending_approval":
            return [Assignment(casualty_id=item.casualty_id, responder_id=item.responder_id)
                    for item in current.assignments]
        if current is not None:
            return []
        return [item.model_copy(deep=True) for item in session.previous_recommendations]

    def _build_request(self, session: _Session, now: datetime, solver_name: str | None,
                       settings: IntelligenceSettings) -> tuple[Any, dict[str, CameraProvenance]]:
        """Detached solver request for the current state. No events, no mutation.

        Intelligence fields are set only when a setting is enabled, so the
        default request is identical to the legacy one.
        """
        from optimization.models import OptimizationRequest

        scenario = session.scenario(include_resolved=False)
        present = {casualty.id for casualty in scenario.casualties}
        active = [Assignment(casualty_id=item.casualty_id, responder_id=item.responder_id,
                             route=item.route.model_copy(deep=True) if item.route else None)
                  for item in session.active_assignments() if item.casualty_id in present]
        observations = {
            casualty_id: session.tracks[track_id].latest.model_copy(deep=True)
            for casualty_id, track_id in session.casualty_tracks.items() if casualty_id in present
        }
        extra: dict[str, Any] = {}
        if settings.temporal_gate:
            exclusions = self._temporal_exclusions(
                session, self._temporal_reports(session, now, settings))
            if exclusions:
                extra["assignment_exclusions"] = exclusions
        if settings.stability_preference:
            extra["objective_policy"] = settings.objective_policy
            if settings.stability_travel_budget_m is not None:
                extra["stability_travel_budget_m"] = settings.stability_travel_budget_m
            previous = [item for item in self._stability_source(session)
                        if item.casualty_id in present and item.responder_id in session.responders]
            if previous:
                extra["previous_assignments"] = previous
        request = OptimizationRequest(
            scenario=scenario,
            required_capabilities={cid: list(tags) for cid, tags in session.required_capabilities.items()
                                   if cid in present and tags},
            active_assignments=active, observations=observations,
            scene_revision=session.scene_revision, now=now,
            solver=solver_name or self.solver_name,
            freshness_seconds=self.freshness_seconds, min_confidence=self.min_confidence,
            **extra,
        )
        provenance = {
            casualty_id: session.tracks[track_id].camera.model_copy()
            for casualty_id, track_id in session.casualty_tracks.items()
            if casualty_id in present and session.tracks[track_id].camera is not None
        }
        return request, provenance


    def _prepare(self, session: _Session, now: datetime, trigger: str,
                 solver_name: str | None = None) -> _SolverJob | None:
        request, provenance = self._build_request(session, now, solver_name,
                                                  session.intelligence_policy)
        session.emit("simulation.replan_requested",
                     {"trigger": trigger, "scene_revision": session.scene_revision,
                      "actor": "system" if not trigger.startswith("explicit:") else trigger[9:]},
                     now)
        return _SolverJob(session.epoch, session.scene_revision, trigger, request, now, provenance)

    def _execute(self, session: _Session, job: _SolverJob) -> SimulationProposal | RecommendationState | None:
        """Run the solver unlocked; install only if the scene is unchanged.

        If time alone changed observation freshness during the solve, the sweep
        bumps the revision and the solve is retried a bounded number of times.
        A stale result caused by another mutation is discarded: that mutation
        schedules its own replan.
        """
        for _ in range(EXPLICIT_REPLAN_ATTEMPTS):
            error = None
            result = None
            try:
                result = self.solver(job.request.model_copy(deep=True))
            except Exception as exc:
                error = f"{type(exc).__name__}: {exc}"

            shadow_comparison = None
            if self.neural_shadow_enabled and result is not None and error is None:
                try:
                    from backend.app.intelligence.neural_shadow import get_neural_shadow_service

                    service = self.neural_shadow_service or get_neural_shadow_service()
                    shadow_comparison = service.run_inference(
                        job.request.model_copy(deep=True),
                        ortools_assignments=getattr(result, "assignments", []),
                        ortools_runtime_ms=getattr(result, "runtime_ms", 0.0),
                        epoch=job.epoch,
                        revision=job.revision,
                    )
                except Exception as exc:
                    logger.warning("Neural shadow inference failed: %s: %s", type(exc).__name__, exc)
                    shadow_comparison = None

            with session.lock:
                now = self._now()
                swept = self._sweep(session, now)
                if session.epoch == job.epoch and session.scene_revision == job.revision:
                    if not job.trigger.startswith("explicit:") and any(
                            p.status in OPEN_PROPOSAL_STATUSES and p.revision == job.revision
                            for p in session.proposals.values()):
                        return None
                    return self._install(session, job, result, error, shadow_comparison=shadow_comparison)
                if not (swept and self.auto_recommend):
                    return None
                job = self._prepare(session, now, "observation_freshness_changed")
        return None

    def _install(self, session: _Session, job: _SolverJob, result: Any,
                 error: str | None, shadow_comparison: Any | None = None) -> SimulationProposal | RecommendationState:
        now = self._now()
        for proposal in session.proposals.values():
            if proposal.status in OPEN_PROPOSAL_STATUSES:
                self._close(session, proposal, "superseded", f"replaced_by_replan:{job.trigger}", now)
        active = session.active_assignments()
        busy_casualties = {item.casualty_id for item in active} | session.resolved
        busy_responders = {item.responder_id for item in active}
        new: list[Assignment] = []
        preserved: list[Assignment] = []
        if result is not None and error is None:
            if getattr(result, "scene_revision", job.revision) != job.revision:
                error = "Solver result scene_revision does not match request"
            else:
                seen_casualties: set[str] = set()
                seen_responders: set[str] = set()
                for assignment in result.assignments:
                    if (assignment.casualty_id in busy_casualties
                            or assignment.responder_id in busy_responders):
                        preserved.append(assignment.model_copy(deep=True))
                        continue
                    responder = session.responders.get(assignment.responder_id)
                    if (assignment.casualty_id not in session.casualties or responder is None
                            or responder.status != "available"
                            or assignment.casualty_id in seen_casualties
                            or assignment.responder_id in seen_responders):
                        error = "Solver returned an assignment that violates current state"
                        new = []
                        break
                    seen_casualties.add(assignment.casualty_id)
                    seen_responders.add(assignment.responder_id)
                    new.append(assignment.model_copy(deep=True))
                for assignment in getattr(result, "preserved_assignments", None) or []:
                    if assignment not in preserved:
                        preserved.append(assignment.model_copy(deep=True))
        proposal = None
        if error is None and new:
            expires_at = now + self.proposal_ttl
            horizon = self._freshness_horizon(session, now)
            if horizon is not None and horizon < expires_at:
                expires_at = horizon
            proposal = SimulationProposal(
                id=session.next_id("proposal"), scenario_id=session.scenario_id,
                epoch=session.epoch, revision=job.revision, status="pending_approval",
                assignments=new, preserved_assignments=preserved,
                unassigned_casualty_ids=[cid for cid in result.unassigned_casualty_ids
                                         if cid not in busy_casualties],
                reasons={key: list(value) for key, value in result.reasons.items()},
                rationale=[*result.rationale, *job.location_rationale()], engine=result.engine,
                runtime_ms=result.runtime_ms, map_revision=result.map_revision,
                objective_values=dict(result.objective_values),
                recommendation=result.metadata.model_copy(deep=True),
                location_provenance={k: v.model_copy() for k, v in job.location_provenance.items()},
                created_at=now, expires_at=expires_at,
            )
            session.proposals[proposal.id] = proposal
            session.proposal_context[proposal.id] = (
                job.request.scenario.model_copy(deep=True),
                result.model_copy(update={"rationale": list(proposal.rationale)}, deep=True))
            session.current_proposal_id = proposal.id
            if shadow_comparison is not None:
                shadow_comparison = shadow_comparison.model_copy(update={"proposal_id": proposal.id})
                session.neural_comparisons[proposal.id] = shadow_comparison
                session.current_neural_comparison = shadow_comparison
        recommendation = RecommendationState(
            scene_revision=job.revision, computed_at=now, trigger=job.trigger,
            proposal_id=proposal.id if proposal else None,
            engine=getattr(result, "engine", None) if error is None else None,
            unassigned_casualty_ids=[] if result is None or error else
            [cid for cid in result.unassigned_casualty_ids if cid not in busy_casualties],
            reasons={} if result is None or error else
            {key: list(value) for key, value in result.reasons.items()},
            location_provenance={k: v.model_copy() for k, v in job.location_provenance.items()},
            location_rationale=job.location_rationale(),
            error=error,
        )
        session.last_recommendation = recommendation
        session.emit("simulation.recommendation_computed", _dump(recommendation), now)
        if proposal is not None:
            session.emit("simulation.proposal_created", _dump(proposal), now)
            return proposal.model_copy(deep=True)
        return recommendation.model_copy(deep=True)


    def reset(self, scenario_id: str, request: ResetRequest | None = None,
              idempotency_key: str | None = None, *, only_if_unloaded: bool = False) -> ActionResponse:
        """New epoch: clears all simulation records but never rewinds the event
        sequence, scene revision, or ID counters, so subscribers stay monotonic."""
        request = request or ResetRequest()
        if request.scenario is not None:
            scenario = request.scenario.model_copy(deep=True)
            if scenario.id != scenario_id:
                raise _invalid("Scenario id must match the path scenario_id")
        elif scenario_id == DEMO_SCENARIO_ID:
            scenario = load_demo_scenario()
        else:
            raise _not_found("Only the demo scenario loads without a supplied synthetic scenario")
        if request.add_demo_map and scenario.scene_map is None:
            scenario = scenario.model_copy(update={"scene_map": demo_scene_map(scenario.frame_id)})
            scenario = Scenario.model_validate(_dump(scenario))
        casualty_ids = {casualty.id for casualty in scenario.casualties}
        if set(request.required_capabilities) - casualty_ids:
            raise _invalid("required_capabilities must reference scenario casualty IDs")
        session = self._get_or_create(scenario_id)

        def apply(now: datetime):
            if only_if_unloaded and session.meta is not None:
                return {"epoch": session.epoch, "scene_revision": session.scene_revision}, None, True
            session.epoch += 1
            session.meta = scenario.model_copy(update={"casualties": [], "responders": [],
                                                       "scene_map": None})
            session.casualties = {c.id: c.model_copy(deep=True) for c in scenario.casualties}
            session.responders = {r.id: r.model_copy(deep=True) for r in scenario.responders}
            session.scene_map = scenario.scene_map.model_copy(deep=True) if scenario.scene_map else None
            session.required_capabilities = {
                cid: sorted(set(request.required_capabilities.get(cid, []))) for cid in casualty_ids}
            for name in ("tracks", "casualty_tracks", "observation_fingerprints",
                         "responder_reported_at", "responder_preferences", "proposals",
                         "proposal_context", "approvals", "dispatches", "assignments",
                         "route_invalidations", "device_events", "cameras",
                         "neural_comparisons"):
                getattr(session, name).clear()
            session.current_neural_comparison = None
            session.resolved.clear()
            session.map_revisions = ({scenario.scene_map.revision: _map_fingerprint(scenario.scene_map)}
                                     if scenario.scene_map else {})
            session.freshness_signature = ()
            session.current_proposal_id = None
            session.last_recommendation = None
            session.intelligence_policy = IntelligencePolicy()
            self._forget_previous(session)
            revision = session.scene_revision + 1
            session.emit("simulation.reset", {"epoch": session.epoch, "scene_revision": revision,
                                              "scenario": _dump(session.scenario())}, now)
            return {"epoch": session.epoch, "scene_revision": revision}, "reset", False

        return self._run(session, "reset", _dump(request), apply, idempotency_key)

    def ingest_observation(self, scenario_id: str, observation: CVObservation,
                           idempotency_key: str | None = None) -> ActionResponse:
        session = self._session(scenario_id)

        def apply(now: datetime):
            reserved = [value for value in (observation.id, observation.track and observation.track.track_id)
                        if value and value.startswith(CAMERA_PREFIX)]
            if reserved:
                raise _invalid(f"IDs starting with {CAMERA_PREFIX!r} are reserved for camera ingest")
            return self._apply_observation(session, observation, now, None)

        return self._run(session, "observation", _dump(observation), apply, idempotency_key)

    def register_camera(self, scenario_id: str, camera_id: str, request: CameraRegistration,
                        idempotency_key: str | None = None) -> ActionResponse:
        """Register a SIMULATION camera mapping; immutable for the epoch."""
        session = self._session(scenario_id)

        def apply(now: datetime):
            for zone_id, position in (request.zones or {}).items():
                self._check_frame(session, position)
            config = {"mapping_mode": request.mapping_mode,
                      "calibration_ref": request.calibration_ref,
                      "zones": {k: _dump(v) for k, v in (request.zones or {}).items()} or None}
            existing = session.cameras.get(camera_id)
            if existing is not None:
                current = {"mapping_mode": existing.mapping_mode,
                           "calibration_ref": existing.calibration_ref,
                           "zones": {k: _dump(v) for k, v in (existing.zones or {}).items()} or None}
                if current != config:
                    raise _conflict("Camera mapping is immutable within an epoch; reset to remap")
                return _dump(existing), None, True
            record = CameraRecord(
                camera_id=camera_id, epoch=session.epoch, mapping_mode=request.mapping_mode,
                calibration_ref=request.calibration_ref,
                zones={k: v.model_copy(deep=True) for k, v in request.zones.items()}
                if request.zones else None,
                registered_by=request.actor, registered_at=now,
            )
            session.cameras[camera_id] = record
            session.emit("simulation.camera_registered", _dump(record), now)
            return _dump(record), None, False

        return self._run(session, "camera_registration", [camera_id, _dump(request)], apply,
                         idempotency_key)

    def ingest_camera_observation(self, scenario_id: str, camera_id: str,
                                  body: CameraObservation,
                                  idempotency_key: str | None = None) -> ActionResponse:
        """Convert a camera report via its registered mapping, then ingest it.

        Observation and track IDs are namespaced per camera, so equal local IDs
        from two cameras never merge. No casualty association or cross-camera
        re-identification happens here.
        """
        session = self._session(scenario_id)

        def apply(now: datetime):
            camera = session.cameras.get(camera_id)
            if camera is None:
                raise _not_found(f"Camera {camera_id} is not registered in this epoch")
            if camera.mapping_mode == "calibrated_world":
                if body.position is None or body.zone_id is not None:
                    raise _invalid("calibrated_world cameras must report exactly a position")
                self._check_frame(session, body.position)
                position, basis = body.position, "calibrated_world"
            else:
                if body.zone_id is None or body.position is not None:
                    raise _invalid("predefined_zones cameras must report exactly a zone_id")
                assert camera.zones is not None
                if body.zone_id not in camera.zones:
                    raise _invalid(f"Unknown zone {body.zone_id} for camera {camera_id}")
                position, basis = camera.zones[body.zone_id], "zone_estimate"
            track = body.track.model_copy(update={"track_id": _camera_id(camera_id, body.track.track_id)})
            observation = CVObservation(
                id=_camera_id(camera_id, body.id), source="cv", observed_at=body.observed_at,
                position=position.model_copy(deep=True), kind="person_detected",
                confidence=body.confidence, track=track, signs=body.signs,
            )
            provenance = CameraProvenance(
                camera_id=camera_id, local_observation_id=body.id,
                local_track_id=body.track.track_id, mapping_mode=camera.mapping_mode,
                location_basis=basis, zone_id=body.zone_id,
                calibration_ref=camera.calibration_ref,
            )
            return self._apply_observation(session, observation, now, provenance)

        return self._run(session, "camera_observation", [camera_id, _dump(body)], apply,
                         idempotency_key)

    def _apply_observation(self, session: _Session, observation: CVObservation, now: datetime,
                           provenance: CameraProvenance | None):
        fingerprint = _fingerprint(_dump(observation), _dump(provenance) if provenance else None)
        known = session.observation_fingerprints.get(observation.id)
        payload = {"observation_id": observation.id,
                   "camera": _dump(provenance) if provenance else None}
        if known is not None:
            if known != fingerprint:
                raise _conflict(f"Observation {observation.id} was already received with different content")
            return payload, None, True
        self._check_frame(session, observation.position)
        if observation.observed_at > now + self.skew:
            raise _invalid("observed_at is in the future")
        cause = None
        casualty_id = None
        track = observation.track
        if track is not None:
            record = session.tracks.get(track.track_id)
            if record is not None:
                previous = record.latest
                previous_mapping = (record.camera.model_dump(exclude={"local_observation_id"})
                                    if record.camera else None)
                current_mapping = (provenance.model_dump(exclude={"local_observation_id"})
                                   if provenance else None)
                mapping_changed = previous_mapping != current_mapping
                assert previous.track is not None
                if observation.observed_at < previous.observed_at:
                    raise _conflict("Stale observation: older than the track's latest update")
                if track.first_seen_at != previous.track.first_seen_at:
                    raise _conflict("Track identity conflict: first_seen_at differs for this track_id")
                if track.last_seen_at < previous.track.last_seen_at:
                    raise _conflict("Stale observation: last_seen_at regresses")
                if track.state != "visible" and (
                        observation.position != previous.position
                        or observation.confidence != previous.confidence
                        or track.last_seen_at != previous.track.last_seen_at
                        or mapping_changed):
                    raise _conflict(f"A {track.state} report must retain the last actual "
                                    "detection's position, confidence and last_seen_at")
                record.latest = observation.model_copy(deep=True)
                if observation.signs is not None:
                    record.latest_visible_signs = observation.signs.model_copy(deep=True)
                    record.signs_observed_at = observation.observed_at
                record.count += 1
                record.camera = provenance.model_copy() if provenance else None
                casualty_id = record.casualty_id
                if casualty_id is not None:
                    impactful = (
                        session.casualties[casualty_id].position != observation.position
                        or previous.track.state != track.state
                        or (previous.confidence >= self.min_confidence)
                        != (observation.confidence >= self.min_confidence)
                        or mapping_changed
                    )
                    self._set_casualty_position(session, casualty_id, observation.position)
                    if impactful:
                        cause = f"observation:{track.track_id}"
            else:
                session.tracks[track.track_id] = _Track(
                    track.track_id, observation.model_copy(deep=True),
                    camera=provenance.model_copy() if provenance else None,
                    latest_visible_signs=observation.signs.model_copy(deep=True) if observation.signs else None,
                    signs_observed_at=observation.observed_at if observation.signs else None)
        session.observation_fingerprints[observation.id] = fingerprint
        session.emit("simulation.observation_received",
                     {"observation": _dump(observation),
                      "track_id": track.track_id if track else None,
                      "casualty_id": casualty_id,
                      "camera": _dump(provenance) if provenance else None}, now)
        payload.update(track_id=track.track_id if track else None, casualty_id=casualty_id)
        return payload, cause, False

    def associate_track(self, scenario_id: str, request: AssociationRequest,
                        idempotency_key: str | None = None) -> ActionResponse:
        session = self._session(scenario_id)

        def apply(now: datetime):
            record = session.tracks.get(request.track_id)
            if record is None:
                raise _not_found(f"Unknown track {request.track_id}")
            result = {"track_id": record.track_id, "casualty_id": request.casualty_id,
                      "created": False}
            if record.casualty_id is not None:
                if record.casualty_id == request.casualty_id and not request.create_casualty:
                    return result, None, True
                raise _conflict(f"Track is already associated with {record.casualty_id}")
            if request.create_casualty:
                if request.casualty_id in session.casualties or request.casualty_id in session.responders:
                    raise _conflict(f"Entity ID {request.casualty_id} already exists")
                casualty = Casualty(id=request.casualty_id,
                                    position=record.latest.position.model_copy(deep=True),
                                    medical_assessment=None)
                session.casualties[casualty.id] = casualty
                session.required_capabilities[casualty.id] = []
                session.emit("simulation.casualty_created",
                             {"casualty": _dump(casualty), "track_id": record.track_id,
                              "actor": request.actor}, now)
                result["created"] = True
            else:
                if request.casualty_id not in session.casualties:
                    raise _not_found(f"Unknown casualty {request.casualty_id}")
                existing = session.casualty_tracks.get(request.casualty_id)
                if existing is not None:
                    old = session.tracks.get(existing)
                    if old is not None and (
                        old.latest.track is None
                        or old.latest.track.state == "visible"
                    ):
                        raise _conflict(f"Casualty is already associated with track {existing}")
                    if old is not None:
                        old.casualty_id = None
                    result["replaced_track_id"] = existing
                self._set_casualty_position(session, request.casualty_id, record.latest.position)
            record.casualty_id = request.casualty_id
            session.casualty_tracks[request.casualty_id] = record.track_id
            session.emit("simulation.track_associated", {**result, "actor": request.actor}, now)
            return result, f"association:{record.track_id}", False

        return self._run(session, "association", _dump(request), apply, idempotency_key)

    def record_assessment(self, scenario_id: str, casualty_id: str, assessment: MedicalAssessment,
                          idempotency_key: str | None = None) -> ActionResponse:
        session = self._session(scenario_id)

        def apply(now: datetime):
            casualty = session.casualties.get(casualty_id)
            if casualty is None:
                raise _not_found(f"Unknown casualty {casualty_id}")
            if assessment.assessed_at > now + self.skew:
                raise _invalid("assessed_at is in the future")
            current = casualty.medical_assessment
            result = {"casualty_id": casualty_id, "medical_assessment": _dump(assessment)}
            if current is not None:
                if current == assessment:
                    return result, None, True
                if assessment.assessed_at <= current.assessed_at:
                    raise _conflict("Stale assessment: not newer than the recorded assessment")
            casualty.medical_assessment = assessment.model_copy(deep=True)
            session.emit("simulation.assessment_recorded", result, now)
            return result, f"assessment:{casualty_id}", False

        return self._run(session, "assessment", [casualty_id, _dump(assessment)], apply,
                         idempotency_key)

    def update_responder(self, scenario_id: str, responder_id: str, request: ResponderUpdateRequest,
                         idempotency_key: str | None = None) -> ActionResponse:
        session = self._session(scenario_id)

        def apply(now: datetime):
            responder = session.responders.get(responder_id)
            if responder is None:
                raise _not_found(f"Unknown responder {responder_id}")
            if request.reported_at is not None:
                if request.reported_at > now + self.skew:
                    raise _invalid("reported_at is in the future")
                last = session.responder_reported_at.get(responder_id)
                if last is not None and request.reported_at < last:
                    raise _conflict("Stale responder update: older than the last report")
            if request.position is not None:
                self._check_frame(session, request.position)
            updated = responder.model_copy(deep=True)
            if request.position is not None:
                updated.position = request.position.model_copy(deep=True)
            if request.capabilities is not None:
                updated.capabilities = sorted(set(request.capabilities))
            if request.status is not None:
                if any(item.responder_id == responder_id for item in session.active_assignments()):
                    session.responder_preferences[responder_id] = request.status
                    updated.status = "offline" if request.status == "offline" else "busy"
                else:
                    updated.status = request.status
            if request.reported_at is not None:
                session.responder_reported_at[responder_id] = request.reported_at
            result = {"responder": _dump(updated),
                      "status_after_active_work": session.responder_preferences.get(responder_id)}
            if updated == responder:
                return result, None, True
            session.responders[responder_id] = updated
            session.emit("simulation.responder_updated", result, now)
            return result, f"responder:{responder_id}", False

        return self._run(session, "responder_update", [responder_id, _dump(request)], apply,
                         idempotency_key)

    def update_map(self, scenario_id: str, scene_map: SceneMap,
                   idempotency_key: str | None = None) -> ActionResponse:
        session = self._session(scenario_id)

        def apply(now: datetime):
            assert session.meta is not None
            if scene_map.frame_id != session.meta.frame_id:
                raise _invalid("Map frame_id must match the scenario frame")
            result: dict[str, Any] = {"map_revision": scene_map.revision}
            current = session.scene_map
            if current is not None and current == scene_map:
                return result, None, True
            known = session.map_revisions.get(scene_map.revision)
            if known is not None and known != _map_fingerprint(scene_map):
                raise _conflict("Map revision was already used this epoch with different "
                                "geometry; every geometry/occupancy change needs a new revision")
            session.map_revisions[scene_map.revision] = _map_fingerprint(scene_map)
            session.scene_map = scene_map.model_copy(deep=True)
            session.emit("simulation.map_updated",
                         {"previous_revision": current.revision if current else None,
                          "scene_map": _dump(scene_map)}, now)
            invalidated, cleared = self._refresh_route_invalidations(session, now)
            result["invalidated_assignment_ids"] = invalidated
            result["revalidated_assignment_ids"] = cleared
            return result, f"map:{scene_map.revision}", False

        return self._run(session, "map_update", _dump(scene_map), apply, idempotency_key)

    @staticmethod
    def _refresh_route_invalidations(session: _Session,
                                     now: datetime) -> tuple[list[str], list[str]]:
        """Flag active dispatched routes crossing blocked cells on the new map.

        Historical ``route`` bytes are never rewritten; only ``route_status``
        markers and the advisory ``route_invalidations`` list change. Returns
        (newly_invalidated_ids, newly_cleared_ids).
        """
        assert session.scene_map is not None
        previous = {entry.assignment_id: entry for entry in session.route_invalidations}
        invalidations: list[RouteInvalidation] = []
        for record in session.assignments.values():
            if record.status == "completed" or record.route is None:
                if record.route_status != "current":
                    record.route_status = "current"
                    record.invalidated_reason = None
                    record.invalidated_blocks = None
                continue
            blocked = _blocked_cells_on_route(session.scene_map, record.route)
            if blocked:
                record.route_status = "invalidated"
                record.invalidated_reason = f"route_blocked:{session.scene_map.revision}"
                record.invalidated_blocks = [cell[:] for cell in blocked]
                responder = session.responders.get(record.responder_id)
                casualty = session.casualties.get(record.casualty_id)
                alternative = None
                if responder is not None and casualty is not None:
                    alternative = _advisory_alternative(
                        session.scene_map, responder.position, casualty.position)
                invalidations.append(RouteInvalidation(
                    assignment_id=record.id, casualty_id=record.casualty_id,
                    responder_id=record.responder_id,
                    map_revision=session.scene_map.revision,
                    historical_map_revision=record.route.map_revision,
                    blocked_cells=[cell[:] for cell in blocked],
                    alternative_route=alternative,
                ))
            elif record.route_status == "invalidated":
                record.route_status = "current"
                record.invalidated_reason = None
                record.invalidated_blocks = None
        session.route_invalidations = invalidations
        current_ids = {entry.assignment_id for entry in invalidations}
        previous_ids = set(previous)
        changed = (current_ids != previous_ids or any(
            previous[entry.assignment_id].blocked_cells != entry.blocked_cells
            for entry in invalidations if entry.assignment_id in previous))
        if invalidations and changed:
            session.emit("simulation.route_invalidated", {
                "map_revision": session.scene_map.revision,
                "assignment_ids": sorted(current_ids),
                "invalidations": [_dump(entry) for entry in invalidations],
            }, now)
        elif previous_ids - current_ids:
            session.emit("simulation.route_revalidated", {
                "map_revision": session.scene_map.revision,
                "assignment_ids": sorted(previous_ids - current_ids),
            }, now)
        return sorted(current_ids - previous_ids), sorted(previous_ids - current_ids)

    def set_required_capabilities(self, scenario_id: str, casualty_id: str,
                                  request: RequiredCapabilitiesRequest,
                                  idempotency_key: str | None = None) -> ActionResponse:
        session = self._session(scenario_id)

        def apply(now: datetime):
            if casualty_id not in session.casualties:
                raise _not_found(f"Unknown casualty {casualty_id}")
            tags = sorted(set(request.capabilities))
            result = {"casualty_id": casualty_id, "required_capabilities": tags}
            if session.required_capabilities.get(casualty_id, []) == tags:
                return result, None, True
            session.required_capabilities[casualty_id] = tags
            session.emit("simulation.capabilities_updated", {**result, "actor": request.actor}, now)
            return result, f"capabilities:{casualty_id}", False

        return self._run(session, "required_capabilities", [casualty_id, _dump(request)], apply,
                         idempotency_key)

    def replan(self, scenario_id: str, request: ReplanRequest,
               idempotency_key: str | None = None) -> ActionResponse:
        """Explicit replan: supersedes open proposals at the current revision.

        Retries compare-and-set a bounded number of times, then returns 409.
        """
        session = self._session(scenario_id)
        fingerprint = _fingerprint("replan", _dump(request))
        with session.lock:
            replay = self._replay(session, idempotency_key, fingerprint)
            if replay is not None:
                return replay
            if idempotency_key is not None:
                self._remember(session, idempotency_key, fingerprint, None)
        trigger = f"explicit:{request.actor}" + (f":{request.reason}" if request.reason else "")
        try:
            outcome = None
            for _ in range(EXPLICIT_REPLAN_ATTEMPTS):
                with session.lock:
                    now = self._now()
                    self._sweep(session, now)
                    job = self._prepare(session, now, trigger, request.solver)
                outcome = self._execute(session, job)
                if outcome is not None:
                    break
            if outcome is None:
                raise _conflict("Scene changed during every replan attempt; retry")
        except BaseException:
            if idempotency_key is not None:
                with session.lock:
                    stored = session.idempotency.get(idempotency_key)
                    if stored is not None and stored.response is None:
                        del session.idempotency[idempotency_key]
            raise
        with session.lock:
            response = ActionResponse(action="replan", result=_dump(outcome),
                                      state=self._state(session, self._now()))
            if idempotency_key is not None:
                self._remember(session, idempotency_key, fingerprint, response)
        return response

    def decide(self, scenario_id: str, proposal_id: str, request: DecisionRequest,
               idempotency_key: str) -> ActionResponse:
        session = self._session(scenario_id)

        def apply(now: datetime):
            proposal = session.proposals.get(proposal_id)
            if proposal is None:
                raise _not_found(f"Unknown proposal {proposal_id}")
            if proposal.status != "pending_approval":
                raise _conflict(f"Proposal is {proposal.status}, not pending_approval")
            if request.proposal_revision != proposal.revision:
                raise _conflict("Stale proposal revision: decide on the exact current revision")
            if proposal.revision != session.scene_revision:
                raise _conflict("Proposal no longer matches the current scene revision")
            approval = SimulationApproval(
                id=session.next_id("approval"), proposal_id=proposal.id,
                proposal_revision=proposal.revision, decision=request.decision,
                approved_by=request.actor, operator_id=request.actor, decided_at=now,
                expires_at=proposal.expires_at,
                status="active" if request.decision == "approve" else "recorded",
            )
            session.approvals[approval.id] = approval
            proposal.approval_id = approval.id
            proposal.status = "approved" if request.decision == "approve" else "rejected"
            if request.decision == "reject":
                proposal.closed_reason = f"rejected_by:{request.actor}"
                session.current_proposal_id = None
                self._forget_previous(session)
            session.emit("simulation.decision_recorded", _dump(approval), now)
            return _dump(approval), None, False

        return self._run(session, "decision", [proposal_id, _dump(request)], apply, idempotency_key)

    def dispatch(self, scenario_id: str, request: DispatchRequest,
                 idempotency_key: str) -> ActionResponse:
        """SIMULATION dispatch: atomically consumes one approval. No device I/O."""
        session = self._session(scenario_id)

        def apply(now: datetime):
            proposal = session.proposals.get(request.proposal_id)
            if proposal is None:
                raise _not_found(f"Unknown proposal {request.proposal_id}")
            approval = session.approvals.get(request.approval_id)
            if approval is None:
                raise _not_found(f"Unknown approval {request.approval_id}")
            if approval.proposal_id != proposal.id:
                raise _conflict("Approval does not belong to this proposal")
            if request.proposal_revision != proposal.revision or approval.proposal_revision != proposal.revision:
                raise _conflict("Stale proposal revision")
            if approval.decision != "approve":
                raise _conflict("Proposal was rejected; nothing to dispatch")
            if approval.status == "consumed":
                raise _conflict("Approval was already consumed by a dispatch")
            if approval.status != "active":
                raise _conflict(f"Approval is {approval.status}: {approval.invalidated_reason}")
            if proposal.status != "approved" or proposal.revision != session.scene_revision:
                raise _conflict(f"Proposal is {proposal.status} at a stale scene revision")
            active = session.active_assignments()
            for assignment in proposal.assignments:
                responder = session.responders.get(assignment.responder_id)
                if (assignment.casualty_id not in session.casualties
                        or assignment.casualty_id in session.resolved
                        or responder is None or responder.status != "available"
                        or any(item.casualty_id == assignment.casualty_id
                               or item.responder_id == assignment.responder_id for item in active)):
                    raise _conflict("Proposal no longer matches current entity availability")
            dispatch_id = session.next_id("dispatch")
            created = []
            for assignment in proposal.assignments:
                record = SimulatedAssignment(
                    id=session.next_id("assignment"), dispatch_id=dispatch_id,
                    command_id=session.next_id("command"),
                    device_id=f"sim-device-{assignment.responder_id}",
                    casualty_id=assignment.casualty_id, responder_id=assignment.responder_id,
                    route=assignment.route.model_copy(deep=True) if assignment.route else None,
                    status="dispatched", created_at=now,
                )
                session.assignments[record.id] = record
                session.responders[assignment.responder_id].status = "busy"
                created.append(record)
            dispatch = SimulationDispatch(
                id=dispatch_id, proposal_id=proposal.id, proposal_revision=proposal.revision,
                approval_id=approval.id, dispatched_by=request.actor, dispatched_at=now,
                assignment_ids=[record.id for record in created],
            )
            session.dispatches[dispatch.id] = dispatch
            approval.status = "consumed"
            proposal.status = "dispatched"
            proposal.dispatch_id = dispatch.id
            session.current_proposal_id = None
            self._forget_previous(session)
            result = {"dispatch": _dump(dispatch), "assignments": [_dump(r) for r in created]}
            session.emit("simulation.dispatch_created", result, now)
            return result, f"dispatch:{dispatch.id}", False

        return self._run(session, "dispatch", _dump(request), apply, idempotency_key)

    def ingest_device_event(self, scenario_id: str, event: SimulatedDeviceEvent,
                            idempotency_key: str | None = None) -> ActionResponse:
        session = self._session(scenario_id)
        return self._run(session, "device_event", _dump(event),
                         lambda now: self._apply_device_event(session, scenario_id, event, now),
                         idempotency_key)

    def simulate_device_event(self, scenario_id: str, assignment_id: str,
                              request: SimulateDeviceRequest,
                              idempotency_key: str | None = None) -> ActionResponse:
        """Build and ingest a correlated SIMULATED device report atomically.

        The default event ID is deterministic per assignment and kind. When that
        ID was already ingested and no ``occurred_at`` is given, the stored
        timestamp is reused, so retrying the same helper request (with or
        without the same Idempotency-Key) replays instead of conflicting.
        """
        session = self._session(scenario_id)

        def apply(now: datetime):
            record = session.assignments.get(assignment_id)
            if record is None:
                raise _not_found(f"Unknown assignment {assignment_id}")
            event_id = request.event_id or f"sim-device-event-{assignment_id}-{request.kind}"
            stored = session.device_events.get(event_id)
            occurred_at = request.occurred_at or (stored.occurred_at if stored else now)
            event = SimulatedDeviceEvent(
                id=event_id, scenario_id=scenario_id, device_id=record.device_id,
                dispatch_id=record.dispatch_id, command_id=record.command_id,
                casualty_id=record.casualty_id, responder_id=record.responder_id,
                kind=request.kind, occurred_at=occurred_at, mode="simulation",
            )
            return self._apply_device_event(session, scenario_id, event, now)

        return self._run(session, "simulate_device_event", [assignment_id, _dump(request)],
                         apply, idempotency_key)

    def _apply_device_event(self, session: _Session, scenario_id: str,
                            event: SimulatedDeviceEvent, now: datetime):
        if event.scenario_id != scenario_id:
            raise _invalid("Device event scenario_id must match the path")
        known = session.device_events.get(event.id)
        if known is not None:
            if known != event:
                raise _conflict(f"Device event {event.id} was already received with different content")
            return {"event_id": event.id, "state_changed": False,
                    "device_event": _dump(known)}, None, True
        dispatch = session.dispatches.get(event.dispatch_id)
        if dispatch is None:
            raise _not_found(f"Unknown dispatch {event.dispatch_id}")
        record = next((session.assignments[aid] for aid in dispatch.assignment_ids
                       if session.assignments[aid].command_id == event.command_id), None)
        if record is None:
            raise _conflict("command_id does not belong to this dispatch")
        if (record.casualty_id, record.responder_id, record.device_id) != (
                event.casualty_id, event.responder_id, event.device_id):
            raise _conflict("Device event does not correlate with the dispatched assignment")
        if event.occurred_at > now + self.skew:
            raise _invalid("occurred_at is in the future")
        if event.occurred_at < dispatch.dispatched_at - self.skew:
            raise _conflict("Stale device event: occurred before the dispatch")
        for kind in ("delivered", "acknowledged", "completed"):
            recorded = getattr(record, f"{kind}_at")
            if recorded is None or kind == event.kind:
                continue
            earlier = MILESTONE_RANK[kind] < MILESTONE_RANK[event.kind]
            if (recorded > event.occurred_at) if earlier else (recorded < event.occurred_at):
                raise _conflict(f"{event.kind} at {event.occurred_at.isoformat()} is inconsistent "
                                f"with recorded {kind} at {recorded.isoformat()}")
        session.device_events[event.id] = event.model_copy(deep=True)
        record.device_event_ids.append(event.id)
        stamp = f"{event.kind}_at"
        result = {"event_id": event.id, "assignment_id": record.id, "state_changed": False,
                  "device_event": _dump(event)}
        if getattr(record, stamp) is not None:
            return result, None, False
        setattr(record, stamp, event.occurred_at)
        if MILESTONE_RANK[event.kind] > MILESTONE_RANK[record.status]:
            record.status = event.kind
        result["state_changed"] = True
        session.emit(f"simulation.device_{event.kind}",
                     {"device_event": _dump(event), "assignment": _dump(record)}, now)
        if event.kind == "completed":
            responder = session.responders[record.responder_id]
            responder.status = session.responder_preferences.pop(record.responder_id, "available")
            session.resolved.add(record.casualty_id)
            record.route_status = "current"
            record.invalidated_reason = None
            record.invalidated_blocks = None
            session.route_invalidations = [entry for entry in session.route_invalidations
                                           if entry.assignment_id != record.id]
            session.emit("simulation.responder_released",
                         {"responder_id": responder.id, "status": responder.status,
                          "assignment_id": record.id}, now)
        return result, f"device:{event.kind}:{record.id}", False


    def _check_frame(self, session: _Session, position: Position) -> None:
        assert session.meta is not None
        if position.frame_id != session.meta.frame_id:
            raise _invalid("Position frame_id must match the scenario frame")

    @staticmethod
    def _set_casualty_position(session: _Session, casualty_id: str, position: Position) -> None:
        session.casualties[casualty_id].position = position.model_copy(deep=True)

    def _state(self, session: _Session, now: datetime) -> SimulationState:
        tracks = [TrackRecord(track_id=t.track_id, casualty_id=t.casualty_id,
                              latest_observation=t.latest.model_copy(deep=True),
                              observation_count=t.count,
                              camera=t.camera.model_copy() if t.camera else None,
                              latest_visible_signs=t.latest_visible_signs.model_copy(deep=True) if t.latest_visible_signs else None,
                              signs_observed_at=t.signs_observed_at)
                  for t in sorted(session.tracks.values(), key=lambda t: t.track_id)]
        return SimulationState(
            scenario_id=session.scenario_id, epoch=session.epoch,
            scene_revision=session.scene_revision, sequence=session.events.sequence,
            generated_at=now, scenario=session.scenario(),
            required_capabilities={cid: list(tags) for cid, tags in
                                   sorted(session.required_capabilities.items())},
            tracks=tracks, resolved_casualty_ids=sorted(session.resolved),
            cameras=[c.model_copy(deep=True) for _, c in sorted(session.cameras.items())],
            current_proposal_id=session.current_proposal_id,
            proposals=[p.model_copy(deep=True) for p in session.proposals.values()],
            approvals=[a.model_copy(deep=True) for a in session.approvals.values()],
            dispatches=[d.model_copy(deep=True) for d in session.dispatches.values()],
            assignments=[a.model_copy(deep=True) for a in session.assignments.values()],
            route_invalidations=[entry.model_copy(deep=True)
                                 for entry in session.route_invalidations],
            last_recommendation=(session.last_recommendation.model_copy(deep=True)
                                 if session.last_recommendation else None),
        )
