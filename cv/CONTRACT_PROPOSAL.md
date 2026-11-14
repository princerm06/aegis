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

| Sign | `value` | Extra fields | How it is measured |
| --- | --- | --- | --- |
| `posture` | `standing` / `sitting` / `lying` / `unknown` | – | Pose keypoints on the floor plane, 15-frame vote (already used internally) |
| `movement` | `moving` / `still` / `not_testable` | `still_for_s` | Keypoint motion normalized by body size. `still_for_s` restarts after the camera moved |
| `response` | `responded` / `no_response` / `not_tested` | `command_at` | Limb motion in the window after the operator marks a spoken command |
| `eyes` | `open` / `closed` / `not_testable` | – | MediaPipe Face Landmarker blink scores on the face crop; `not_testable` when the face is turned away or too small |
| `breathing` | `motion_detected` / `no_motion_detected` / `not_testable` | `rate_bpm` (only when detected) | Optical flow on the chest while the person is still, clear spectral peak in 6–40 /min in both halves of a 15 s window. `no_motion_detected` = chest motion under 0.3 px (low confidence); irregular motion = `not_testable`. Tuned to miss faint breathing rather than report breathing that is not there |
| `visible_blood` | `detected` / `not_detected` / `not_testable` | `regions`, `method` | Deep-red pixels (green and blue ≤ 0.35 × red, which excludes skin and lips) inside body regions from keypoints (`color`), later a fine-tuned model (`model`). Red clothing triggers it |

**Moving cameras (bodycam, handheld).** These add `camera_motion`, measured from
the background:

```json
"camera_motion": {"value": "steady", "speed_px_s": 34.2},
"breathing": {"value": "not_testable", "confidence": 0.0, "reason": "camera_moving"}
```

| `camera_motion.value` | Effect on the other signs |
| --- | --- |
| `still` | As on a fixed camera |
| `steady` (sway) / `unknown` (too little background) | Movement and response measured relative to the body (`still` confidence ≤ 0.6); breathing `not_testable` |
| `moving` (walking, turning) | Movement and response `not_testable` / `not_tested` too |

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

## Not proposed

- **Heart rate:** the brief excludes camera-based heart rate (rPPG), and a
  webcam at 2–3 m cannot measure it reliably.
- **CV output:** no injury type, diagnosis, severity or priority comes from
  CV.
- **Automatic priority suggestions** from signs: on hold until validated.
