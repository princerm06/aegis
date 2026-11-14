# Frontend — AEGIS Command Center (Hillary)

Next.js 15 · TypeScript · Tailwind v4 · SVG map. Built against `../shared/CONTRACT.md`
and the simulation API documented in `../docs/INTEGRATION.md` ("Hillary — human
controls and state synchronization").

**SIMULATION ONLY.** Every action targets `/api/v1/simulations/demo`. Approvals are
local operator decisions (`authenticated: false`), not production authorization.

## Run

```sh
# terminal 1 — backend (repo root)
uv run uvicorn backend.app.main:app --host 127.0.0.1 --port 8000

# terminal 2 — dashboard
cd frontend
npm install
npm run dev            # landing page http://localhost:3000 · dashboard http://localhost:3000/command

# with both running
npm run smoke          # end-to-end API check through the dev proxy
npm run e2e            # browser click-through (uses your installed Google Chrome)
npm run typecheck && npm run lint && npm run build
```

Config (all optional):

| Variable | Default | Purpose |
| --- | --- | --- |
| `AEGIS_BACKEND_URL` | `http://127.0.0.1:8000` | Target of the same-origin `/aegis-api/*` rewrite (backend has no CORS) |
| `NEXT_PUBLIC_AEGIS_WS_URL` | `ws://<page host>:8000` | WebSocket base; connects directly to the backend |
| `NEXT_PUBLIC_AEGIS_SCENARIO` | `demo` | Simulation scenario ID |

## What's on screen

The operator at the command center watches the scene and relays guidance to firefighters.

```
┌ AEGIS · loop chips (detect → … → adapt) · SIMULATION · 🔔 · ☰ Setup · live · operator · Reset ┐
├──────────────────────────────────────────────┬──────────────────────────────┤
│ SCENE MAP (~68% × ~70%)                      │ BODYCAM GRID (2×2)           │
│ victims, firefighters, routes, CV detections │ Alpha · Bravo · Building · + │
│ Mark <firefighter> · Block route             │ COMMS → selected firefighter │
├──────────────────────────────────────────────┼──────────────────────────────┤
│ VICTIM STRIP: summary + one card per victim  │ COMMAND DOCK: recommendation,│
│ (click a card → editor)                      │ replan diff, approve/dispatch│
└──────────────────────────────────────────────┴──────────────────────────────┘
```

| Area | Shows | Operator actions |
| --- | --- | --- |
| Scene map | Victims (priority colour; UNASSESSED = dashed "?"), firefighters, walls/grid, proposed vs dispatched A* routes, previous plan (dotted) after a replan, CV detections, optional floor-plan image | **Block route** (B) toggles cells → `PUT /map`; **Mark** (M) then click → `PATCH /responders/{id}` position |
| Bodycam grid | One tile per firefighter + building camera + spare; target and task status overlay | Click a tile (or 1–9) to select that firefighter; per-tile source; 🔇/🔊 to hear the firefighter |
| Comms | Selected firefighter's availability, voice link, quick commands, turn-by-turn directions from the A* route, comms log | Hold to talk (button or Space); spoken commands/messages; availability → `PATCH /responders/{id}` |
| Victim strip | Totals (priorities, unassessed, blood seen, pulse known); per victim: human priority, pulse, operator observations, top camera signs, task | Card → editor: priority → `POST /assessment`; bleeding/wound/breathing/notes/reported pulse; full camera signs with confidence + age. Unconfirmed detections → **Confirm new victim** / **Link** (`POST /associations`) |
| Command dock | Recommendation, expiry, **what changed since the previous revision**, tasks with ack source (**via device** vs **simulated**), device status | Approve exact revision (A) → `POST /decision`; separate **Dispatch** (D) → `POST /dispatches`; ↻ replan; details › |
| Details drawer (L) | Full recommendation + grounded explanation, task progress with SIMULATE rehearsal controls, incident timeline | — |
| Alerts | New camera detection, new victim, acknowledgement, responder offline, approval invalidated/expiring, camera lost sight of a victim | Sound (🔔 to mute) + flash on the related card/tile/map marker |
| ☰ Setup | Preview data toggles, fallback mode, floor-plan upload/opacity, incident summary export, shortcuts | — |

### Evidence sources on a victim card (never mixed up)

| Label | Source | Can change priority? |
| --- | --- | --- |
| Priority badge | Human (`source: human`) via the editor | — it *is* the priority |
| `cam: …` lines | Backend camera signs (`aegis-cv-signs/0.1`), literal wording, confidence, age, STALE | No |
| bleed / wound / breath chips, notes, "reported" pulse | Operator observations (browser storage until backend request #4) | No |
| `♥ … PREVIEW` | Mock sensor (Setup → preview) until backend request #3 | No |

### Video and audio

- Each tile's **Connect** offers any camera attached to this computer (Logitech webcam; an iPhone
  via macOS Continuity Camera works as a stand-in bodycam) or a stream URL (MJPEG as image,
  MP4/WebM as video). For bodycams, "hear via" picks an audio input so command can listen.
- **Voice link**: the operator's mic plays out of a chosen output (e.g. a Bluetooth speaker on the
  firefighter, paired to this computer). Muted unless the talk button/Space is held.
- Quick commands and directions use speech synthesis on the **system default output**.
- Directions are map-relative (north = up); responder heading isn't tracked.

### Preview data and fallback

- Features the backend doesn't support yet are listed in [`BACKEND_REQUESTS.md`](./BACKEND_REQUESTS.md).
  Their mock data is **off by default**; when on, a banner and per-value `PREVIEW` tags are shown.
- **Fallback** (Setup, or the offline screen) shows the seeded `shared/mock` scenario read-only
  with a banner and all actions disabled. For recorded footage, put a video in `frontend/public/`
  and use its URL as a tile source.

### Keyboard shortcuts

`A` approve · `D` dispatch · `1–9` select firefighter · hold `Space` talk · `B` block route ·
`M` mark position · `L` timeline · `?` help · `Esc` cancel. Ignored while typing.

## Sync model

- WebSocket `/ws`: the first `simulation.snapshot` sets state; each later event is
  deduplicated by `event_id`/`sequence`, appended to the timeline, and triggers a
  `GET /state` refetch. The UI never builds state from events itself.
- Every mutation response carries the authoritative post-state, which is applied
  only if its `sequence` ≥ the one already shown.
- If the socket drops, the UI polls `/state` + `/events` every 2 s and reconnects
  with backoff. The header shows `live · ws` / `polling` / `backend offline`.

## Safety rules enforced in the UI

- `medical_assessment: null` is shown as **UNASSESSED** (dashed "?"), never as a
  severity. Severity comes only from the operator's click and is sent with `source: "human"`.
- CV tracks show confidence, track state, age, and **STALE** after 30 s. Zone-based
  positions are labelled *estimate*. Detections never create casualties without an
  explicit operator confirmation.
- Recommendations are labelled advisory. Approve sends the exact `proposal.revision`.
  Dispatch is a second explicit click using the stored `approval_id`. Nothing
  dispatches on load, selection, a recommendation, or an acknowledgement.
- Idempotency keys are deterministic per epoch/proposal/revision, so a double click
  replays (`duplicate: true`) instead of acting twice. 409s are shown verbatim and
  require a fresh human decision.
- Simulated device events stay behind the "rehearsal" toggle and are striped/labelled.
  Real Arduino acknowledgements arrive through the backend (`/device-events`) and
  show up the same way.

## Layout

```
src/app/            layout, page (landing), command/page (dashboard), globals.css (design tokens)
src/components/landing/  landing page (illustrative data is always labelled):
                    ScrollExperience (motion shell), motion (SectionTransition, ParallaxDecor,
                    Reveal, MotionCard), FloatingSectionNavigation, Hero + InteractiveHeroScene,
                    Problem, Workflow3D, TechnologyArchitecture, HumanLoop, Hardware, Closing
src/components/     CommandCenter (layout + shortcuts), FeedStage (click-to-swap primary/secondary feeds;
                    feeds stay mounted, only their slot changes), SceneMap, CamGrid (camera feed views), VideoFeed, CommsPanel,
                    VictimStrip, CommandDock, Overlays (alerts, setup, help), ProposalPanel,
                    TasksPanel, Timeline, LoopStrip, ui, theme (ThemeProvider/ThemeToggle — light/dark
                    shared by landing + dashboard, saved in localStorage "aegis.theme")
src/lib/            types.ts (contract mirror), api.ts, useSimulation.ts, format.ts,
                    insights.ts (camera-sign wording, ack source, replan diff), useAlerts.ts,
                    navigation.ts (route → directions), fieldNotes.ts, preview.ts (mock adapters),
                    fallback.ts (+ fallbackScenario.json copy of shared/mock), report.ts (export)
scripts/smoke.mjs   API check via the proxy
scripts/e2e.mjs     browser click-through (puppeteer-core + system Chrome)
BACKEND_REQUESTS.md proposed endpoints for features currently on preview data
```
