# Historical NYC EMS dispatch-duration research

The focused frozen-model [generalization audit](GENERALIZATION_AUDIT.md) reports
severity-8-excluded results, outlier influence, feature provenance and demo limits.

This is **genuine XGBoost training on official historical records**, not synthetic
training examples. It is an optional, read-only simulation advisory, disabled by
default. It is not operationally or clinically validated and does not generalize
to indoor emergency response. No frontend files are changed.

## Source, scope and verification

- Official FDNY NYC Open Data dataset: https://data.cityofnewyork.us/Public-Safety/EMS-Incident-Dispatch-Data/76xm-jjuj
- Socrata endpoint: https://data.cityofnewyork.us/resource/76xm-jjuj.json
- The official metadata attachment `EMS_incident_dispatch_data_description.xlsx`
  defines `DISPATCH_RESPONSE_SECONDS_QY` as elapsed seconds between
  `incident_datetime` and `first_assignment_datetime`. This is **not** the distinct
  `INCIDENT_RESPONSE_SECONDS_QY` creation-to-on-scene measure, outdoor travel,
  A* route distance, or indoor movement time.
- Fixed window: January 1, 2023 inclusive through April 1, 2023 exclusive,
  NYC local wall-clock timestamps. All incidents in this bounded quarter, not
  a random sample of the full dataset. Hard cap: 400,000 records. Observed count:
  **377,606**, fetched in eight ordered API pages, never the full dataset.
- Retrieved November 14, 2026. Metadata, the original dictionary, all selected raw
  rows, and manifest are retained locally under ignored `data/ems/q1-2023/`.
  The report embeds provenance, exact queries, retrieval time, source revision,
  row counts and SHA-256 hashes. Before/after revision and count checks reject a
  source changing during pagination. Incident IDs must be unique.
- Sample SHA-256:
  `34e609b1a0850c5c17854ebdc7377adb7e8bda2ed938600761ba444ddcbc22f4`.
  Historical Socrata data can be revised: exact reproduction requires preserving
  this snapshot. A later fresh download may legitimately have a different hash.
  No public archival copy or immutable upstream snapshot is claimed.

## Reproduce

### Fresh checkout: checkpoint is included

`checkpoint/model.ubj` is included as a normal Git file (4,938,809 bytes, about
4.7 MiB), along with category vocabularies, benchmark/provenance, full per-severity
metrics and aggregate audit results. No Git LFS, external model download, secret,
raw incident sample or retraining is required for the demonstration. Model SHA-256:
`078d3fb1970c0059c08677dfb67e0834f43e510fc9146da083803817ef548b15`.

After cloning/checking out this branch, run `uv sync --frozen --group ems`, then
`uv run --frozen --group ems python -m ml.ems.demo` (on macOS first install `libomp`
as shown below). This loads genuine historical-data-trained weights and predicts
**62.5109748840332 seconds** for a fixed, explicitly illustrative input. The input
is not published as an actual incident and has no observed outcome attached.
Training and held-out evaluation remain genuine historical data, not illustrative
examples. The illustrative query protects row-level incident details in Git.

### Rebuild training or audit from source

From the repository root, using the Python version in `.python-version`:

```sh
# macOS only: XGBoost's wheel requires the OpenMP runtime
brew install libomp
uv sync --frozen --group ems

# Fetch into a NEW directory; existing snapshots are never overwritten
uv run --frozen --group ems python -m ml.ems.fetch --output data/ems/q1-2023

# Choose a NEW output directory; the supplied checkpoint is never overwritten
uv run --frozen --group ems python -m ml.ems.train \
  --data data/ems/q1-2023 --output data/ems/retrained-checkpoint

# Load the supplied genuine weights; no raw data is needed for this command
uv run --frozen --group ems python -m ml.ems.demo
uv run --frozen --group ems pytest -q
```

The local verified snapshot already exists in this worktree; skip fetching when
replaying it. Default installs do not install XGBoost or scikit-learn. The `ems`
dependency group is version-pinned; `uv.lock` pins transitive dependencies and
package hashes. Existing default runtime dependency versions are preserved.
Python dependencies use the OS trust store for HTTPS; certificate checking is
never disabled. Linux wheels require a compatible OpenMP runtime (`libgomp`).

Raw records, row-level held-out predictions, audit examples, the dictionary and
original demo details stay under Git-ignored `data/ems/`, never in this PR.
The audit needs the original hashed snapshot; a fresh download can be revised
upstream and is not guaranteed to reproduce the exact accepted benchmark. The
pipeline is reproducible, but no immutable public archive of this sample is
claimed. Saved aggregate metrics and inference remain available without it.

## Features, missingness and leakage controls

The model receives only `initial_call_type`, `initial_severity_level_code`,
`borough`, and creation-time hour, weekday (Monday=0), and month. Call type and
initial severity are explicitly creation-time fields in the official dictionary.
Borough is incident location, assumed available at creation; the source cannot
prove its field-level edit history. Dispatch area is conservatively excluded.
Categories are fitted **only on training rows** and supplied as categorical
features to XGBoost, not numerically ordered clinical priorities. Missing inputs
use an explicit sentinel; unseen categories follow the tree's missing branch.
The report counts missing raw fields and unseen features per partition.
There are no missing selected raw values in this particular snapshot.

No final call/severity, assignment/activation/on-scene timestamps, travel times,
held/disposition/outcome flags or target durations enter the feature matrix.
`valid_dispatch_rspns_time_indc` is used only for label-quality exclusion, never
as an inference feature. Keep only `Y` and finite, nonnegative durations;
**1,764 invalid-flag rows** are excluded. Zero values and long tails remain:
no winsorization, outlier target clipping, or target-based balancing.

After quality filtering, 375,842 records remain. A strict label-availability
embargo also removes rows whose creation time plus dispatch duration reaches
or crosses their partition end: **2 train, 3 validation, 4 test**. These labels
would not have been available as-of that boundary. Final evaluation population:
375,833 records. Boundary exclusions, including their small censoring bias, are
reported rather than shifting incidents into later partitions.

- Train: January 1–February 28, **245,744** incidents.
- Validation: March 1–15, **62,291** incidents.
- Test: March 16–31, **67,798** incidents.

All partitions are chronological; no incident crosses partitions. Three fixed
candidates (depth/trees 3/180, 5/240, 7/300) compete on validation MAE only.
Learning rate 0.06, four CPU threads, seed 2023, histogram trees, absolute-error
loss; no refit on validation or test. Selected model: depth 7, 300 trees.
Negative outputs are floored at zero consistently in evaluation and inference.
No hyperparameter search or feature changes were made in response to test error.

Three training-only baselines are evaluated: global median (21 seconds),
hour/weekday median, and initial-severity/hour/weekday median. Group medians
require at least 30 training examples, otherwise falling back to the global
median. Lowest validation MAE selects **hour/weekday median** before test scoring.
The optional advisory gate requires strictly better test MAE **and** RMSE than
that selected temporal baseline. All baseline scores remain in `checkpoint/report.json`.

## Held-out results

All durations/errors are seconds. Errors are against the untouched chronological
holdout, not training fit or simulated data.

| Predictor | MAE | RMSE | Absolute-error p50 | p90 | p95 | p99 |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| Training global median | 82.820 | 549.259 | 12.000 | 101.000 | 267.000 | 1466.030 |
| Selected temporal median | 82.629 | 548.903 | 13.000 | 100.000 | 265.000 | 1462.000 |
| Trained XGBoost | **78.388** | **410.282** | 12.846 | 97.176 | 260.913 | 1466.564 |

Aggregate improvements are about 5.13% MAE and 25.25% RMSE. These are descriptive
point estimates, not statistical confidence or an operational acceptance test.
Model mean signed error is **−63.935 seconds**: substantial underprediction of
the long tail. Its p75 and p99 errors are worse than the temporal median.

**Important regression:** on the post-hoc slice with observed duration **≤1 hour**
(67,631 incidents), XGBoost is worse: MAE **64.935 vs 63.923 seconds**, RMSE
**262.755 vs 243.251 seconds**. This outcome-conditioned slice is a diagnostic,
not a runtime selection rule. Excluding severity 8, improvements shrink to
0.328% MAE and 0.412% RMSE; see the generalization audit for the full analysis.

| Initial severity | Test n | Model MAE | Baseline MAE | Model RMSE | Baseline RMSE |
| --- | ---: | ---: | ---: | ---: | ---: |
| 1 | 958 | 13.573 | 13.442 | 22.178 | 21.634 |
| 2 | 13,818 | 19.913 | 19.922 | 45.596 | 39.271 |
| 3 | 9,589 | 22.216 | 22.320 | 46.459 | 45.474 |
| 4 | 14,328 | 63.889 | 64.023 | 236.271 | 236.994 |
| 5 | 11,953 | 92.318 | 92.541 | 388.501 | 389.190 |
| 6 | 8,757 | 144.974 | 145.057 | 541.143 | 544.787 |
| 7 | 8,236 | 146.261 | 147.561 | 551.231 | 554.185 |
| 8 | 158 | 2022.762 | 3738.766 | 4833.445 | 8944.920 |
| 9 | 1 | 741.514 | 146.000 | 741.514 | 146.000 |

**Most aggregate gain comes from the 158 severity-8 incidents**; typical
severity groups show small gains or regressions. Severity 1 MAE/RMSE worsen;
severity 2–3 RMSE worsens; severity 9 has only one case. The gain is not evidence
of broad clinical value. All March month values are unseen during training and
use the missing branch, as do 36 test call types and one test severity value.
Month is not evidence that seasonality was learned. Per-severity full error
percentiles, raw quality counts and every validation score are in the report.

## Optional read-only endpoint and demo

```sh
AEGIS_EMS_ADVISORY_ENABLED=true uv run --frozen --group ems \
  uvicorn backend.app.main:app --host 127.0.0.1 --port 8000
curl -G http://127.0.0.1:8000/api/v1/simulations/advisories/ems-dispatch \
  --data-urlencode 'incident_datetime=2023-03-20T12:00:00' \
  --data-urlencode 'initial_call_type=RESPIR' \
  --data-urlencode 'initial_severity_level_code=4' \
  --data-urlencode 'borough=BRONX'
```

The actual trained checkpoint predicts **62.5109748840332 seconds** for that
illustrative query, which is not represented as an actual source incident. The
CLI demo loads `model.ubj`, verifies hashes, computes this prediction again and
checks it against the saved frozen-model example. It cannot fall back to a
fabricated response. Incident-level observed targets remain only in ignored
local data; public reports contain aggregate historical evaluation metrics.

GET without query inputs reports availability/provenance, without a prediction.
`AEGIS_EMS_ADVISORY_ENABLED` defaults to false; only `true` enables it.
`AEGIS_EMS_MODEL_DIR` optionally chooses a trusted local checkpoint directory.
Disabled mode does not import the optional ML stack or load any checkpoint.
Missing dependencies, corrupt/absent weights, or a failed benchmark gate produce
HTTP 503 with `status=unavailable`, `prediction_seconds=null` and an error code;
disabled mode returns HTTP 200 with `status=disabled`. Malformed inputs get 422.
There are no POST/PUT/DELETE inference or control endpoints. The response
includes model identity, provenance, benchmark, limitations, unknown/missing
input warnings, and an explicit distinction from A* distance and indoor travel.
Naive input times are NYC wall time; aware inputs convert to America/New_York.
Out-of-window inputs receive a prominent unvalidated-generalization warning.

No inference reads or writes the simulation store, events, OR-Tools objectives,
responder assignments, clinical priorities, approvals, dispatch state or hardware.
There is no mapping from clinical acuity to NYC call codes and no automatic
feedback into any decision. No UI panel is added, avoiding ongoing frontend work.

## Checks, timings, files and limitations

Focused tests cover feature leakage, missing/unknown inputs, temporal and label
availability boundaries, duplicate IDs, checksum mismatch, bounded fetches,
training-only baselines, default-disabled imports, checkpoint gate/integrity,
actual-weight demo inference, public-artifact privacy, invalid input, no write methods and unchanged
simulation state/events using a fixed clock. Original training-phase results and
timings are recorded in `checkpoint/verification.json`; post-main-integration
checks and upstream neural-suite prerequisites are recorded in `PR_PREPARATION.md`.

Files added: `ml/ems/{fetch,features,train,demo,audit}.py`, research documentation,
package markers, category/provenance/demo JSON, aggregate audit results and the
approximately 4.7 MiB trained UBJ checkpoint; `backend/app/{api,services}/ems_dispatch.py`;
and three focused backend test modules. Existing files changed:
`backend/app/main.py` (router and optional service only), `.env.example`,
`pyproject.toml`, `uv.lock`, and README links. Raw data and row-level predictions
and audit examples are ignored, not proposed for Git.

Limits: one city/quarter, no external or prospective validation, missing-outcome
selection bias, source revisions, no resource supply/queue/workload/weather
features, categorical drift and small subgroups. Absolute-error percentiles are
retrospective errors, **not prediction intervals**. The loss is median-oriented,
not a guarantee of an expected mean duration. Public caller-derived call types
are not diagnoses. Research-only use; **no claims about indoor EMS performance**.

Work is isolated on `research/ems-dispatch-ml`, based on origin/main
`532adb31b82bbd6823ff6e7998ebd3a715d6e1b6` at task start, safely updated to
`5364506ba6d7e46187e0fbaea6e3ebd502934de5` for PR preparation. This research change
does not alter upstream voice/neural/two-responder/hardware/CV work, other
worktrees, the dashboard or optimizer. The PR is not authorization to merge.
