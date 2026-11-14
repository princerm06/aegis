# CV and mapping (AskAr)

Webcam → YOLO pose → ByteTrack IDs → floor mapping → camera observations for
the backend, as defined in `../shared/CONTRACT.md`, `../docs/INTEGRATION.md`
and `../docs/LIVE_DEMO.md`.

The CV module reports **observations with confidence** only: where a person is
on the floor, how sure the detector is, whether the track is currently seen,
and (with `--signs`) DARPA-style observed signs: posture, movement, response
to a command, eyes, breathing motion and visible blood. It never outputs
injury type, diagnosis, severity, triage or heart rate. The backend stores
and shows the signs; only the medic-confirmed priority is used for dispatch
(`CONTRACT_PROPOSAL.md`). All scenes and casualties are simulated.

## Status

| Phase | Scope | State |
| --- | --- | --- |
| 0 | Detection test harness | Done |
| 1 | Camera → YOLO → ByteTrack → mapping → publisher | Done; staged-floor calibration needs the tape marks |
| 1 | Phone as camera 2 (`phone-1`, predefined zones) | Done, verified live together with the Logitech |
| 1 | Rooms without calibration (`rooms.yaml`) and bodycam (`bodycam.yaml`, camera-motion aware signs) | Done, unit-tested and checked on handheld clips; needs a real bodycam walk-through |
| 3 | Record / replay fallback, evaluation tool | Done; staged scenario runs (lying, sitting, occlusion) still to record |
| 2 | Observed signs: posture, movement, command response, eyes, breathing, visible blood | Done and unit-tested; live-checked for eyes, movement and blood (no false positive on skin). Sending needs the backend change in `CONTRACT_PROPOSAL.md`; breathing and closed eyes still need a staged lying test |

## Setup

The CV module has its own environment so the backend's `pyproject.toml` /
`uv.lock` stay untouched. Run everything from the **repository root**.

```sh
uv venv --python 3.11 cv/.venv
uv pip install --python cv/.venv/bin/python -r cv/requirements-dev.txt
source cv/.venv/bin/activate
```

`requirements.txt` is enough to run the pipeline; `requirements-dev.txt` adds
the backend's packages so the tests can validate payloads against the
backend's own models. Without `uv`: `python3.11 -m venv cv/.venv`,
activate it, then `pip install -r cv/requirements-dev.txt`.
Model weights download on first use into `cv/models/` (git-ignored).

### Camera

The app that launches Python needs camera access: **System Settings → Privacy &
Security → Camera**, enable Terminal / iTerm / Cursor, then restart that app.
The first run triggers the prompt and fails; run again after allowing.

Camera indices follow AVFoundation's order and **change when devices connect**
(the iPhone moved from 3 to 2 during testing). Check before each session:

```sh
ffmpeg -hide_banner -f avfoundation -list_devices true -i "" 2>&1 | grep "\]"
```

On the dev MacBook: 0 = Logitech C922, 1 = FaceTime, 2 = iPhone (Continuity
Camera), 3 = iPhone Desk View. Pass `--camera N` when it differs.

## 1. Calibrate the staged floor into the demo map

We keep the backend's 24 × 20 m demo map (frame `demo-floor-1`, wall at
x = 8..9, y = 2..13, responders at (0, 0) and (20, 0)) and place the staged
floor inside it with a fixed offset. Recommended placement for a floor up to
4 × 4 m: **`--offset 12 6`**, covering x 12–16, y 6–10. That area is walkable
(checked against the backend's occupancy grid) and contains `casualty-actor-a`
at (14, 8) from `docs/LIVE_DEMO.md`.

1. Mount the Logitech high (2 m or more), angled down at the staged floor.
2. Tape 5–6 marks on the floor. Measure each from the staged area's front-left
   corner, in meters: +x to the right, +y away from the camera.
3. Start the backend and reset the demo scenario (step 2 below), so the
   calibration can be checked against its map.
4. Run:

```sh
python -m cv.tools.calibrate --camera 0 --offset 12 6
```

5. SPACE freezes the live frame. Click each tape mark (right-click or Backspace
   undoes, `r` resets), then press Enter.
6. Type each point's local `x y` in meters; the offset is added for you.
7. Read the per-point error table and the map check:
   - `OK: staged area x 12.00..16.00 m, y 6.00..10.00 m ... is walkable`, or
   - `NOT WALKABLE` with the blocked cells, out-of-map samples or obstacles.
     With `--yes` such an area is never saved. Fix the offset or the points.
8. Check that the blue 0.5 m grid lies on the floor, then answer `y` to save
   `cv/config/homography.json`.

With exactly 4 points the fit is exact, so the error is 0 by construction. Use
5–6 points for a real accuracy figure. Re-calibrate after moving the camera or
changing its resolution; the same aspect ratio at another resolution is
rescaled automatically. `--no-map-check` skips the map check (no backend).
Non-interactive form: `--image frame.png --points "u,v;..." --world "x,y;..." --yes`.

## 2. Run the pipeline

```sh
uv run uvicorn backend.app.main:app --host 127.0.0.1 --port 8000      # Prince's backend
uv run python -m scripts.live_demo --base-url http://127.0.0.1:8000 --prepare-only

python -m cv.main                                  # Logitech (logitech-1, calibrated)
python -m cv.main --config cv/config/phone.yaml    # phone (phone-1, zones), second terminal
python -m cv.main --mock-backend                   # print the JSON, no backend needed
```

| Flag | Meaning |
| --- | --- |
| `--camera N` | Camera index (config: `camera.index`) |
| `--camera-id ID` | Backend camera ID (config: `publisher.camera_id`) |
| `--source FILE` | Replay a recording, paced to its measured FPS; `--loop` restarts it, `--no-pace` runs flat out |
| `--mock-backend` | Log requests to stdout and `cv/artifacts/mock_<camera>.jsonl` instead of sending (`--quiet-mock`: file only) |
| `--summary-json FILE` | Also write the measured run summary as JSON |
| `--backend-url URL`, `--homography FILE`, `--model NAME`, `--config FILE` | Overrides |
| `--no-display`, `--duration S`, `-v` | Headless, stop after S seconds, debug logs |

Without a calibration the Logitech pipeline still detects and tracks, but
publishes nothing and shows `UNCALIBRATED`. Tunables live in
`config/pipeline.yaml`, `config/phone.yaml` and `config/bytetrack.yaml`;
unknown keys are rejected.

**Debug window** (with `--signs` the responder view opens instead; **v** switches):

- Boxes and skeletons, with the track ID, state, whether the person was seen
  this frame, confidence and posture.
- Floor coordinates in meters, or `zone <id> (estimate)` for the phone.
- The ground point (cyan dot), calibration marks or zone outlines, and a
  top-down mini-map.
- A HUD with FPS, camera ID and mapping, publisher counters and warnings such
  as `UNCALIBRATED` and `REPLAY`.

Green means seen this frame, orange occluded, red stale. Press `q` or Esc to
quit. The run summary prints FPS, inference time, track counts, accepted and
rejected reports and capture-to-sent latency.

Each camera registers itself and starts posting. The operator then links each
track to a casualty with `POST /associations`, using the canonical `track_id`
from `GET /state`: `sim-camera:10:logitech-1:<session>-t<n>` or
`sim-camera:7:phone-1:<session>-t<n>`. The two cameras never share IDs, and
there is no cross-camera matching. The CV module never associates tracks or
creates casualties.

### Phone as camera 2

The phone (iPhone Continuity Camera) has no floor calibration, so
`config/phone.yaml` uses **predefined zones**:

- **Zone polygons:** each zone is a polygon in normalized image coordinates
  (0..1) plus a reference point on the map.
- **What is reported:** a person whose ground point falls in a polygon is
  reported as that `zone_id`.
- **What the backend does:** it places the person at the zone's reference
  point and labels it `zone_estimate`, so no precise coordinate is claimed.
- **Zone changes:** the reported zone only changes after 5 consecutive frames
  in the new zone (`tracking.zone_switch_frames`), so a person on a border
  doesn't flicker between zones.

The default is the whole frame as `zone-b` at (3, 2), identical to Prince's
`--prepare-only` registration, so the two never conflict. To split the view,
add zones with `image_polygon` (see the comments in `phone.yaml`). The backend
fixes a camera's mapping until the scenario is reset, so change zones only
before a reset. If the phone gets a real floor calibration, set
`mapping.mode: calibrated` and a `homography` file in its config instead.

### Any room without calibration, and bodycams

Tape and a homography are only needed for metre positions on one fixed floor.
For tests in different rooms and for a moving camera, each **room is a zone**
with one point on the demo map (`room-1` (3, 2), `room-2` (14, 8), `room-3`
(18, 14), all walkable). Everyone a camera sees is reported in its current
room, which the backend places at that room's point as a `zone_estimate`.

```sh
python -m cv.main --config cv/config/rooms.yaml --room room-2 --signs     # fixed camera in a room
python -m cv.main --config cv/config/rooms.yaml --room room-3 --camera 1 --camera-id room-cam-2
python -m cv.main --config cv/config/bodycam.yaml --room room-1           # moving camera
```

- **Picking the room:** `--room` at start; keys **1–3** in the window when the
  wearer enters another room (`mapping.zone_select: keys`). The HUD shows
  `ROOM ...`. Switching applies at once to everyone visible, so press it on
  entering, not before leaving.
- **Camera IDs:** one per physical camera. A camera's room list is registered
  once per epoch, so keep the list the same until a reset.

**Bodycam signs** (`signs.moving_camera`, on in `bodycam.yaml`): the camera's
own motion is measured from background corners outside the people's boxes
(`signs/camera_motion.py`) and shown on the HUD:

| Camera | Measured on | Signs |
| --- | --- | --- |
| `still` (≤ 10 px/s) | Fixed UR Fall camera: 82–100% of frames | All, breathing with the camera's own shift removed |
| `steady` (≤ 80 px/s) | Handheld stock clips: 10–60 px/s | Movement and response relative to the body (sway does not count; whole-body shifts like crawling do not either); breathing not testable |
| `moving` | Walking camera: 57–71% of frames | Movement, response and breathing paused: `not_testable`, `reason: camera_moving` |
| `unknown` | Too little background, e.g. a casualty filling the frame | Like `steady` |

Eyes, blood and posture are judged per frame and keep working. Stillness
restarts after the camera moved, and a command given while the camera moves
gives `not_tested`, never `no_response`. On the handheld clips the body-relative
mode reads people as still as often or more often than the fixed-camera mode
(sleeping man 59% vs 48%). Remaining "moving" there is pose noise on bodies
under blankets. For the bodycam: kneel and hold still for breathing (about 15 s);
looking straight down at someone lying can make posture read upright.

Test a recording: `python -m cv.tools.signs_report --source walk.mp4 --moving-camera`.

## What is published

`POST /api/v1/simulations/demo/cameras/<camera_id>/observations`:

```json
{"mode": "simulation", "id": "s20261114T051005-o000001",
 "observed_at": "2026-11-14T05:10:08.377Z", "confidence": 0.89,
 "track": {"track_id": "s20261114T051005-t1", "state": "visible",
           "first_seen_at": "2026-11-14T05:10:06.362Z", "last_seen_at": "2026-11-14T05:10:06.362Z"},
 "position": {"frame_id": "demo-floor-1", "x_m": 14.23, "y_m": 8.44}}
```

The phone sends `"zone_id": "zone-b"` instead of `position`. Registration is
`calibrated_world` with the `calibration_ref` from `homography.json`
(Logitech), or `predefined_zones` with the zone map (phone).
`publisher.endpoint: legacy` posts a plain `CVObservation` to `/observations`
(calibrated cameras only).

- **IDs:** track IDs are `<session>-t<n>`. The session is the process start
  time (UTC), so IDs are never reused within or across runs. Observation IDs are
  unique per report and reused only for a retry of the same report.
- **States:**
  - `visible`: detected within the last 0.5 s (`occlusion_grace_s`, which
    absorbs one-frame misses).
  - `occluded`: not detected since, but the track is alive.
  - `lost`: not detected for 30 s (`lost_after_s`). Sent once, then the ID is
    retired.
- **Repeated values:** occluded and lost reports repeat the position (or
  zone), confidence and `last_seen_at` of the last report the backend
  accepted, as the contract requires. `last_seen_at` never advances while
  unseen.
- **Position:** floor position of the ground point, smoothed (EMA) with a
  15 cm deadband. It only changes when the person really moves, so detector
  jitter does not invalidate pending approvals.
- **Confidence:** detector confidence smoothed over frames (EMA). It is not
  medical certainty.
- **Rate:** 5 Hz tick. Changed tracks go out on the next tick and unchanged
  tracks every 1 s (`heartbeat_s`), well within the backend's 30 s freshness
  window.
- **Failures:**
  - Backend down: the video loop never blocks. Only the newest report per
    track is kept, retries back off up to 5 s, and a retry reuses the same ID
    and body.
  - Camera registration gone (scenario reset): it registers again.
  - Camera already registered with another mapping (409): it keeps posting
    under that mapping and logs a warning.
  - Other 4xx: logged and dropped.
- **Not published:** tracks without a valid floor position or zone
  (uncalibrated, a point at or above the horizon, outside every zone) are
  omitted rather than guessed.

**Ground point:** standing or sitting people use the bottom-center of the box.
Lying people use the hip midpoint when the hip keypoints are confident, else
the box center. Posture comes from:

1. Torso length on the floor plane, which catches people lying along the
   camera's line of sight.
2. Torso angle and thigh angle.
3. Box aspect ratio when keypoints are missing.

The result is majority-voted over 15 frames.

**Re-attach:** if ByteTrack issues a new ID close to a track that is currently
unseen and not yet lost (0.6 m on the floor; 8 % of the frame diagonal in zones
mode), the old track ID continues, so casualties don't need re-linking after
an occlusion. Set `tracking.reattach: false` to disable it.

## Observed signs (`--signs`)

```sh
python -m cv.main --signs                # compute and show signs; not sent
python -m cv.main --send-signs           # also send them (backend must accept "signs")
```

Each sign is an observation with a confidence and an explicit
`not_testable` / `not_tested`. Wire format and backend asks:
`CONTRACT_PROPOSAL.md`.

**Responder view** (`cv/monitor.py`, the default window with signs; **v**
switches to the debug view with FPS, track states and publisher counters):

- **A box beside each person.** It is tied to the person's chest by a line
  and follows them. There is no side panel: the reading sits next to the body
  it belongs to. The number and color match the dashboard track.
- **Top line:** number, posture, and how long the person has been still.
- **Eye row:** an open, closed or crossed-out eye with `EYES OPEN / CLOSED /
  NOT VISIBLE`, and the response to a spoken command under it.
- **Drop row:** `NO VISIBLE BLOOD` or `BLOOD: HEAD, L ARM`. The body regions
  are outlined in amber on the person.
- **Lungs row:** breaths per minute with a live chest trace, or why there is
  no number yet (`MEASURING 6 s`, `CAMERA MUST BE STILL`, `PERSON MUST BE
  STILL`, `CHEST NOT VISIBLE`).
- **Colors:** white = observed; amber = notable observation (blood seen, no
  chest motion, no response, eyes closed); grey = the camera cannot tell, with
  the reason; cyan = listening after a command, with the time left. A trailing
  `?` marks a low-confidence value.
- **Placement:** a box goes to the side of its person where it covers no other
  box and no face, and moves smoothly. Boxes shrink for more than 2 and more
  than 4 people. People out of view keep a dimmed box for 5 s and are then
  counted in the top bar.

Text and icons are drawn with Pillow (`pillow` in `requirements.txt`).

Colors mark observations, never severity, and there is no heart rate: the
brief excludes camera-based heart rate and a webcam at 2–3 m cannot measure it.
A pulse would need a contact sensor on the casualty. `signs_report
--save-video` writes this same view.

| Sign | How | Needs |
| --- | --- | --- |
| movement | Keypoints vs 1 s ago, divided by body size; `still` after 2 s | Pose visible |
| response | Press **c** in the window right after the spoken command ("raise your hand if you hear me"). `responded` if a limb moves ≥ 0.15 body size within 8 s; `no_response` only if the person was in view for at least half the window | Limbs visible |
| eyes | MediaPipe Face Landmarker blink scores on a face crop rotated level (works for faces lying sideways). Head turned more than 25°: the higher eye score; more than 60°: `not_testable`. The model downloads into `cv/models/` on first use | Face ≥ 36 px across; at 2–3 m use 1080p capture |
| breathing | Optical flow on the chest while still; rate only from a clear rhythm, same in both halves of 15 s | Person still ~11 s, chest visible |
| visible blood | Deep-red pixels (green and blue ≤ 0.35 × red, hue ≤ 10 or ≥ 174) per body region: head, torso, arms, legs | No red, pink or magenta clothing or props: their shadows are blood-colored |

Before the demo, check the actual fake blood under the room light: run
`--signs`, put blood on one actor, and confirm `blood: <region>` appears and
clean skin stays `not detected`. Thin smears look paler. If they are missed,
raise `signs.blood_max_gb_ratio` a little (skin measured ~0.45 in a dim room,
so stay below 0.42). The fine-tuned model (`method: model`) is the next step
once we have labelled footage of our own blood.

Measured at a desk (one person, Logitech 720p): signs add 0.1–0.5 ms median
per frame, 12–16 ms p95 when the eye model runs; pipeline at 24–26 FPS. Eyes
`open` with confidence 0.74–0.95. The first blood detector flagged warm-lit
skin as blood; the green/blue ratio fixed it. On the ultralytics `bus.jpg`
sample the shadowed folds of a magenta shirt (BGR 20, 10, 103) still read as
torso blood: color alone cannot tell them apart, which is why actors must
avoid red and pink, and why the fine-tuned model is worth doing. A seated person working at a
desk moves the chest 5–15 px irregularly, so breathing stays `not_testable`;
that is intended.

### Testing signs on datasets and stock video

```sh
python -m cv.tools.signs_report --source clip.mp4 --expect eyes=closed --expect movement=still
python -m cv.tools.signs_report --source clip.mp4 --command-at 4 --save-video cv/recordings/out.mp4
python -m cv.tools.eyes_eval --closed .../ClosedFace --open .../OpenFace
```

`signs_report` runs detector, tracker and signs on every frame of a file and
prints, per person, how often each sign had each value, the value changes and
the agreement with `--expect`. `eyes_eval` scores the eye detector on labelled
faces. Datasets and clips go in `cv/recordings/datasets/` (git-ignored); only
numbers are saved, to `cv/eval/eyes/` and `cv/eval/signs/`.

Results (Nov 14):

| Data | What it showed |
| --- | --- |
| CEW, 2,423 labelled faces | Face found 98–100%. Eyes 95.5% correct at threshold 0.4 (closed 94.6%, open 96.3%). The old 0.5 missed 15% of closed eyes |
| UR Fall, 10 falls (320×240 Kinect) | 9 of 10 end as `lying`; 6 of 10 keep one ID through the fall. Without floor calibration the tracker cannot re-attach a person after a split |
| Pexels sleeping man, side profile | Eyes `closed` 88% of testable samples. Before the turned-head fix, 0%: the hidden eye's score dragged the mean to "open" |
| Pexels three men lying, overhead | Eyes follow the clip: closed 0–6 s, open about 7–15 s, closing again |
| Mixkit car-crash head wound | Blood `detected` on the head 55% of samples (the wound is out of view in most of the rest). Before the head threshold (2%), 0% |
| Pexels woman with orange roses | Rose edges read as blood: red and orange props are a real limit of the color method |
| Breathing | No stock clip had a fixed camera and a still body for 15 s, so this is still only unit-tested. Record our own |

### Fake casualties (no camera needed)

For building the backend priority and the dashboard while nobody is staging:

```sh
python -m cv.tools.fake_casualties                 # positions to the backend now (5 tracks, 90 s)
python -m cv.tools.fake_casualties --loop          # keep going until Ctrl-C
python -m cv.tools.fake_casualties --send-signs    # once the backend accepts "signs"
python -m cv.tools.fake_casualties --mock-backend --send-signs --fast
    # whole scene instantly -> cv/artifacts/mock_fake-cam-1.jsonl (payload examples)
```

It sends through the same publisher as the camera, as its own camera
`fake-cam-1` on the staged floor (x 12–16, y 6–10), so it never conflicts with
`logitech-1` or `phone-1`. The scripted scene:

| Track | Scripted signs |
| --- | --- |
| t1 | lying, blood on torso and leg, no response, eyes closed, breathing 26 /min |
| t2 | lying, no response, eyes closed, no chest motion |
| t3 | walking, blood on left arm, responds; out of view at 30–34 s |
| t4 | sitting and responding; at 45 s lies down, eyes closed, stops responding |
| t5 | standing, moving, responds, nothing visible |

Commands are "given" at 10 s and 50 s. A sensible priority rule should put t1
and t2 first, move t4 up after about 58 s, and keep t5 last. Until the backend
accepts `signs`, `--send-signs` is rejected with HTTP 422 and the tool says so.
At the end, or on Ctrl-C, every fake track is sent as `lost`.

## 3. Record / replay fallback

Record each staged scenario once the camera is mounted and calibrated. If the
live camera fails during the demo, replay the recording into the same backend
under the same camera ID.

```sh
python -m cv.tools.record --scenario lying_two --duration 30 --countdown 5
python -m cv.tools.record --config cv/config/phone.yaml --scenario phone_zone

python -m cv.main --source cv/recordings/lying_two-<time>.mp4 --loop      # fallback
```

- **Files:** `cv/recordings/<scenario>-<UTC>.mp4` plus a `.json` sidecar with
  the measured FPS, per-frame times, camera, mapping and the calibration valid
  for that camera pose.
- **Replay timing:** replay uses the sidecar's measured FPS (the container FPS
  can be wrong; one test clip reported 28.5 while the measured rate was 29.45)
  and its calibration. `--homography` overrides the calibration.
- **Looping:** `--loop` restarts the clip and keeps reporting, so tracks stay
  fresh.
- **On screen:** the window shows `REPLAY`. Say on stage that it is a
  recording.
- **Privacy:** recordings are git-ignored and stay on the laptop. Never commit
  captured imagery.

## 4. Evaluation (Phase 3)

```sh
python -m cv.tools.evaluate --source cv/recordings/lying_two-<time>.mp4 \
    --truth cv/eval/truth/lying_two.yaml
python -m cv.tools.evaluate --source cv/recordings/standing_two-<time>.mp4 --expected 2
```

The evaluation runs the same detector, tracker and mapping on every frame,
timed by the recording's frame times. The ground truth (copy
`cv/eval/truth/example.yaml`) lists each staged person's tape-measured map
position and optional posture. Each frame, tracks are matched one-to-one to
people within 1 m.

| Metric | Meaning |
| --- | --- |
| everyone detected / exact count | % of frames where every person is matched / the number of tracks equals N |
| detected % per person, longest gap | Recall per person; the longest time without a match |
| ID switches (per minute) | Times a person's matched track ID changed |
| false-positive tracks | Detected tracks matched to nobody |
| mapping error median / p90 / max, bias | Raw per-frame floor position vs the truth point, in cm; bias shows a systematic offset |
| published position error | Same, for the smoothed and deadbanded position actually sent |
| posture agreement | % of matched frames whose posture label equals the truth |
| processing FPS, inference median / p95 | Offline speed (live speed comes from `--summary-json`) |

Results go to `cv/eval/runs/<scenario>-<time>.json` and one row of
`cv/eval/evaluation_runs.csv` (numbers only, safe to commit). With
`--expected N` only the counts are scored.

**Scenario checklist** (30 s each, from the mounted Logitech, positions measured):

| Scenario | Setup |
| --- | --- |
| `standing_two` | Two people standing apart |
| `sitting_two` | Two people sitting on the floor |
| `lying_facing` / `lying_sideways` | One or two people lying, along and across the camera's view |
| `partial_occlusion` | Lying person with a chair or box covering the legs or torso |
| `crossing_occlusion` | One person walks between the camera and a lying person |
| `phone_zone` | Phone camera, `--expected N` |

## Measured (dev MacBook, Apple silicon, MPS)

| Check | Result |
| --- | --- |
| Live C922, 1280×720, yolo11n-pose | 22.6–26 FPS loop, 18–20 ms median inference |
| **Logitech + phone at the same time**, both live to the backend, 20 s | 26.2 and 26.4 FPS; 28 + 16 reports accepted, 0 rejected; capture → accepted median 21.5 / 22.4 ms |
| Phone in zones mode | Backend track `sim-camera:7:phone-1:…`, `location_basis: zone_estimate`, zone-b (3, 2) |
| Record 8 s | 237 frames at 29.45 FPS measured; sidecar with frame times and calibration |
| Replay with `--loop`, 20 s | 2 loops at 29.5 FPS paced, 20 reports accepted, 0 rejected |
| Evaluate (count mode, 1 person at the desk) | Person detected in 100 % of 237 frames, 1 track ID; offline 61 FPS |
| Real backend, 270-frame clip with a 3 s blackout | 64 / 64 reports accepted; tracks went visible → occluded → lost, new IDs afterwards |
| Jitter (±3 px shake + noise), 3 tracks linked to casualties | 65 later reports, **0** scene-revision bumps |
| Backend down for the whole run | Pipeline kept full speed, backed off, no crash |

The two-camera and replay runs used a stand-in calibration, so their positions
are not accuracy figures. Mapping error comes from the staged scenario runs.

## Tests

```sh
python -m pytest cv/tests
```

157 tests. The suite covers:

- Homography math: exact recovery, reprojection error, horizon, degenerate
  points, rescaling.
- Ground-point and posture selection.
- Track state: occluded, stale and lost, ID retirement, re-attach, deadband,
  duplicate IDs.
- Zones: polygon lookup, config validation, zone hysteresis, image-space
  re-attach.
- Publisher: heartbeat, last-accepted repeat, backoff, re-registration, zone
  payloads, legacy format.
- Camera reconnect and file looping.
- The staged-area map check against the backend's demo map.
- Evaluation: matching, ID switches, false positives, error and bias.
- Signs on synthetic data: stillness vs jitter, one-hand command response,
  no_response only when watched, blood on the torso vs skin and pink cloth,
  breathing rate under noise and no false rate from 300 noise-only windows,
  level face crops for sideways faces, signs sent only on visible reports.
- Fake casualties: the scripted timeline, and the whole scene accepted by the
  in-process backend.
- Responder view: amber findings, grey reasons, listening countdown, no
  clinical words or heart rate, any frame size and crowd size; boxes stay in
  the frame beside their person, off faces and off each other, and follow
  smoothly.
- Rooms and bodycam: camera still / steady / moving / unknown from the
  background (a moving person is not camera motion), camera sway not counted
  as movement while a raised hand still counts as a response, a moving camera
  pausing signs, sway at a breathing rate not read as breathing, room keys,
  and every room point walkable on the demo map.
- Contract tests that validate every payload against `backend/app/models.py` and
  `backend/app/simulation/models.py`, and drive the in-process backend through
  full calibrated and zone lifecycles (skipped without `requirements-dev.txt`).

## Phase 0 — detection test harness

```sh
python -m cv.tools.detection_test \
  --model yolo11n-pose.pt --camera 0 --expected 3 \
  --scenario lying_sideways --duration 20
```

| Flag | Default | Meaning |
| --- | --- | --- |
| `--model` | `yolo11n.pt` | `yolo11n.pt`, `yolo11s.pt`, `yolo11n-pose.pt`, or a path |
| `--camera` | `0` | Camera index |
| `--expected` | `1` | Number of people who should be visible (N) |
| `--conf` | `0.4` | Detection confidence threshold |
| `--duration` | `20` | Seconds to run; `0` = until `q` / end of file |
| `--scenario` | `unlabeled` | Free-text label stored with the run |
| `--imgsz` | `640` | Inference size |
| `--width` / `--height` | `1280` / `720` | Requested capture resolution |
| `--device` | `auto` | `auto` (MPS when available), `cpu`, `mps` |
| `--source` | – | Video file instead of the camera |
| `--no-display` | off | Run without a window |
| `--csv` | `cv/eval/detection_runs.csv` | Results file to append to |

Each run prints and appends one row: the model, the % of frames with at least
N people, mean confidence, FPS, and mean / p95 inference latency. The model is
warmed up before timing starts. Values that cannot be computed are left blank,
never written as zero. No frames are saved.

## Integration notes (for Prince and Hillary)

Nothing here changes a shared file.

- **Module location:** the code lives in `cv/`, matching the repository
  layout, and runs as its own process that only talks HTTP to the backend.
  Its tests run separately (`python -m pytest cv/tests`) because the root
  `pyproject.toml` test paths cover `backend/`, `optimization/` and `hardware/`, and
  the CV dependencies (torch, OpenCV) are not in `uv.lock`.
- **`--prepare-only` and `logitech-1`:** `scripts.live_demo --prepare-only`
  registers `logitech-1` with `calibration_ref: external-calibration-demo-001`.
  The backend fixes the mapping for the epoch, so the real pipeline gets a 409
  and keeps posting under that placeholder reference (verified: accepted, but
  the provenance shows the placeholder). Two fixes would work: register
  `logitech-1` from `--prepare-only` with the real reference that
  `cv.tools.calibrate` prints, or skip `logitech-1` when the live CV runs. The
  pipeline registers itself. `phone-1` already matches exactly.
- **Confidence near 0.5:** a person whose smoothed confidence hovers around 0.5
  (partly out of frame) still bumps the scene revision on each crossing, which
  the backend treats as impactful. Keep linked casualties fully in view.
- **Posture, stillness and breathing** are not sent: the backend rejects
  unknown fields. Phase 2 will add a schema proposal in `CONTRACT_PROPOSAL.md`,
  behind a config flag.
- **For Hillary:** per track, the dashboard can already show:
  - `camera.camera_id` and `camera.local_track_id`.
  - `track.state` (visible / occluded / lost) and `last_seen_at` (its age is
    the staleness).
  - `confidence` and `position`.
  - `camera.location_basis`: `calibrated_world` with `calibration_ref`, or
    `zone_estimate` with `zone_id`. Show the zone estimate as approximate.
