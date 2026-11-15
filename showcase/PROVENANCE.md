# Intelligence context: provenance and boundaries

These are curated public-safe excerpts and explanations, not operational imports.
The static application reads only `showcase/src/lib/sandbox/context.ts`.
No build reads source reports outside showcase; no raw dataset, video, checkpoint,
device configuration or inference code is shipped. Source references below were
reviewed at repository commit `d922b09e45a2114651a4acf6c30df468d465d264`.

## Camera cards: synthetic scenario fixtures

`CAMERA_OBSERVATIONS` contains four invented identities (`SIM-P01`–`SIM-P04`)
mapped to this sandbox's C1–C4. Camera names, visibility, posture, movement and
T+ timestamps are **synthetic**, never recorded detections or real observations.
The scenario snapshot is frozen at T+00:12; each timestamp is a last-observed
scenario offset, not a wall-clock timestamp or a claim of live freshness.
Partially occluded/lost tracks have not-testable posture and movement. No numerical
confidence or automated clinical priority is claimed. Explicit human confirmation
is shown from the existing reducer; fixture observations never change priorities,
availability, routes, approvals, dispatch or task events.

This presentation follows the uncertainty boundary documented in
`cv/CONTRACT_PROPOSAL.md`: non-visible reports carry no current signs, and older
signs age with their observation time. It does not claim to execute that contract.

## Recorded CV excerpt: separate clip, never scenario input

Source: `cv/eval/signs/pexels-three-lying-overhead-20261114T065011.json`

SHA-256: `2e1bbd372bc68a3dd63d5f8718ccf41363d2e73bbabd59a8d83c27e30d8f49bd`

Only the following verified fields are transcribed into `RECORDED_CV`:

| Source JSON field | Recorded value |
| --- | --- |
| `run_at_utc` | `2026-11-14T06:50:11.433Z` |
| `source` | `pexels-three-lying-overhead.mp4` |
| Track key | `t1` |
| `tracks.t1.signs.posture.first_at_s.lying` | `0.0` seconds |
| `tracks.t1.signs.movement.first_at_s.moving` | `1.0` seconds |
| `tracks.t1.signs.movement.first_at_s.still` | `2.0` seconds |

These are **first observations**, not continuous states, calibrated confidence,
accuracy, or proof of clinical conditions. The excerpt is in a separately labeled,
collapsed disclosure. It is not assigned to C1–C4 or fed into the sandbox engine.
No video is bundled, streamed or replayed, and no model is executed.

## Neural / optimization context

Reviewed `docs/NEURAL_SHADOW_INTEGRATION.md`,
`experiments/neural_assignment/src/neural_assignment/models.py`, and
`optimization/README.md`.

- OR-Tools CP-SAT produces the authoritative approval-eligible assignment proposal.
- Operational A* uses four-neighbor grid routing and Manhattan distance.
- Pairwise MLP learns responder–casualty compatibility; bipartite GNN learns over
  the assignment graph. Constrained decoding and independent validation enforce
  feasibility rather than trusting learned weights alone.
- When enabled, neural advisory shadows can be compared against mathematical
  optimization. They do **not** authorize dispatch and are disabled by default.
- The public browser instead uses its existing independent enumeration/BFS engine.
  No neural scores, predictions, benchmark claims or recorded neural outputs were
  added. Architecture descriptions are not a claim of services running publicly.

## NYC EMS: recorded aggregate research evaluation

Sources:
- `ml/ems/checkpoint/report.json`
  SHA-256: `c52daa699adf07cc359d9e6ab0b19c0bd74961a75bfddeeea0a206cabc366650`
- `ml/ems/checkpoint/generalization_audit.json`
  SHA-256: `6e1febf3f803f8066694bb5fb065d8beed65344fcedd909d9c52791b49c5344b`
- Explanations: `ml/ems/README.md` and `ml/ems/GENERALIZATION_AUDIT.md`.

| Source field / meaning | Exact value | UI |
| --- | ---: | ---: |
| `provenance.row_count` / bounded Q1 2023 source snapshot | 377606 | 377,606 |
| `test.n` / held-out chronological partition | 67798 | 67,798 |
| `test.mae_seconds` / XGBoost | 78.38800684320292 | 78.39 seconds |
| `test_baselines.train_hour_weekday_median.mae_seconds` | 82.6293474733768 | 82.63 seconds |
| Audit `severity_8_count` | 158 | 158 rare severity-8 incidents |

Target: **incident creation to first assignment duration**, not arrival, travel,
clinical severity prediction or responder-assignment optimization. Training is
January–February 2023, validation March 1–15 and test March 16–31, after label
quality/availability exclusions. The source count is not the training count and
not the full NYC EMS archive. The selected temporal baseline is a training-only
hour/weekday median chosen on validation MAE.

The audit finds aggregate gains concentrated in severity 8; typical groups have
small gains or regressions. This caveat is **always visible**, not hidden behind
the provenance disclosure. Results are not broad clinical/operational validation.
No individual incident records or XGBoost inference are supplied to the browser.

## Hardware description

Alpha → UNO R4 WiFi; Bravo → UNO Q follows the project owner's specified prototype
mapping. `hardware/README.md` documents approved assignment delivery and explicit
acknowledgment/completion button events; its enclosure covers both board types.
`hardware/VERIFICATION.md` records R4 physical checks; no additional UNO Q test or
measured hardware performance is claimed here. Public receipt/ACK/DONE remain
browser-only actions. No firmware, protocol, bridge or device settings changed.
