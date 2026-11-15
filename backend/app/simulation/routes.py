"""SIMULATION-only REST and WebSocket routes under ``/api/v1/simulations``.

HTTP handlers are synchronous (FastAPI threadpool), so locks and the solver
never block the event loop. The WebSocket reads events through a worker
thread and polls with ``anyio.sleep``. Client WebSocket messages are ignored:
they cannot approve or dispatch anything.
"""

from collections.abc import Callable
from typing import Any

import anyio
from fastapi import APIRouter, Body, Header, HTTPException, Query, Request, WebSocket
from starlette.websockets import WebSocketDisconnect, WebSocketState

from backend.app.models import CVObservation, MedicalAssessment, SceneMap
from backend.app.services.evaluation_reports import EvaluationIndex
from backend.app.intelligence.neural_shadow import NeuralComparisonReport
from backend.app.simulation.comparisons import ComparisonReport, ComparisonRequest
from backend.app.simulation.models import (
    ActionResponse,
    AssociationRequest,
    CameraObservation,
    CameraRegistration,
    DecisionRequest,
    DispatchRequest,
    EventPage,
    IntelligencePolicyRequest,
    IntelligenceSnapshot,
    ReplanRequest,
    RequiredCapabilitiesRequest,
    ResetRequest,
    ResponderUpdateRequest,
    SimulatedDeviceEvent,
    SimulateDeviceRequest,
    SimulationState,
)
from backend.app.simulation.store import SimulationError, SimulationStore

router = APIRouter(prefix="/api/v1/simulations", tags=["simulation"])

WS_POLL_SECONDS = 0.05
WS_BATCH = 100
WS_TICK_POLLS = 20
IdempotencyKey = Header(default=None, alias="Idempotency-Key", min_length=1)
RequiredIdempotencyKey = Header(alias="Idempotency-Key", min_length=1)


def _store(app: Any) -> SimulationStore:
    store = getattr(app.state, "simulation_store", None)
    if store is None:
        raise HTTPException(503, "Simulation store is not configured")
    return store


def _call(request: Request, operation: Callable[[SimulationStore], Any]) -> Any:
    try:
        return operation(_store(request.app))
    except SimulationError as error:
        raise HTTPException(error.status_code, error.detail) from None


@router.post("/{scenario_id}/reset", response_model=ActionResponse, status_code=201)
def reset(request: Request, scenario_id: str,
          body: ResetRequest | None = Body(default=None),
          idempotency_key: str | None = IdempotencyKey) -> ActionResponse:
    """Start a new SIMULATION epoch (demo fixture + synthetic map by default)."""
    return _call(request, lambda s: s.reset(scenario_id, body or ResetRequest(), idempotency_key))


@router.get("/{scenario_id}/state", response_model=SimulationState)
def state(request: Request, scenario_id: str) -> SimulationState:
    return _call(request, lambda s: s.snapshot(scenario_id))


@router.get("/{scenario_id}/events", response_model=EventPage)
def events(request: Request, scenario_id: str,
           after_sequence: int = Query(default=0, ge=0),
           limit: int = Query(default=200, ge=1, le=1000)) -> EventPage:
    return _call(request, lambda s: s.events(scenario_id, after_sequence, limit))


@router.post("/{scenario_id}/observations", response_model=ActionResponse, status_code=201)
def observation(request: Request, scenario_id: str, body: CVObservation,
                idempotency_key: str | None = IdempotencyKey) -> ActionResponse:
    """Detection evidence only: never creates a casualty or an assessment."""
    return _call(request, lambda s: s.ingest_observation(scenario_id, body, idempotency_key))


@router.put("/{scenario_id}/cameras/{camera_id}", response_model=ActionResponse)
def register_camera(request: Request, scenario_id: str, camera_id: str, body: CameraRegistration,
                    idempotency_key: str | None = IdempotencyKey) -> ActionResponse:
    """SIMULATION camera mapping; immutable per epoch except identical replay."""
    return _call(request, lambda s: s.register_camera(scenario_id, camera_id, body,
                                                      idempotency_key))


@router.post("/{scenario_id}/cameras/{camera_id}/observations",
             response_model=ActionResponse, status_code=201)
def camera_observation(request: Request, scenario_id: str, camera_id: str,
                       body: CameraObservation,
                       idempotency_key: str | None = IdempotencyKey) -> ActionResponse:
    """Camera-namespaced detection; never associates or re-identifies casualties."""
    return _call(request, lambda s: s.ingest_camera_observation(scenario_id, camera_id, body,
                                                                idempotency_key))


@router.post("/{scenario_id}/associations", response_model=ActionResponse, status_code=201)
def association(request: Request, scenario_id: str, body: AssociationRequest,
                idempotency_key: str | None = IdempotencyKey) -> ActionResponse:
    return _call(request, lambda s: s.associate_track(scenario_id, body, idempotency_key))


@router.post("/{scenario_id}/casualties/{casualty_id}/assessment",
             response_model=ActionResponse, status_code=201)
def assessment(request: Request, scenario_id: str, casualty_id: str, body: MedicalAssessment,
               idempotency_key: str | None = IdempotencyKey) -> ActionResponse:
    """Human-entered label (``source: human``); stale/out-of-order rejected."""
    return _call(request, lambda s: s.record_assessment(scenario_id, casualty_id, body,
                                                        idempotency_key))


@router.put("/{scenario_id}/casualties/{casualty_id}/required-capabilities",
            response_model=ActionResponse)
def required_capabilities(request: Request, scenario_id: str, casualty_id: str,
                          body: RequiredCapabilitiesRequest,
                          idempotency_key: str | None = IdempotencyKey) -> ActionResponse:
    return _call(request, lambda s: s.set_required_capabilities(scenario_id, casualty_id, body,
                                                                idempotency_key))


@router.patch("/{scenario_id}/responders/{responder_id}", response_model=ActionResponse)
def responder(request: Request, scenario_id: str, responder_id: str, body: ResponderUpdateRequest,
              idempotency_key: str | None = IdempotencyKey) -> ActionResponse:
    return _call(request, lambda s: s.update_responder(scenario_id, responder_id, body,
                                                       idempotency_key))


@router.put("/{scenario_id}/map", response_model=ActionResponse)
def scene_map(request: Request, scenario_id: str, body: SceneMap,
              idempotency_key: str | None = IdempotencyKey) -> ActionResponse:
    return _call(request, lambda s: s.update_map(scenario_id, body, idempotency_key))


@router.post("/{scenario_id}/replan", response_model=ActionResponse, status_code=201)
def replan(request: Request, scenario_id: str, body: ReplanRequest,
           idempotency_key: str | None = IdempotencyKey) -> ActionResponse:
    """Explicit recommendation; supersedes open proposals. Advisory only."""
    return _call(request, lambda s: s.replan(scenario_id, body, idempotency_key))


@router.get("/{scenario_id}/intelligence", response_model=IntelligenceSnapshot)
def intelligence(request: Request, scenario_id: str) -> IntelligenceSnapshot:
    """Advisory temporal and objective diagnostics after the normal freshness tick.

    The tick itself may expire proposals or replan; the read installs nothing.
    """
    return _call(request, lambda s: s.intelligence(scenario_id))


@router.put("/{scenario_id}/intelligence/policy", response_model=ActionResponse)
def intelligence_policy(request: Request, scenario_id: str, body: IntelligencePolicyRequest,
                        idempotency_key: str | None = IdempotencyKey) -> ActionResponse:
    """Opt-in per-scenario intelligence settings; explicit actor required."""
    return _call(request, lambda s: s.set_intelligence_policy(scenario_id, body,
                                                              idempotency_key))


@router.post("/{scenario_id}/intelligence/comparisons", response_model=ComparisonReport)
def comparisons(request: Request, scenario_id: str, body: ComparisonRequest) -> ComparisonReport:
    """Pure counterfactual reruns of the real solver; installs and authorizes nothing."""
    return _call(request, lambda s: s.compare(scenario_id, body))


@router.get("/{scenario_id}/intelligence/evaluations", response_model=EvaluationIndex)
def evaluations(request: Request, scenario_id: str) -> EvaluationIndex:
    """Committed synthetic evaluation artifacts with content digests."""
    return _call(request, lambda s: s.evaluations(scenario_id))


@router.get("/{scenario_id}/neural-comparison", response_model=NeuralComparisonReport)
def neural_comparison(request: Request, scenario_id: str) -> NeuralComparisonReport:
    """Read-only live shadow comparison (OR-Tools vs Pairwise MLP vs Bipartite GNN)."""
    return _call(request, lambda s: s.neural_comparison(scenario_id))


@router.get("/{scenario_id}/proposals/{proposal_id}/explanation")
def explanation(request: Request, scenario_id: str, proposal_id: str) -> dict[str, Any]:
    """Grounded facts for a proposal; reading it changes no state."""
    return _call(request, lambda s: s.explanation(scenario_id, proposal_id))


@router.post("/{scenario_id}/proposals/{proposal_id}/decision",
             response_model=ActionResponse, status_code=201)
def decision(request: Request, scenario_id: str, proposal_id: str, body: DecisionRequest,
             idempotency_key: str = RequiredIdempotencyKey) -> ActionResponse:
    """Local operator decision on the exact proposal revision. Not authenticated."""
    return _call(request, lambda s: s.decide(scenario_id, proposal_id, body, idempotency_key))


@router.post("/{scenario_id}/dispatches", response_model=ActionResponse, status_code=201)
def dispatch(request: Request, scenario_id: str, body: DispatchRequest,
             idempotency_key: str = RequiredIdempotencyKey) -> ActionResponse:
    """SIMULATION dispatch: consumes the approval exactly once; no device I/O."""
    return _call(request, lambda s: s.dispatch(scenario_id, body, idempotency_key))


@router.post("/{scenario_id}/device-events", response_model=ActionResponse, status_code=201)
def device_event(request: Request, scenario_id: str, body: SimulatedDeviceEvent,
                 idempotency_key: str | None = IdempotencyKey) -> ActionResponse:
    return _call(request, lambda s: s.ingest_device_event(scenario_id, body, idempotency_key))


@router.post("/{scenario_id}/assignments/{assignment_id}/simulate-device-event",
             response_model=ActionResponse, status_code=201)
def simulate_device_event(request: Request, scenario_id: str, assignment_id: str,
                          body: SimulateDeviceRequest,
                          idempotency_key: str | None = IdempotencyKey) -> ActionResponse:
    return _call(request, lambda s: s.simulate_device_event(scenario_id, assignment_id, body,
                                                            idempotency_key))


@router.websocket("/{scenario_id}/ws")
async def stream(websocket: WebSocket, scenario_id: str) -> None:
    """Server-to-client stream: a snapshot, then events with greater sequence.

    A snapshot is re-sent whenever this client fell behind retained history.
    """
    store = getattr(websocket.app.state, "simulation_store", None)
    if store is None:
        await websocket.close(code=1011)
        return
    try:
        snapshot = await anyio.to_thread.run_sync(store.snapshot_envelope, scenario_id)
    except SimulationError:
        await websocket.close(code=1008)
        return
    await websocket.accept()

    async def receive(scope: anyio.CancelScope) -> None:
        try:
            while True:
                message = await websocket.receive()
                if message["type"] == "websocket.disconnect":
                    break
        except (WebSocketDisconnect, RuntimeError):
            pass
        scope.cancel()

    async def send(scope: anyio.CancelScope, first: Any) -> None:
        try:
            await websocket.send_json(first.model_dump(mode="json"))
            cursor = first.sequence
            idle = 0
            while True:
                if idle >= WS_TICK_POLLS:
                    idle = 0
                    await anyio.to_thread.run_sync(store.tick, scenario_id)
                page = await anyio.to_thread.run_sync(store.events, scenario_id, cursor, WS_BATCH)
                if page.gap or page.latest_sequence < cursor:
                    fresh = await anyio.to_thread.run_sync(store.snapshot_envelope, scenario_id)
                    await websocket.send_json(fresh.model_dump(mode="json"))
                    cursor = fresh.sequence
                    continue
                for event in page.events:
                    await websocket.send_json(event.model_dump(mode="json"))
                    cursor = event.sequence
                if not page.events:
                    idle += 1
                    await anyio.sleep(WS_POLL_SECONDS)
        except (WebSocketDisconnect, RuntimeError, SimulationError):
            pass
        scope.cancel()

    async with anyio.create_task_group() as group:
        group.start_soon(receive, group.cancel_scope)
        group.start_soon(send, group.cancel_scope, snapshot)
    if websocket.client_state == WebSocketState.CONNECTED:
        try:
            await websocket.close()
        except RuntimeError:
            pass
