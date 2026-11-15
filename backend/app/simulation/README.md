# Simulation coordination API (SIMULATION ONLY)

Local, in-memory, synthetic. No authentication, no real devices, no real
dispatch. Simulation-specific requests, responses and events carry
`mode: "simulation"`; reused shared CV/map/assessment inputs retain their original
schemas without a mode field. Sending `mode: "real"` returns 422. All request
bodies forbid unknown fields.

Wiring (owned by `backend/app/main.py`):

```python
from backend.app.simulation.routes import router as simulation_router
from backend.app.simulation.store import SimulationStore
application.state.simulation_store = SimulationStore()  # clock=, solver=, auto_recommend= injectable
application.include_router(simulation_router)
```

Demo scenario id: **`demo`** (loaded lazily on first use, or via reset). Other
IDs must be reset with a supplied synthetic `scenario` whose `id` matches.

## Routes — prefix `/api/v1/simulations/{scenario_id}`

| Method | Path | Body | Success |
| --- | --- | --- | --- |
| POST | `/reset` | optional `ResetRequest` `{scenario?, required_capabilities?, add_demo_map=true}` | 201 ActionResponse |
| GET | `/state` | — | 200 SimulationState |
| GET | `/events?after_sequence=0&limit=200` | — | 200 EventPage |
| WS | `/ws` | — | snapshot then events |
| POST | `/observations` | contract `CVObservation` | 201 |
| PUT | `/cameras/{camera_id}` | `CameraRegistration` (below) | 200 ActionResponse |
| POST | `/cameras/{camera_id}/observations` | `CameraObservation` (below) | 201 |
| POST | `/associations` | `{track_id, casualty_id, create_casualty=false, actor}` | 201 |
| POST | `/casualties/{casualty_id}/assessment` | contract `MedicalAssessment` (`source: "human"`) | 201 |
| PUT | `/casualties/{casualty_id}/required-capabilities` | `{capabilities: [str], actor}` | 200 |
| PATCH | `/responders/{responder_id}` | `{position?, status?, capabilities?, reported_at?}` (≥1 of first 3) | 200 |
| PUT | `/map` | contract `SceneMap` (new `revision` required for changes) | 200 |
| POST | `/replan` | `{actor, reason?, solver?: ortools/fallback/nearest}` | 201 |
| GET | `/proposals/{proposal_id}/explanation` | — | 200 grounded facts (no state change) |
| POST | `/proposals/{proposal_id}/decision` | `{proposal_revision, decision: approve/reject, actor}` + **`Idempotency-Key` header required** | 201 |
| POST | `/dispatches` | `{proposal_id, proposal_revision, approval_id, actor}` + **`Idempotency-Key` header required** | 201 |
| POST | `/device-events` | `SimulatedDeviceEvent` (contract DeviceAssignmentEvent, `mode` must be `simulation`) | 201 |
| POST | `/assignments/{assignment_id}/simulate-device-event` | `{kind: delivered/acknowledged/completed, event_id?, occurred_at?}` | 201 |

`Idempotency-Key` is optional on other mutations. Same key + same body replays
the stored response with `duplicate: true`; same key + different body → 409.
A key is reserved atomically before any unlocked work (e.g. an explicit replan's
solve): a retry while the original is still in flight gets 409 "still in
progress" (no waiting on locks); retry after it completes to get the replay.

Errors: `{"detail": "..."}`. 404 unknown scenario/entity/proposal/approval/
dispatch; 409 stale revision, out-of-order timestamp, already consumed/decided,
expired, superseded, conflicting duplicate ID/key, unavailable responder;
422 invalid payload, future-dated timestamp, wrong frame, `mode: real`.

## Responses

Every mutation returns `ActionResponse`:
`{mode, action, duplicate, result: {...action-specific...}, state: SimulationState}`
where `state` is the snapshot **after** any automatic replan.

`SimulationState` (see `models.py`): `mode, schema_version, scenario_id, epoch,
scene_revision, sequence, generated_at, scenario (contract Scenario),
required_capabilities {casualty_id: [tags]}, tracks [{track_id, casualty_id|null,
latest_observation, observation_count}], resolved_casualty_ids,
current_proposal_id, proposals, approvals, dispatches, assignments,
last_recommendation`.

Key `result` shapes:
- decision → `SimulationApproval` `{id, proposal_id, proposal_revision, decision, approved_by, operator_id, authenticated: false, decided_at, expires_at, status: active/consumed/invalidated/expired/recorded}`
- dispatch → `{dispatch: SimulationDispatch, assignments: [SimulatedAssignment]}`; each assignment has `id, dispatch_id, command_id, device_id, casualty_id, responder_id, route, status, delivered_at, acknowledged_at, completed_at`
- replan → the new `SimulationProposal`, or a `RecommendationState` when nothing new was proposable
- device event → `{event_id, assignment_id, state_changed}`

## Lifecycle semantics

1. Reset → scene revision bumps → auto recommendation → `current_proposal_id`
   (status `pending_approval`). `proposal.revision` == scene revision it was
   computed on; quote it exactly. Proposals contain **new assignments only**;
   `preserved_assignments` lists active work, which is never re-proposed.
   No proposal is created when nothing new is assignable (see `last_recommendation`).
2. Any impactful change bumps `scene_revision`, supersedes open proposals,
   invalidates unconsumed approvals, and auto-replans once (bounded; no solver
   loops). Impactful = reset, association, assessment, required capabilities,
   **any** responder change (status, capabilities, or position — no distance
   tolerance), map revision, an associated track's position (any change),
   state, or confidence crossing `min_confidence` (0.5), dispatch, device
   milestone, and **observation freshness transitions** (below).
   An unchanged heartbeat (same position/state, still fresh) does not bump.
3. Decision: only `pending_approval`, exact revision, unexpired.
4. Dispatch: approval must be `active`, proposal `approved` at the current
   scene revision, responders still available. Approval is consumed atomically;
   a second dispatch with a different key → 409. Responders become `busy`.
5. Device events must correlate dispatch/command/casualty/responder/device
   (`device_id` = `sim-device-{responder_id}`). Out-of-order milestones never
   regress status; missing earlier milestones stay `null`. Event ID reused with
   the same body → `duplicate: true`; with a different body → 409.
6. `completed` frees the responder to `available`, unless a status update
   arrived during active work (stored preference `busy`/`offline` wins). Active
   assignments survive responder status changes. Completed casualties enter
   `resolved_casualty_ids` and are not re-proposed.
7. Observations keep `track_id` separate from casualty IDs. Only an explicit
   association links a track to an existing casualty or creates a new confirmed
   casualty (unassessed). Associated tracks move the casualty and feed solver
   freshness/confidence checks. Observations never create assessments.
   Per track: older `observed_at`, regressing `last_seen_at`, or a different
   `first_seen_at` → 409. An `occluded`/`lost` report must repeat the last
   actual detection's position, confidence and `last_seen_at` exactly → else 409.
8. Freshness (evaluated at request time on every mutation, `GET /state`,
   explicit replan, and roughly every second while a WebSocket is connected):
   each associated observation is classified like the solver does —
   `observed_at` and `track.last_seen_at` are *future* (age < 0), *fresh*
   (0 ≤ age ≤ 30 s) or *stale* (age > 30 s) — and every human assessment is
   *future* (`assessed_at > now`, which the solver ignores, giving unassessed
   parity) or *effective*. This includes assessments supplied in a reset
   scenario. When any classification changes
   (e.g. a track goes stale, or a stale track is restored with identical
   values) the scene revision bumps and the system replans. Proposal
   `expires_at` = min(created + 120 s, next freshness/assessment transition),
   so an approval can never be dispatched against observations that have since
   gone stale or an assessment that has since taken effect. A late automatic
   solve never replaces an open proposal at the same revision (e.g. one
   produced by an explicit replan meanwhile). Stale/uncertain observations block *new* work for that casualty only;
   active work is preserved.
9. Map revisions are a per-epoch registry: re-sending a known revision with
   identical content is allowed (e.g. restoring a prior map); reusing any
   revision seen this epoch with different geometry/occupancy → 409. Reset
   reseeds the registry from the loaded map.
10. Device milestone times must be chronological against every milestone
    already recorded for that assignment (`delivered ≤ acknowledged ≤
    completed`); inconsistent times → 409 before anything is stored.
    Out-of-order *arrival* is fine when the times are consistent.
    `simulate-device-event` without `event_id`/`occurred_at` uses the
    deterministic ID `sim-device-event-{assignment_id}-{kind}` and, on retry,
    the originally stored timestamp, so retries replay (`duplicate: true`).

## SIMULATION cameras

Local demo adapters; they never touch the shared contract `/observations`
schema, which is unchanged (except that IDs starting with `sim-camera:` are
reserved there → 422).

`PUT /cameras/{camera_id}` — `CameraRegistration`:

```json
{"mode": "simulation", "actor": "operator-1",
 "mapping_mode": "calibrated_world" | "predefined_zones",
 "calibration_ref": "lab-cal-1",                     // required + nonblank for calibrated_world
 "zones": {"doorway": {"frame_id": "demo-floor-1", "x_m": 10, "y_m": 16}},  // required nonempty for predefined_zones; forbidden for calibrated_world
 "stream_url": "http://192.168.137.145:8090/stream.mjpg"}  // optional live view for dashboards
```

`mode` is required. `calibration_ref` is an external/human assertion that a
world calibration exists; the backend computes nothing. Zone positions must use
the scene frame (else 422). A registration is immutable for the epoch: an
identical mapping replays (`duplicate: true`, original `registered_by` kept,
actor ignored); a different mapping → 409. Reset clears all cameras.
`stream_url` (plain `http(s)://`, no credentials, ≤ 500 chars) is not part of
the mapping: the same mapping with a different `stream_url` updates it
(`duplicate: false`, `simulation.camera_registered` again with the new record),
so a restarted camera on a new address stays on the dashboard. The record in
`state.cameras` carries it (`null` when the camera serves no view).

`POST /cameras/{camera_id}/observations` — `CameraObservation`:

```json
{"mode": "simulation", "id": "o1", "observed_at": "...Z", "confidence": 0.9,
 "track": {"track_id": "t1", "state": "visible", "first_seen_at": "...Z", "last_seen_at": "...Z"},
 "position": {"frame_id": "demo-floor-1", "x_m": 10, "y_m": 16}}   // calibrated_world: exactly position
 // or "zone_id": "doorway"                                        // predefined_zones: exactly zone_id
```

404 unregistered camera; 422 wrong location kind, unknown zone, non-scene
(raw camera) frame, `mode` missing/`real`, or any extra field (no severity).
The report becomes a contract `CVObservation` (`source: cv`,
`kind: person_detected`) with namespaced IDs
`sim-camera:{len(camera_id)}:{camera_id}:{local_id}` for both observation and
track — two cameras' local `t1` stay distinct. Use `result.track_id` from the
response for `/associations`. No automatic casualty creation, association, or
cross-camera re-identification. Dedup and occluded/lost retention rules
apply per namespaced track (a lost zone report must keep the same zone).

Provenance: `state.cameras` lists registrations; each `tracks[]` entry has
`camera` (null for legacy ingest) =
`{camera_id, local_observation_id, local_track_id, mapping_mode,
location_basis: calibrated_world | zone_estimate, zone_id, calibration_ref}`;
the same object appears in the `result.camera` and in
`simulation.observation_received` payloads. `zone_estimate` positions are the
zone's registered reference point, not an observed coordinate.
Event: `simulation.camera_registered` (payload CameraRecord).

Recommendations carry the same provenance for every associated casualty whose
track came from a camera, snapshotted when the solve was prepared:
`SimulationProposal.location_provenance` and
`RecommendationState.location_provenance` (`{casualty_id: CameraProvenance}`,
empty for legacy tracks), plus deterministic sentences appended to
`proposal.rationale` / `last_recommendation.location_rationale` naming the
scene revision. Zone notes state the location is a zone reference point, not
a precise observed coordinate; calibrated notes state `calibration_ref` is an
external assertion the backend neither computes nor verifies. The proposal
explanation endpoint includes these sentences as facts.

## Events (`EventEnvelope`)

`{schema_version: "1.0", mode: "simulation", event_id, type, scenario_id,
occurred_at, sequence, payload}`. `sequence` is per scenario, strictly
increasing and **never resets** (not even across `/reset`). Types:
`simulation.reset, simulation.snapshot (WS only, not stored), observation_received, camera_registered,
track_associated, casualty_created, assessment_recorded, responder_updated,
map_updated, capabilities_updated, replan_requested, recommendation_computed,
proposal_created, proposal_superseded, proposal_expired, decision_recorded,
approval_invalidated, dispatch_created, device_delivered, device_acknowledged,
device_completed, responder_released` (all prefixed `simulation.`).

WebSocket: first message is `simulation.snapshot` whose `sequence` is the last
event already reflected; then every event with a greater sequence. If the
client falls behind the retained window (1000 events) a fresh snapshot is sent.
On reconnect, simply use the new initial snapshot. Client messages are ignored.
Unknown scenarios are refused (close 1008) before accept. Reset does not
restart `sequence`, `scene_revision`, or ID counters; `epoch` increments.
