# Proposal: observed signs on camera observations (`signs`, v0.1)

Status: **agreed scope** (Prince, Nov 14): signs are visual observations only.
The backend stores and exposes them, the dashboard shows them next to the
casualty's tracking information, and the human-confirmed simulated triage
priority stays the only priority used for optimization and dispatch. Automatic
priority recommendations from signs are on hold until there is a defensible,
validated method. The CV side is built behind `signs.enabled` (compute and
show) and `publisher.send_signs` (put them on the wire); sending stays off until
the backend accepts the payload.

## Why

Our inspiration, the DARPA Triage Challenge, scores systems that report
per-casualty signs (severe hemorrhage, respiratory signs, alertness, trauma
region) to medics. AEGIS keeps the human in the loop:

```
camera ──signs (observations + confidence)──▶ backend (store, expose) ──▶ dashboard
                                         medic assesses ──▶ human priority ──▶ optimization, dispatch
```

1. **CV** reports what it sees, each sign with a confidence and an explicit
   `not_testable`. It never outputs a diagnosis, injury type, severity or
   priority.
2. **The backend** stores the latest signs per track and exposes them with the
   tracking information. It derives nothing medical from them.
3. **The medic** sees the signs in the dashboard and records the priority as
   today (`source: "human"`). Only that priority reaches the solver and the
   responder device.

## Wire format

One optional field on `CameraObservation` (and on `CVObservation` for the
legacy route), present only on `visible` reports:

```json
"signs": {
  "schema": "aegis-cv-signs/0.1",
  "window_s": 10.0,
  "posture":   {"value": "lying",        "confidence": 0.82},
  "movement":  {"value": "still",        "confidence": 0.90, "still_for_s": 42.5},
  "response":  {"value": "no_response",  "confidence": 0.70, "command_at": "2026-11-14T06:10:02.120Z"},
  "eyes":      {"value": "closed",       "confidence": 0.75},
  "breathing": {"value": "motion_detected", "confidence": 0.55, "rate_bpm": 14.0},
  "visible_blood": {"value": "detected", "confidence": 0.65,
                    "regions": ["torso", "left_leg"], "method": "color"}
}
```

| Sign            | `value`                                                   | Extra fields                    | How it is measured                                                                                                                                                                                                                                                                                        |
| --------------- | --------------------------------------------------------- | ------------------------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `posture`       | `standing` / `sitting` / `lying` / `unknown`              | –                               | Pose keypoints on the floor plane, 15-frame vote (already used internally)                                                                                                                                                                                                                                |
| `movement`      | `moving` / `still` / `not_testable`                       | `still_for_s`                   | Keypoint motion normalized by body size. `still_for_s` restarts after the camera moved                                                                                                                                                                                                                    |
| `response`      | `responded` / `no_response` / `not_tested`                | `command_at`                    | Limb motion in the window after the operator marks a spoken command                                                                                                                                                                                                                                       |
| `eyes`          | `open` / `closed` / `not_testable`                        | –                               | MediaPipe Face Landmarker blink scores on the face crop; `not_testable` when the face is turned away or too small                                                                                                                                                                                         |
| `breathing`     | `motion_detected` / `no_motion_detected` / `not_testable` | `rate_bpm` (only when detected) | Optical flow on the chest while the person is still, clear spectral peak in 6–40 /min in both halves of a 15 s window. `no_motion_detected` = chest motion under 0.3 px (low confidence); irregular motion = `not_testable`. Tuned to miss faint breathing rather than report breathing that is not there |
| `visible_blood` | `detected` / `not_detected` / `not_testable`              | `regions`, `method`             | Deep-red pixels (green and blue ≤ 0.35 × red, which excludes skin and lips) inside body regions from keypoints (`color`), later a fine-tuned model (`model`). Red clothing triggers it                                                                                                                    |

**Moving cameras (bodycam, handheld).** These add `camera_motion`, measured from
the background:

```json
"camera_motion": {"value": "steady", "speed_px_s": 34.2},
"breathing": {"value": "not_testable", "confidence": 0.0, "reason": "camera_moving"}
```

| `camera_motion.value`                               | Effect on the other signs                                                                                |
| --------------------------------------------------- | -------------------------------------------------------------------------------------------------------- |
| `still`                                             | As on a fixed camera                                                                                     |
| `steady` (sway) / `unknown` (too little background) | Movement and response measured relative to the body (`still` confidence ≤ 0.6); breathing `not_testable` |
| `moving` (walking, turning)                         | Movement and response `not_testable` / `not_tested` too                                                  |

Posture, eyes and blood are judged per frame and are not affected. A sign that
is `not_testable` or `not_tested` because of the camera carries
`"reason": "camera_moving"`. Fixed cameras send no `camera_motion`.

`regions` ⊆ `head`, `torso`, `left_arm`, `right_arm`, `left_leg`, `right_leg`,
following DARPA's trauma regions. `confidence` is in [0, 1] and is a detector
confidence, not a clinical probability. Values are smoothed over `window_s`.

Rules for consumers:

- **Missing sign or `signs` absent:** not observed. That is never evidence of
  absence. `not_testable` / `not_tested` mean the camera could not tell.
- **Non-visible reports:** `occluded` / `lost` reports carry no `signs`. The
  last visible report's signs age with `observed_at`.
- **Validation:** unknown sign names or values should be rejected (keep
  `extra="forbid"` inside `signs`).

## Backend changes requested (Prince)

1. Add `signs: CameraSigns | None = None` to `CameraObservation` and
   `CVObservation`, with the models above (frozen enums, confidence 0..1,
   `extra="forbid"`).
2. **Store without disturbing approvals:** store the latest signs per track
   without bumping the scene revision on every change; signs never feed the
   solver.
3. **Expose** them in state (per track, with `observed_at`), so the dashboard
   can show each sign's age.

## Dashboard (Hillary)

Per casualty:

- Each sign with its confidence and age, next to the tracking information.
- The human-confirmed priority as today; signs never set or suggest it.

Show `not_testable` as grey, not as normal, and never as "fine": a person the
camera cannot assess is not evidence of anything. `cv/monitor.py` shows one
layout (rows: blood, breathing, response, eyes, movement).

## History signs (proposed next, for Prince)

Eight optional keys on top of the current `signs`. They answer the questions a
responder asks first (did they walk, did they fall, are they waving for help,
are they face down, is someone already with them, is the bleeding getting
worse, has anything changed, is a limb not moving), all as things seen on
camera. The human still assigns the simulated triage priority. **Accepted by
the backend since PR #12**; CV sends them with `--send-signs`
(`publisher.send_history`, on by default).

```json
"fall":          {"value": "seen", "confidence": 0.7, "at": "2026-11-15T14:03:12.400Z"},
"walking":       {"value": "not_seen", "confidence": 0.5},
"limbs":         {"value": "some_not_moved", "confidence": 0.45, "window_s": 30.0, "not_moved": ["left_leg"]},
"waving":        {"value": "seen", "confidence": 0.6, "at": "2026-11-15T14:03:20.000Z"},
"face_down":     {"value": "seen", "confidence": 0.6, "for_s": 12.0},
"person_nearby": {"value": "seen", "confidence": 0.6, "for_s": 42.0},
"blood_spread":  {"value": "growing", "confidence": 0.6, "window_s": 30.0, "change_pct": 45},
"changes":       [{"sign": "eyes", "from": "open", "to": "closed", "at": "2026-11-15T14:03:40.000Z"}]
```

| Key             | Values                                                      | What it means                                                                                                                                                                                                                                                                     |
| --------------- | ----------------------------------------------------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `fall`          | `seen`, `not_seen`, `not_testable`                          | The head dropped by at least half the body height within 1 s from standing or sitting, and the person stayed down. `at` is when the drop started. Lying down slowly or sitting down is not a fall.                                                                                |
| `walking`       | `seen`, `not_seen`, `not_testable`                          | Standing and moving across the floor (at least 0.8 body heights in 4 s). `at` is the last time.                                                                                                                                                                                   |
| `limbs`         | `all_moved`, `some_not_moved`, `none_moved`, `not_testable` | Which arms and legs moved relative to the body in the last 30 s. `not_moved` lists the ones that did not. It shows an absence of movement only, not a cause.                                                                                                                      |
| `waving`        | `seen`, `not_seen`, `not_testable`                          | A hand above the shoulder swinging back and forth (lying: an arm held out and swinging), with or without a command. `at` is the last time. A swinging arm while walking is not waving.                                                                                            |
| `face_down`     | `seen`, `not_seen`, `not_testable`                          | Lying with the body seen, wide across the shoulders, the head in the picture and no face for 3 s. Lying on the side with the back to the camera is `not_testable`, not face down. A face turned sideways on the floor may show the nose and read `not_seen`. `for_s` is how long. |
| `person_nearby` | `seen`, `not_seen`, `not_testable`                          | Someone lying or sitting has another person who is not lying within about 1.5 m (or half a body length of their box without floor calibration) for at least 2 s, or someone covering part of them (a helper bent over them, even when the detector then sees only one person). `not_testable` when the person is standing. It does not say who that is. |
| `blood_spread`  | `growing`, `stable`, `shrinking`, `not_testable`            | The red area seen now compared with 30 s ago, relative to body size. It is sent only when blood colour is detected, and the colour detection limits on `visible_blood` apply to it too.                                                                                           |
| `changes`       | up to 5 entries                                             | The last notable changes of the existing signs, each held for at least 3 s, for example eyes from `open` to `closed`, or response from `responded` to `no_response`.                                                                                                              |

No confidence goes above 0.9 (limbs, waving, face down and person nearby 0.6,
blood spread 0.8). A moving camera makes `fall`, `walking` and `limbs`
`not_testable` (`reason: camera_moving`) and starts them over; waving is
measured relative to the shoulder, so it keeps working.

The models are in `cv/history_contract.py` (built on `CameraSign` and
`CameraSigns` from `backend/app/models.py`; `extra="forbid"` kept). A CV test
checks a real snapshot against them, so the CV output and this proposal stay
in sync. To accept the keys:

1. Copy the eight sign classes and the fields of `CameraSignsWithHistory` into
   `backend/app/models.py`. The schema can stay `aegis-cv-signs/0.1`, since
   every new key is optional.
2. Nothing else changes: they are stored and exposed with the other signs and
   never feed the solver or the priority.
3. Tell CV, and we turn on `--send-history`. (Done in PR #12; on by default.)

For Hillary: show a seen `fall`, `face_down` and `waving`, a `growing`
`blood_spread`, a limb in `not_moved` and the latest entry in `changes` as
short notes with their age (as `cv/monitor.py` does under the gauges, for
example "Fall seen 1:20 ago", "Waving now"). Show `person_nearby` ("Someone
next to them for 0:42") and `walking: seen` as plain notes.

**Last seen (no new field).** Occluded and lost reports already carry
`last_seen_at` and the zone (room). For people out of view, the dashboard can
show "Last seen 2:10 ago in Room 2" instead of hiding them, so a responder
knows where to look. The CV window lists them the same way in its top bar
for 10 minutes.

Measured so far: `fall` was seen in 10 of 10 UR Fall clips once a person who
falls keeps their track (before that fix, 8 of 10). There were no false falls
on the 6 stock clips of people lying, sleeping or in a car crash.

## Not proposed

- **Pulse:** the camera view shows a pulse estimate from skin colour
  (`cv/signs/pulse.py`), but it is not proposed for the contract until it is
  checked against a pulse reference at the demo distance.
- **CV output:** no injury type, diagnosis, severity or priority comes from
  CV.
- **Automatic priority suggestions** from signs: on hold until validated.
