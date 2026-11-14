# AEGIS — Live Neural Shadow Advisory Integration

## 1. Overview & Operational Contract

The AEGIS Neural Shadow subsystem provides live, non-dispatching advisory inference from supervised deep learning models (**PairwiseAssignmentMLP** and **BipartiteAssignmentGNN**) operating in parallel with the authoritative **OR-Tools CP-SAT** assignment solver.

```
                    Current Simulation State
                               │
                  OptimizationRequest (Authoritative)
                               │
               ┌───────────────┴───────────────┐
               ▼                               ▼
       Authoritative Solve             Shared Pre-Assignment
       (OR-Tools CP-SAT)                 Feature Extraction
               │                               │
        Authoritative                  ┌───────┴───────┐
        Proposal (ID, Rev)             ▼               ▼
               │                  Pairwise MLP    Bipartite GNN
               │                   Inference        Inference
               │                       │               │
               │                   Constrained     Constrained
               │                    Matching        Matching
               │                    Decoder         Decoder
               │                       │               │
               │                  Independent     Independent
               │                   Hard Gate       Hard Gate
               │                       │               │
               └───────────────┬───────┴───────────────┘
                               ▼
               Objective Comparison & Diagnostics
             GET /api/v1/simulations/{id}/neural-comparison
```

### Safety & Authority Boundaries
1. **Authoritative Engine**: OR-Tools CP-SAT is the sole engine that generates approval-eligible proposals.
2. **Advisory Shadow**: MLP and GNN predictions are strictly advisory shadows (`authorizes_dispatch = false`).
3. **Hard Constraint Isolation**: Feasibility is enforced by the independent validator (`neural_assignment.validator.validate_assignments`), never learned neural weights.
4. **Zero Startup Overhead**: Lazy-loaded checkpoints prevent blocking backend initialization when disabled.
5. **Feature Flag**: `AEGIS_NEURAL_SHADOW_ENABLED=false` (disabled by default).

---

## 2. Checkpoint Verification, Portability & Provisioning

Checkpoints originate from PR #6 (`experiments/neural_assignment/`) and are cryptographically verified using full 64-character SHA-256 digests:

| Model Architecture | Training Seed | Checkpoint Filename | Full SHA-256 Digest | Best Val F1 | Parameter Count |
| :--- | :---: | :--- | :--- | :---: | :---: |
| `PairwiseAssignmentMLP` | 42 | `mlp_seed42.pt` | `3df9bd6a5f9ecfba0129e3c5ce2f167dbdd5745280eb817f740f6d2f4e9ca68a` | 0.7935 | 48,513 |
| `PairwiseAssignmentMLP` | 43 | `mlp_seed43.pt` | `dbfeede473d8bdde0bcc83fd77aeea2fcf198f112365900c02108e9575e71573` | 0.7905 | 48,513 |
| `PairwiseAssignmentMLP` | 44 | `mlp_seed44.pt` | `1614ad8d5e37ec3e65d828b149b589dd0911a7b70adc00bfd45567033905c064` | 0.7908 | 48,513 |
| `BipartiteAssignmentGNN` | 42 | `gnn_seed42.pt` | `fb6ba734ded8b388b29e64feb579c692b389102c3e529bb427eb56203044c94b` | 0.9265 | 392,359 |
| `BipartiteAssignmentGNN` | 43 | `gnn_seed43.pt` | `aa0ddad994d5e3db0db76589ea89d03a2cfd8d0270c35d0bad45c956a3a5cd07` | 0.9286 | 392,359 |
| `BipartiteAssignmentGNN` | 44 | `gnn_seed44.pt` | `ab459dcd37ff4684f92705fb46a6bc0f4acf4fc69b0678a9918f24bf5ec5e196` | 0.9194 | 392,359 |

### Provisioning on a Fresh Checkout
Because trained PyTorch binary weights (`.pt`) are Git-ignored (`.gitignore:5:weights/`), a fresh repository checkout requires provisioning checkpoints before enabling neural shadow mode:
1. **Auditing Existing Checkpoints**: Run `python experiments/neural_assignment/scripts/provision_checkpoints.py` (or `--seeds 42`). The auditor strictly requires exact 64-character SHA-256 match; partial or prefix matches are rejected.
2. **Local Regeneration / Retraining**: If checkpoints need to be reproduced from scratch, run:
   ```bash
   python -m neural_assignment.train --model all --seeds 42 43 44 --output-dir experiments/neural_assignment/weights/
   ```
3. **Cross-Platform Hash Reproducibility Note**: PyTorch training sets deterministic seeds and algorithms (`torch.use_deterministic_algorithms(True)`), but floating-point arithmetic across different microarchitectures (e.g. Apple Silicon Accelerate vs x86_64 MKL/BLAS) or PyTorch minor versions can yield minor floating-point serialization variances. On the intended demo machine (macOS Apple Silicon, PyTorch 2.15), the verified seed-42 weights match the exact hashes above.
4. **Graceful Degradation When Missing**: If checkpoints are missing or corrupt, `NeuralShadowService` immediately returns `status: "checkpoints_unavailable"`, `available: false`, and clear UI warning banners without executing unverified or random weights and without blocking the authoritative OR-Tools solver.

---

## 3. Live Comparison Endpoint

### `GET /api/v1/simulations/{scenario_id}/neural-comparison`

Returns the live shadow evaluation for the current scene revision and proposal.

#### Response Schema

```json
{
  "mode": "simulation",
  "schema_version": "1.0",
  "scenario_id": "demo",
  "scene_revision": 1,
  "epoch": 1,
  "proposal_id": "sim-proposal-0001",
  "read_only": true,
  "authorizes_dispatch": false,
  "authoritative_engine": "ortools",
  "status": "available",
  "available": true,
  "context": "current",
  "stale_reasons": [],
  "evaluated_at": "2026-11-14T17:15:00.000000Z",
  "message": "Live MLP and GNN shadow predictions evaluated and validated against OR-Tools.",
  "ortools_summary": {
    "engine": "ortools",
    "authoritative": true,
    "assigned_count": 2,
    "priority_points": 4.0,
    "travel_distance_m": 27.83,
    "travel_cost_mm": 27828.0,
    "selected_pairs": [
      ["casualty-001", "responder-001"],
      ["casualty-002", "responder-002"]
    ],
    "runtime_ms": 2.31,
    "hard_constraint_validation": {
      "valid": true,
      "violation_count": 0,
      "violations": [],
      "checked_pairs": 2
    }
  },
  "mlp_prediction": {
    "model_name": "PairwiseAssignmentMLP",
    "checkpoint_file": "mlp_seed42.pt",
    "checkpoint_digest": "3df9bd6a5f9ecfba...",
    "available": true,
    "status": "success",
    "selected_pairs": [
      ["casualty-001", "responder-001"],
      ["casualty-002", "responder-002"]
    ],
    "unassigned_casualty_ids": ["casualty-003"],
    "assigned_count": 2,
    "priority_points": 4.0,
    "travel_distance_m": 27.83,
    "travel_cost_mm": 27828.0,
    "prep_duration_ms": 0.52,
    "inference_duration_ms": 0.24,
    "decoding_duration_ms": 0.08,
    "total_duration_ms": 0.84,
    "hard_constraint_validation": {
      "valid": true,
      "violation_count": 0,
      "violations": [],
      "checked_pairs": 2
    },
    "unfiltered_invalid_proposals": 0,
    "comparison_vs_ortools": {
      "count_diff": 0,
      "priority_points_diff": 0.0,
      "distance_diff_m": 0.0,
      "matches_ortools_pairs": true
    },
    "authorizes_dispatch": false
  },
  "gnn_prediction": {
    "model_name": "BipartiteAssignmentGNN",
    "checkpoint_file": "gnn_seed42.pt",
    "checkpoint_digest": "fb6ba734ded8b388...",
    "available": true,
    "status": "success",
    "selected_pairs": [
      ["casualty-001", "responder-001"],
      ["casualty-003", "responder-002"]
    ],
    "unassigned_casualty_ids": ["casualty-002"],
    "assigned_count": 2,
    "priority_points": 4.0,
    "travel_distance_m": 30.83,
    "travel_cost_mm": 30828.0,
    "prep_duration_ms": 0.52,
    "inference_duration_ms": 1.34,
    "decoding_duration_ms": 0.09,
    "total_duration_ms": 1.95,
    "hard_constraint_validation": {
      "valid": true,
      "violation_count": 0,
      "violations": [],
      "checked_pairs": 2
    },
    "unfiltered_invalid_proposals": 0,
    "comparison_vs_ortools": {
      "count_diff": 0,
      "priority_points_diff": 0.0,
      "distance_diff_m": 3.0,
      "matches_ortools_pairs": false
    },
    "authorizes_dispatch": false
  },
  "disagreement_analysis": {
    "models_agree_with_each_other": false,
    "models_agree_with_ortools": false,
    "differing_assignments": [
      {
        "casualty_id": "casualty-002",
        "ortools_responder_id": "responder-002",
        "mlp_responder_id": "responder-002",
        "gnn_responder_id": null
      },
      {
        "casualty_id": "casualty-003",
        "ortools_responder_id": null,
        "mlp_responder_id": null,
        "gnn_responder_id": "responder-002"
      }
    ],
    "summary": "Divergent recommendations across models (2 casualty differences detected). OR-Tools CP-SAT remains the authoritative proposal."
  },
  "voice_summary": "GNN suggests an alternative with the same priority points and 3.0m longer. MLP matches OR-Tools. OR-Tools remains authoritative for approval and dispatch."
}
```

---

## 4. Dashboard Specification: Neural Optimization Intelligence

This panel is designed for future inclusion in the operator frontend without altering Hillary's existing layout.

### Layout Wireframe

```
┌──────────────────────────────────────────────────────────────────────────────────┐
│ NEURAL OPTIMIZATION INTELLIGENCE (Advisory Shadow)                [Revision: 1]  │
├─────────────────────────┬──────────────────────────┬─────────────────────────────┤
│ OR-Tools — Authoritative│ MLP — Neural Advisory    │ GNN — Graph Neural Advisory │
├─────────────────────────┼──────────────────────────┼─────────────────────────────┤
│ Status: Approved/Active │ Checkpoint: 3df9bd6a     │ Checkpoint: fb6ba734        │
│ Latency: 2.3 ms         │ Latency: 0.8 ms          │ Latency: 1.9 ms             │
│ Feasibility: 100% Valid │ Feasibility: 100% Valid  │ Feasibility: 100% Valid     │
│                         │ Agreement: Matches OT    │ Agreement: Diverges (1 cas) │
├─────────────────────────┼──────────────────────────┼─────────────────────────────┤
│ Proposed Assignments:   │ Proposed Assignments:    │ Proposed Assignments:       │
│ • C-001 ➔ R-001 (13.4m) │ • C-001 ➔ R-001 (13.4m)  │ • C-001 ➔ R-001 (13.4m)     │
│ • C-002 ➔ R-002 (14.4m) │ • C-002 ➔ R-002 (14.4m)  │ • C-003 ➔ R-002 (17.4m) [!] │
│                         │                          │   (C-002 unassigned)        │
├─────────────────────────┼──────────────────────────┼─────────────────────────────┤
│ Objective Score:        │ Objective Score vs OT:   │ Objective Score vs OT:      │
│ • Assigned: 2           │ • Assigned: 2 (Δ 0)      │ • Assigned: 2 (Δ 0)         │
│ • Priority Pts: 4.0     │ • Priority Pts: 4.0 (Δ0) │ • Priority Pts: 4.0 (Δ0)    │
│ • Distance: 27.8m       │ • Distance: 27.8m (Δ 0m) │ • Distance: 30.8m (+3.0m)   │
└─────────────────────────┴──────────────────────────┴─────────────────────────────┘
```

### Visual Differentiation Rules
- Differing edges are highlighted with a distinct amber indicator `[!]` and side-by-side delta callout.
- Never state that neural predictions are superior merely because they use deep learning.
- Explicitly emphasize that only OR-Tools proposals can be approved or dispatched by the operator.

---

## 5. Voice Module Integration Contract

The active voice session (`feat/voice-command-prototype`) query handler can consume `GET /api/v1/simulations/{scenario_id}/neural-comparison` to answer operational inquiries:

### Supported Queries & Response Mappings

| Operator Voice Query | Handled By Field | Response Template / TTS Speech |
| :--- | :--- | :--- |
| *"AEGIS, what does the GNN recommend?"* | `gnn_prediction.selected_pairs` + `gnn_prediction.comparison_vs_ortools` | *"The GNN proposes assigning casualty-001 to responder-001, and casualty-003 to responder-002. This covers 4 priority points with 30.8 meters of travel."* |
| *"How does the MLP compare to OR-Tools?"* | `mlp_prediction.comparison_vs_ortools` | *"The MLP matches OR-Tools exactly, recommending casualty-001 to responder-001 and casualty-002 to responder-002."* |
| *"Do the models agree?"* | `disagreement_analysis.summary` | *"OR-Tools and MLP agree on casualty-002, while the GNN suggests casualty-003 instead. Both achieve 4 priority points, but the GNN route is 3 meters longer."* |
| *"Explain the neural prediction."* | `voice_summary` | Direct playback of `voice_summary`: *"GNN suggests an alternative with the same priority points and 3.0 meters longer. MLP matches OR-Tools. OR-Tools remains authoritative for approval and dispatch."* |

---

## 6. Startup & Execution Instructions

### Default (Shadow Disabled)
```bash
uvicorn backend.app.main:app --port 8000
```
- Neural shadow remains inactive (`AEGIS_NEURAL_SHADOW_ENABLED=false`).
- Zero model loading overhead.

### Enable Neural Shadow Advisory
```bash
AEGIS_NEURAL_SHADOW_ENABLED=true uvicorn backend.app.main:app --port 8000
```
- Automatically verifies checkpoints on first proposal.
- Executes real-time shadow inference on all subsequent proposals.
- Exposes live comparison at `/api/v1/simulations/{scenario_id}/neural-comparison`.
