# Camera observed signs integration (SIMULATION ONLY)

This is an additive backend integration for the proposed `aegis-cv-signs/0.1` observation payload in `cv/CONTRACT_PROPOSAL.md` (PR #7).

## Wire contract

`POST /api/v1/simulations/demo/cameras/{camera_id}/observations` may contain an optional `signs` object only when `track.state` is `visible`. The legacy `POST /observations` route supports the same optional evidence field. The validated fields are `schema`, `window_s`, `posture`, `movement`, `response`, and optional `eyes`, `breathing`, `visible_blood`, `camera_motion`. Every sign carries a detector confidence; invalid names, values or confidence ranges return HTTP 422.

The track's `latest_observation.signs` contains the newest report (or null when occluded/lost), while `latest_visible_signs` and `signs_observed_at` preserve the last observed sign evidence with an explicit timestamp. `simulation.observation_received` events also include `observation.signs`.

**No inferred clinical labels or priority.** Visual signs are unvalidated advisory measurements, not medical observations, diagnoses, hemorrhage confirmation, breathing determinations, or clinical probabilities. The backend does not calculate a severity or suggested triage category. Human assessments using `source: "human"` remain the only triage data passed to OR-Tools. Updating `signs` alone cannot invalidate an approval or change scene revision; existing location/track eligibility changes still can.

Dashboard: show sign source, value, confidence, time observed and age; explicitly distinguish `not_testable` and stale results. Avoid labels like "not breathing" or "severe hemorrhage"; use literal "chest motion not detected" and "red-color pattern detected".

## Live camera registration

`scripts.live_demo --base-url URL --prepare-only` resets the synthetic scene, registers the phone's predefined zone, and **does not register Logitech**. The live CV process can register `logitech-1` with its real external calibration reference without encountering a placeholder mapping conflict. To have preparation register Logitech explicitly, use:

```sh
uv run python -m scripts.live_demo --base-url http://127.0.0.1:8000 --prepare-only --calibration-ref taped-floor-real-v1
```

The reference must exactly match the value used by the live CV publisher. A calibration is an external assertion: the backend does not measure its error. Never run a reset or full synthetic rehearsal while the physical demonstration is active.

## Verify

```sh
uv run pytest -q
uv run pytest backend/tests/test_camera_signs.py backend/tests/test_live_demo.py -q
```

A PR-only code review or GitHub mergeability check is not a substitute for executing these tests.
