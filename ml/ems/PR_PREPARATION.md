# Research-only PR preparation

## Scope and integration

The isolated `research/ems-dispatch-ml` branch was safely updated from original
base `532adb31b82bbd6823ff6e7998ebd3a715d6e1b6` to latest fetched `origin/main`
`5364506ba6d7e46187e0fbaea6e3ebd502934de5` before the research commit. Local
research changes were restored without conflicts. Upstream voice, neural-shadow
and route-invalidation integrations are inherited unchanged. The research diff
does not modify the dashboard, optimizer, simulation store, dispatch/assessment
logic, approvals, hardware or other worktrees.

The endpoint still defaults to disabled in both `backend/app/main.py` and
`.env.example`; only explicit `AEGIS_EMS_ADVISORY_ENABLED=true` enables it.
No checkpoint or dependency is loaded by disabled EMS inference. Read-only,
no-write-method, failed-checkpoint and non-mutation tests remain in place.

## Model delivery and source-data privacy

- The original **4,938,809-byte `checkpoint/model.ubj` is included in Git**, not
  LFS and not separately provisioned. SHA-256 remains
  `078d3fb1970c0059c08677dfb67e0834f43e510fc9146da083803817ef548b15`.
- Category vocabularies, original benchmark/provenance, full per-severity
  MAE/RMSE/percentiles, and the accepted aggregate audit are included.
- `data/ems/` is covered by root `.gitignore`'s `data/` rule. Raw JSONL, row-level
  held-out CSV, dictionary/metadata snapshots, detailed outlier examples and
  original incident-level demo details remain there and are not staged.
- Public audit JSON contains aggregate statistics, not individual outlier rows.
  The private row examples are written to ignored `audit_examples.json`.
- The public demo uses an explicitly illustrative creation-time query, not a
  published real incident or an observed outcome. Its **62.5109748840332-second**
  result is recomputed from the original actual trained weights. This is not
  synthetic training or a substituted benchmark: no training was run and the
  accepted held-out and subgroup metrics are unchanged.
- Original historical demo details were withheld from the public training-phase
  verification JSON. The historical verification timestamp and aggregate
  benchmark evidence remain; this preparation document records later checks.

## Fresh checkout

No raw dataset, separate checkpoint provisioning, secret or training is needed:

```sh
# macOS prerequisite only
brew install libomp
uv sync --frozen --group ems
uv run --frozen --group ems python -m ml.ems.demo
uv run --frozen --group ems pytest -q \
  backend/tests/test_ems_advisory.py backend/tests/test_ems_training.py \
  backend/tests/test_ems_audit.py
```

The source-data hash and full query specification make retraining reproducible
given the same source snapshot. That snapshot is intentionally not committed;
later upstream Socrata revisions may prevent exact benchmark replay from a fresh
download. Demo inference and saved evaluation reports have no such dependency.

A clean directory exported from the staged Git tree was tested with no `data/`
directory and no inherited neural `weights/` directory. The fresh-environment
demo loaded the bundled EMS checkpoint and produced the documented result;
all **23 focused EMS tests passed** (14.15 seconds, two dependency deprecation
warnings). The same focused tests in the working environment passed in 1.16
seconds. Local raw records are not a hidden dependency of this demonstration.

## Regression results after incorporating main

The inherited main test suite now requires optional neural-experiment PyTorch
and six separately provisioned neural checkpoints. These are not new EMS runtime
dependencies. For this local regression run, the existing approved checkpoints
were copied read-only from the project artifact cache into this isolated
checkout's ignored neural `weights/` directory; all six exact upstream hashes
passed its existing verification script. No neural source, checkpoint content,
or other checkout was modified. No production neural-checkpoint retraining or
checkpoint provisioning download ran; upstream regression tests may fit their
own temporary test models without modifying released weights.

The initial bare EMS-group full-suite run reported missing `torch`. With the
upstream-locked PyTorch 2.15.0 supplied using `uv run --with`, the unrestricted
native-thread run crashed with a fatal Python error (exit 139). Limiting native
thread counts for the test process resolved it without code or dependency-lock
changes:

```sh
OMP_NUM_THREADS=1 MKL_NUM_THREADS=1 OPENBLAS_NUM_THREADS=1 \
  uv run --frozen --group ems --with 'torch==2.15.0' pytest -q
```

**507 passed in 11.95 seconds.** This covers upstream neural/route regressions and
the EMS pipeline, endpoint and privacy tests. The native crash is disclosed, not
presented as a passing unrestricted run. Local test-only PyTorch installation
does not add it to the default or EMS dependency group.

The frozen-weight audit was recomputed after privacy minimization. All accepted
full-population and per-severity metrics, severity-8-excluded results and the
**observed-duration ≤1-hour regression** remain unchanged: MAE **64.935 vs 63.923**,
RMSE **262.755 vs 243.251 seconds**. This is an outcome-conditioned diagnostic,
not an inference-time filter. The accepted audit and all its limitations are
preserved in `GENERALIZATION_AUDIT.md` and `checkpoint/generalization_audit.json`.

The model remains suitable only for an optional historical research software
demo, not operational EMS forecasting or indoor response. No merge is authorized.
