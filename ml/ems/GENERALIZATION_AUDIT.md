# Frozen-model generalization audit

## Verdict

**Suitable only for an optional research/demo endpoint that runs real trained
weights on historical-style inputs. Not suitable for live operational EMS
predictions, clinical decisions, or a broad-generalization claim.** The modest
aggregate improvement is overwhelmingly concentrated in standby/special-event
incidents. The endpoint remains disabled by default. No dashboard, optimizer,
model architecture, training procedure or weights are changed by this audit.
No retraining, commits, pushes or merges were performed.

## Audit method and reproducibility

Run from the repository root with the already downloaded, ignored snapshot:

```sh
uv run --frozen --group ems python -m ml.ems.audit
uv run --frozen --group ems pytest -q
```

`audit.py` verifies source, metadata, dictionary and checkpoint hashes, rebuilds
the chronological partitions and training-only category vocabularies, loads the
frozen checkpoint, recomputes predictions and the original selected temporal
baseline, and checks agreement with both saved row predictions and reported
metrics. It never calls `fit`, selects a new model, changes thresholds used in
the original benchmark, or writes to the model checkpoint. JSON results are in
`checkpoint/generalization_audit.json`. Runtime: 1.55 seconds for the recorded
audit. Full suite at audit acceptance: **480 passed in 8.89 seconds** (22 focused
EMS tests also passed). PR-preparation regressions are recorded separately in
`PR_PREPARATION.md` after incorporating latest main.

Model SHA-256 remains
`078d3fb1970c0059c08677dfb67e0834f43e510fc9146da083803817ef548b15`.
The original `report.json`, model and category vocabularies are unchanged. For
public PR preparation, incident-level outlier examples were moved to ignored
`data/ems/q1-2023/audit_examples.json`, and the public demo now uses a fixed,
explicitly illustrative query with no observed incident outcome. This changes
neither trained weights nor any evaluation or audit metric. The baseline remains
training-only hour/weekday median selected by
validation MAE, not reselected after looking at this audit.

## 1. Held-out results, including severity-8 exclusion

All values are seconds; lower is better. Exclusion is a descriptive, post-hoc
sensitivity analysis on the same frozen model and baseline, not a replacement
benchmark or a proposal to filter future incidents by their eventual outcome.

| Test population | n | Model MAE | Baseline MAE | Model RMSE | Baseline RMSE |
| --- | ---: | ---: | ---: | ---: | ---: |
| All severities | 67,798 | 78.388 | 82.629 | 410.282 | 548.903 |
| Excluding severity 8 | 67,640 | 73.846 | 74.089 | 337.865 | 339.263 |

Without severity 8, MAE improves by **0.243 seconds (0.328%)** and RMSE by
**1.398 seconds (0.412%)**. These tiny point-estimate improvements do not
establish statistical significance, meaningful operational benefit, or future
generalization. No external or prospective test has been performed.

## 2. Per-initial-severity results

Difference means **model MAE minus baseline MAE**, in seconds: negative is better
for the model; positive is worse. Counts are from the held-out test only.

| Initial severity | n | Model MAE | Baseline MAE | Difference |
| --- | ---: | ---: | ---: | ---: |
| 1 | 958 | 13.573 | 13.442 | +0.131 |
| 2 | 13,818 | 19.913 | 19.922 | −0.010 |
| 3 | 9,589 | 22.216 | 22.320 | −0.104 |
| 4 | 14,328 | 63.889 | 64.023 | −0.134 |
| 5 | 11,953 | 92.318 | 92.541 | −0.223 |
| 6 | 8,757 | 144.974 | 145.057 | −0.083 |
| 7 | 8,236 | 146.261 | 147.561 | −1.300 |
| 8 | 158 | 2022.762 | 3738.766 | −1716.004 |
| 9 | 1 | 741.514 | 146.000 | +595.514 |

Severity 1 regresses. Severities 2–6 show negligible MAE gains; severity 7 improves
by about 1.3 seconds. Severity 9 has one unseen-category case and cannot support
a generalization conclusion. RMSE also worsens for severities 1, 2, 3 and 9;
the JSON contains all per-severity RMSE and error percentiles.

## 3. Chronology and temporal leakage

All timestamps below use the source's NYC wall-clock convention.

| Partition | n | First creation | Last creation | Latest label available |
| --- | ---: | --- | --- | --- |
| Train | 245,744 | Jan 1 00:00:02 | Feb 28 23:58:53 | Feb 28 23:59:00 |
| Validation | 62,291 | Mar 1 00:00:10 | Mar 15 23:59:02 | Mar 15 23:59:59 |
| Test | 67,798 | Mar 16 00:00:09 | Mar 31 23:58:30 | Mar 31 23:58:38 |

The year is 2023. Boundaries are March 1, March 16 and April 1 exclusive.
The audit independently checks each partition's creation-time bounds,
pairwise-disjoint incident IDs, and label availability strictly before its end.
Label availability is reconstructed as creation time plus dispatch duration.
The existing embargo excludes 2 train, 3 validation and 4 test boundary-crossing
labels; the earlier training review already corrected this, before final weights
were produced. These nine exclusions introduce a small, disclosed boundary
censoring effect; they are not assigned to another partition.

Code inspection confirms training-only model fitting, vocabulary fitting and
baseline medians. Model hyperparameters and baseline type are selected using
validation MAE only; there is no test refit. The audit checks that saved choices
match those validation-only rules. No remaining split, incident-overlap,
label-availability or preprocessing leakage was identified.

This is not an exact replay of immutable historical database versions. Source
records are retrospective and revisable; validity flags and field edits have no
version history in the snapshot. Chronological verification cannot prove every
record was already in its final published form at prediction time.

## 4. Availability of every predictive feature

Intended prediction time: **incident creation**, with only information then known.
The locally preserved official FDNY dictionary is `data/ems/q1-2023/dictionary.xlsx`;
its checksum is checked against training provenance.

| Feature | Availability evidence |
| --- | --- |
| `initial_call_type` | Official dictionary explicitly assigns it at incident creation. Caller-derived dispatch classification, not a diagnosis. |
| `initial_severity_level_code` | Official dictionary explicitly assigns it at incident creation. |
| `hour` | Derived only from creation timestamp. |
| `day_of_week` | Derived only from creation timestamp. |
| `month` | Derived only from creation timestamp. |
| `borough` | Defined as incident-location borough; assumed known at creation. **Not independently certifiable as an initial, unrevised field.** |

Thus the two initial classifications and three clock features have explicit or
direct creation-time provenance. Borough is plausible pre-outcome location
context but remains an unverified assumption, not a confirmed as-of guarantee.
There is no evidence here of an actual post-outcome borough leak, so this audit
does not justify retraining, but the assumption prevents claiming all six fields
are proven historically immutable. Any later operational study must establish
as-of location provenance or separately evaluate a model without borough.

Final severity/call type, assignment/activation timestamps, travel/arrival times,
held flags and other outcomes are not predictive inputs. The target and valid
dispatch flag are used only to validate labels and reconstruct their availability.
All March month values are unseen in training, as are 36 test call types and one
test severity; these use missing branches. This does not demonstrate learned
seasonal generalization.

## 5. Why RMSE is much larger than MAE

RMSE squares residuals before averaging, so a small number of errors of thousands
of seconds dominate it. Absolute-error loss is median-oriented, not optimized
for rare-tail RMSE. The distribution is strongly right-tailed:

- Held-out target median: **22 seconds**; p99: **1487.03 seconds**;
  p99.9: **5490.08 seconds**; maximum: **29,867 seconds (8 h 17 m 47 s)**.
- **167 incidents (0.246%)** have targets over one hour; **11** exceed six hours.
- The model's largest **678 absolute residuals (about 1%)** account for
  **87.95% of squared error**; the largest 68 account for **49.04%**.
- The model still has 153 underpredictions and 9 overpredictions exceeding one
  hour; mean signed error over all cases is **−63.94 seconds**.
- Full-population p99 absolute error is slightly worse for the model
  (1466.56 vs 1462.00 seconds); aggregate RMSE improvement does not mean a uniformly
  better tail or calibrated prediction intervals.

All ten largest target durations are severity 8, with initial call codes
`STNDBM`, `STNDBY` or `SPEVNT`. The official dictionary defines these as municipal
unit standby requests, standby requests and special events. In this test set,
these codes cover **exactly the same 158 records as severity 8**. Many extreme
requests were created shortly after midnight. This is consistent with a distinct
standby/event process, not evidence of ordinary emergency-response improvement.

The records pass the source's valid-dispatch flag and numeric validity checks.
Their durations must not automatically be called corrupt or clipped away.
However, the bounded training sample contains neither independent incident
narratives nor assignment timestamps for verifying each extreme duration;
source validity is not independently established ground truth. No outliers were
removed from the primary benchmark or the model during this audit.

As a **post-hoc sensitivity only**, among targets at most one hour (67,631 cases),
the model is worse: MAE **64.935 vs 63.923**, RMSE **262.755 vs 243.251**.
Among targets at most ten minutes (66,033 cases), MAE is **34.494 vs 33.291** and
RMSE **127.639 vs 75.939**. These are outcome-conditioned slices, not legitimate
runtime filters or substitutes for the original benchmark. They reinforce that
the headline gain is tail-concentrated, not uniformly better everyday accuracy.

## 6. Breadth and permitted demo claims

Severity 8 is **0.233%** of the test population but accounts for **94.29% of net
absolute-error reduction** and **99.29% of net squared-error reduction** versus
the original baseline. These percentages compare additive error sums, not
subtraction of group RMSEs. Improvements are strongly concentrated, not broad.

A permissible demo claim is: “Real XGBoost weights trained on bounded historical
NYC EMS records predict creation-to-first-assignment duration. On this one
chronological holdout they beat a selected temporal median baseline overall;
almost all gain is associated with a tiny standby/special-event subgroup.”

Do not claim better emergency dispatch generally, clinically meaningful benefit,
reliable handling of critical cases, future NYC accuracy, calibrated uncertainty,
arrival-time prediction or indoor-response generalization. For a demo, keep
operator opt-in, source/model identity, baseline, observed-target comparisons and
limitations visible; the existing API already provides provenance and warnings.
No dashboard change is required or made. The default remains
`AEGIS_EMS_ADVISORY_ENABLED=false`; existing default-disabled, no-write-method,
integrity/failure-state and simulation-immutability tests still pass.
