# Frontend → backend requests (proposal for review)

Owner of this file: Hillary (frontend). **This is not a contract change.** `shared/CONTRACT.md`
is Prince's; each item below is a request for review. The dashboard already has a UI for every
item, backed today by either an honest "no data" state or clearly labelled **PREVIEW** mock data
(`src/lib/preview.ts`, off by default). When an endpoint ships, only the adapter changes.

All payloads below follow existing conventions: snake_case, `mode: "simulation"`, timezone-aware
UTC timestamps, IDs scoped to the scenario, no real patient data.

---

## 1. Explicit acknowledgement source  *(priority: high — demo honesty)*

**Why:** command must tell a real Arduino button press from a rehearsal click. Today the UI
infers it from the event id (`sim-device-event-<assignment>-<kind>` ⇒ simulated, anything else ⇒
external bridge) — see `milestoneSource()` in `src/lib/insights.ts`. That is a heuristic.

**Ask:** add to `SimulatedAssignment` (or to each recorded milestone):

```json
"milestone_sources": {
  "delivered": "device_bridge",
  "acknowledged": "device_bridge",
  "completed": null
}
```

Values: `"device_bridge" | "simulated_control" | null`, derived server-side from the route used
(`/device-events` vs `/assignments/{id}/simulate-device-event`), never from client input.

## 2. Responder device heartbeat  *(priority: high — contract says handle disconnects)*

**Why:** show whether a firefighter's device (Arduino) is reachable before/after dispatch.
UI today: "unknown" (or PREVIEW mock mirroring responder status).

**Ask:** `POST /api/v1/simulations/{scenario_id}/devices/{device_id}/heartbeat`
`{ "mode": "simulation", "sent_at": "<ts>", "battery_pct": 87 | null }`, and in state:

```json
"devices": [{ "device_id": "sim-device-responder-001", "responder_id": "responder-001",
              "last_seen_at": "<ts>", "status": "online" | "stale" | "offline" }]
```

Status derived server-side from `last_seen_at` (e.g. stale > 10 s, offline > 30 s). Emit
`simulation.device_status_changed` on transitions so the dashboard can alert.

## 3. Vitals observations  *(priority: medium — stretch)*

**Why:** heart rate from a contact sensor (Grove ear-clip / MAX30102 via the Arduino) or a
responder report. UI today: operator-logged pulse in browser storage, or PREVIEW mock sensor.

**Ask:** `POST /api/v1/simulations/{scenario_id}/casualties/{casualty_id}/vitals`

```json
{
  "mode": "simulation",
  "id": "vitals-report-001",
  "observed_at": "<ts>",
  "source": "contact_sensor" | "responder_report" | "operator_observed",
  "device_id": "sim-device-responder-001" | null,
  "heart_rate_bpm": 112 | null,
  "respiratory_rate_bpm": 18 | null,
  "confidence": 0.9 | null
}
```

State: `vitals: { "<casualty_id>": [latest VitalsObservation] }`, event `simulation.vitals_recorded`.
**Must not** change priority, scene revision or approvals; vitals are evidence for the human,
like camera signs. The OR-Tools input stays human assessments only.

## 4. Operator field observations  *(priority: medium)*

**Why:** bleeding / visible wound / breathing / free-text notes the operator logs from the feed
or a radio report. UI today: browser-only (`src/lib/fieldNotes.ts`), lost on another machine.

**Ask:** `PUT /api/v1/simulations/{scenario_id}/casualties/{casualty_id}/field-notes`

```json
{ "mode": "simulation", "actor": "demo-command",
  "bleeding": "yes" | "no" | "unknown", "visible_wound": "yes" | "no" | "unknown",
  "breathing": "yes" | "no" | "unknown", "note": "bleeding left leg" }
```

Recorded with `source: "human"` and timestamp; never feeds the solver.

## 5. Video stream registry  *(priority: low)*

**Why:** feeds are configured per browser today (local camera or URL). A registry lets every
dashboard show the same bodycam/building feeds.

**Ask:** in state, `streams: [{ "stream_id", "kind": "bodycam" | "building", "responder_id" | null,
"camera_id" | null, "url", "format": "mjpeg" | "mp4" | "webrtc" }]`. The backend only lists URLs;
it does not proxy video.

## 6. Annotated surveillance stream  *(AskAr — low)*

An MJPEG endpoint from the perception process with YOLO boxes and track IDs drawn, e.g.
`http://127.0.0.1:5000/stream.mjpg`. Paste it into the building camera tile's source today.

## 7. Building floor per position  *(priority: medium — multi-floor building map)*

**Why:** the Command Center now has building floor plans (L1 ground, L3) and a floor picker that
shows how many people are detected on each floor. The contract has no floor: every `Position`
shares the scenario `frame_id`, and the occupancy grid / A* routes describe one floor. Today the UI
puts everyone on L1 (the routing floor); a PREVIEW flag spreads people over floors with mock data
(`src/lib/floors.ts`).

**Ask:** an optional `floor_id` on `Position` (e.g. `"L1"`, `"L3"`), set from the camera/zone that
produced the observation or from the responder's reported position, plus a `floor_id` on
`OccupancyGrid` so routing knows which floor it covers (cross-floor routing can come later).

## 8. Safe shared map updates  *(priority: medium — building walls / Block route)*

**Why:** building walls publish a new routing map through `PUT /map`. The dashboard re-reads the
live revision right before publishing and aborts if it changed, but two operators can still race
in the gap between that read and the write.

**Ask:**

- An optional `if_revision` on `PUT /map` (or an `If-Match` header carrying the current map
  revision): reject with 409 when the live map is no longer that revision.
- Planner endpoint snapping: when a responder or casualty position touches a blocked cell, plan
  from/to the nearest free cell within a small radius and report the snap in the route. Today the
  UI can only flag "NO ROUTE (ON WALL)" for casualties (no API moves them) and offer an opt-in
  demo correction for responders. Walls are never opened under people.
 (no request needed)

- Camera observed signs (`aegis-cv-signs/0.1`): shown per victim with source, value, confidence,
  observed time/age, `not_testable`, stale; literal wording per `docs/CAMERA_SIGNS_INTEGRATION.md`.
- Replan diff: computed client-side from `state.proposals` (could later use
  `GET /intelligence` → `previous_recommendations`).
