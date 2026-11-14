# AEGIS Neural Assignment Intelligence — Technical Report

- **Generator Version**: `aegis-neural-synth-v1.0`
- **Feature Schema Version**: `aegis-pair-features-v1.0`
- **Date**: `2026-11-14`
- **Scope**: Offline synthetic simulation research only (`authorizes_dispatch = false`, `clinical_inference = false`).

---

## 1. Executive Summary & Research Question

This experiment investigates whether supervised neural models (**Pairwise MLP** and **Compact Bipartite GNN**) trained on exact **OR-Tools CP-SAT** teacher solutions can learn responder-casualty assignment scoring functions that generalize to unseen map geometries when paired with a hard-constraint feasibility gate and a constrained matching decoder.

### Key Findings

1. **Supervised neural scoring + constrained matching substantially outperforms greedy heuristics**: On 1,200 held-out scenarios (`600` in-distribution + `600` out-of-distribution unseen map topologies), the greedy `nearest` baseline achieves the exact OR-Tools lexicographic optimum on only **73.92% (±2.49%)** of scenarios, losing both assignment cardinality (`1.597` vs `1.661`) and human-priority points (`2.393` vs `2.711`). **Supervised MLP + Constrained Matching (`mlp_matching`)** raises zero-regret optimality to **93.42% (±0.81%)**, and **Bipartite GNN + Constrained Matching (`gnn_matching`)** reaches **97.08% (±0.55%)**.
2. **Bipartite message passing adds measurable value over pairwise MLP**: Across 3 independent training seeds (`42, 43, 44`), the 3-layer Bipartite GNN improves validation feasible-edge F1 from **0.7916** (MLP mean) to **0.9248** (GNN mean) and cuts held-out lexicographic regret in more than half (from **6.58%** regret rate for `mlp_matching` down to **2.92%** for `gnn_matching`, and from **7.22%** down to **3.50%** on unseen OOD map geometries).
3. **Hard-constraint decoding guarantees zero safety violations**: All committed assignments pass through the independent validator (`validator.py`), enforcing one-to-one uniqueness, responder availability, capability compatibility, observation freshness/confidence/visibility, active assignment reservations, and verified A* reachability. Across all 1,200 held-out scenarios and 3 seeds, committed constraint violations are **0** for every contender.
4. **Negative Result — Neural surrogates do not improve upon exact OR-Tools CP-SAT at AEGIS scale**: A neural surrogate optimizes a learned smooth score rather than the exact 3-tier lexicographic objective (`count -> priority_points -> travel_mm`). Even `gnn_matching` exhibits non-zero regret on **2.92%** of held-out scenarios, and its end-to-end runtime (`2.076 ms` vs `1.237 ms` for OR-Tools CP-SAT) is not faster than exact CP-SAT on indoor scenes (\(C \le 6, R \le 5\)) because all-pairs A* route verification dominates preparation time for both approaches. **OR-Tools CP-SAT must remain the production/demo optimization engine.**

---

## 2. Synthetic Dataset & Leakage Prevention

All scenarios are constructed using the authoritative AEGIS schemas (`Scenario`, `Casualty`, `Responder`, `MedicalAssessment`, `CVObservation`, `CVTrack`, `SceneMap`, `OccupancyGrid`, `Obstacle`, `OptimizationRequest`) and solved by `optimization.solver.optimize(request)` with `solver="ortools"`.

| Split | Scenarios | Seed Range | Seed List SHA-256 (Prefix) | Geometry Families | Grid Size | Feasible / Partial / Infeasible | Tie Scenarios (>1 Optimal Matching) | Candidate Pairs | Feasible Pairs | Teacher Positive Pairs | Tie-Alternative Optimal Pairs | Gen Time (s) |
|---|---:|---|---|---|---:|---|---:|---:|---:|---:|---:|---:|
| `train` | 5,000 | `10000..14999` | `4f96e649b42b` | `open_scatter`, `central_wall_ns`, `twin_rooms_ew` | 12×12 | 444 / 4,160 / 396 | 683 (13.7%) | 60,093 | 25,027 | 8,488 | 1,437 | 26.11s |
| `val` | 600 | `20000..20599` | `87ff83fc6fa2` | `diagonal_choke` (held-out) | 14×14 | 71 / 482 / 47 | 82 (13.7%) | 7,277 | 3,068 | 1,028 | 168 | 1.60s |
| `test_id` | 600 | `30000..30599` | `76af1c0a43f4` | `open_scatter`, `central_wall_ns`, `twin_rooms_ew` | 12×12 | 55 / 500 / 45 | 86 (14.3%) | 7,099 | 2,868 | 996 | 194 | 1.04s |
| `test_ood` | 600 | `40000..40599` | `02854a0c2406` | `u_shaped_courtyard`, `four_quadrant_cross` (held-out) | 14×14, 16×16 | 60 / 485 / 55 | 67 (11.2%) | 7,166 | 2,922 | 997 | 135 | 1.90s |

### Leakage Controls

- **Seed & Entity Disjointness**: Verified programmatically by `verify_no_split_leakage` across all 6,800 scenarios (`0` shared seeds, `0` shared scenario IDs, `0` shared map revisions).
- **Geometry Family Holdout**: Validation (`diagonal_choke`, 14×14 with unknown `-1` corner cells) and OOD Test (`u_shaped_courtyard` 14×14 and `four_quadrant_cross` 16×16) use obstacle topologies and grid dimensions never seen during training.
- **Pre-Assignment Feature Isolation**: `extract_features_from_request` consumes only `OptimizationRequest`. Automated unit tests (`test_feature_engineering_shapes_and_no_post_assignment_leakage`) verify that mutating or deleting teacher labels has zero effect on extracted feature tensors.
- **Optimal Teacher Verification & Tie Awareness**: 100% of included scenarios are proven optimal across every CP-SAT stage (`excluded_non_optimal = 0`). Because CP-SAT breaks stage-4 ties by lexicographic `(casualty_id, responder_id)` strings, ~13.7% of scenarios possess multiple matchings with identical `(count*, points*, travel_mm*)`. Exact DFS (`_find_all_optimal_edges`) identifies all `tie_alternative_pairs` so the training loss treats equally optimal edges with soft targets (`0.75`, weight `0.35`) rather than false hard negatives.

---

## 3. Models & Training Configurations

Both models are trained with AdamW (`lr = 2.5e-3`, `weight_decay = 1e-4`, cosine annealing schedule, `batch_size = 128`, max `35` epochs, early stopping `patience = 8` on validation loss, `pos_weight = 1.5216`, intra-scenario competition margin ranking weight `0.30`).

| Model | Seed | Parameters | Epochs Ran (Best Epoch) | Runtime (s) | Epoch 1 Train / Val Loss | Best Val Loss | Val Feasible Precision | Val Feasible Recall | Val Feasible F1 | Local Checkpoint SHA-256 (Prefix) |
|---|---:|---:|---|---:|---|---:|---:|---:|---:|---|
| `PairwiseAssignmentMLP` | 42 | 48,513 | 26 (18) | 7.13s | 0.5609 / 0.5095 | 0.3609 | 0.7602 | 0.8298 | **0.7935** | `3df9bd6a5f9e` |
| `PairwiseAssignmentMLP` | 43 | 48,513 | 21 (13) | 4.03s | 0.5614 / 0.5338 | 0.3740 | 0.7336 | 0.8570 | **0.7905** | `dbfeede473d8` |
| `PairwiseAssignmentMLP` | 44 | 48,513 | 29 (21) | 5.39s | 0.5574 / 0.4830 | 0.3687 | 0.7264 | 0.8677 | **0.7908** | `1614ad8d5e37` |
| `BipartiteAssignmentGNN` | 42 | 392,359 | 25 (17) | 21.01s | 0.5243 / 0.3550 | 0.1912 | 0.9147 | 0.9387 | **0.9265** | `fb6ba734ded8` |
| `BipartiteAssignmentGNN` | 43 | 392,359 | 26 (18) | 21.57s | 0.5217 / 0.3742 | 0.1635 | 0.8982 | 0.9611 | **0.9286** | `aa0ddad994d5` |
| `BipartiteAssignmentGNN` | 44 | 392,359 | 24 (16) | 20.01s | 0.5111 / 0.3720 | 0.1930 | 0.8972 | 0.9426 | **0.9194** | `ab459dcd37ff` |

---

## 4. Comparative Held-Out Evaluation

### 4.1 Combined Held-Out Test Suite (1,200 Scenarios: 600 In-Distribution + 600 Unseen OOD Maps)

Neural results are pooled across all 3 training seeds (`42, 43, 44`), with seed-level stability reported in `reports/eval_results.json`.

| Contender | Mean Assigned | Mean Priority Pts | Mean Travel (m) | Unassigned Casualties | Unused Avail Responders | Zero-Regret Optimal Rate (95% CI) | Exact Pair Match | Tier-1 Count Shortfall | Tier-2 Prio Shortfall | Tier-3 Extra Travel (m) | Raw Invalid (Pre-Filter) | Committed Violations | Prep / Inf / Decode / Total (ms) |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---|
| `ortools` | 1.661 | 2.711 | 14.758 | 2.256 | 0.558 | **100.00%** (±0.00%) | 100.00% | 0.0000 | 0.0000 | 0.0000 | 0 | **0** | 0.012 / 0.000 / 1.225 / **1.237** |
| `nearest` | 1.597 | 2.393 | 12.540 | 2.320 | 0.623 | **73.92%** (±2.49%) | 70.83% | 0.0642 | 0.3183 | 0.2260 | 0 | **0** | 0.011 / 0.000 / 0.525 / **0.536** |
| `mlp_greedy` | 1.646 | 2.670 | 14.596 | 2.270 | 0.573 | **89.31%** (±1.01%) | 85.08% | 0.0144 | 0.0406 | 0.3546 | 0 | **0** | 0.935 / 0.145 / 0.039 / **1.119** |
| `mlp_matching` | 1.661 | 2.687 | 14.755 | 2.256 | 0.558 | **93.42%** (±0.81%) | 88.92% | 0.0000 | 0.0239 | 0.2075 | 0 | **0** | 0.946 / 0.146 / 0.046 / **1.138** |
| `gnn_greedy` | 1.659 | 2.701 | 14.752 | 2.257 | 0.560 | **96.61%** (±0.59%) | 92.50% | 0.0014 | 0.0097 | 0.1036 | 1 | **0** | 0.938 / 1.087 / 0.039 / **2.064** |
| `gnn_matching` | 1.661 | 2.702 | 14.773 | 2.256 | 0.558 | **97.08%** (±0.55%) | 92.86% | 0.0000 | 0.0092 | 0.0839 | 1 | **0** | 0.942 / 1.087 / 0.047 / **2.076** |

> *Note on `Exact Pair Match` vs `Zero-Regret Optimal Rate`*: Because ~12.8% of test scenarios have multiple distinct matchings that achieve the exact same proven-optimal `(new_assigned_count, allocation_priority_points, new_travel_cost_mm)` tuple, `gnn_matching` selects an alternative tie-optimal matching in ~4.22% of scenarios (`97.08%` zero-regret optimal vs `92.86%` exact ID-tie-broken pair match).

### 4.2 Generalization: In-Distribution (`test_id`, 600 Scenarios) vs. Unseen OOD Maps (`test_ood`, 600 Scenarios)

| Contender | `test_id` Zero-Regret Optimal Rate | `test_ood` Zero-Regret Optimal Rate | OOD Generalization Gap | `test_id` Tier-2 Prio Shortfall | `test_ood` Tier-2 Prio Shortfall | `test_id` Tier-3 Extra Travel (m) | `test_ood` Tier-3 Extra Travel (m) |
|---|---:|---:|---:|---:|---:|---:|---:|
| `ortools` | 100.00% (±0.00%) | 100.00% (±0.00%) | 0.00% | 0.0000 | 0.0000 | 0.0000 | 0.0000 |
| `nearest` | 74.67% (±3.48%) | 73.17% (±3.55%) | -1.50% | 0.2850 | 0.3517 | 0.2222 | 0.2299 |
| `mlp_greedy` | 89.17% (±1.44%) | 89.44% (±1.42%) | +0.27% | 0.0411 | 0.0400 | 0.3379 | 0.3714 |
| `mlp_matching` | 94.06% (±1.09%) | 92.78% (±1.20%) | -1.28% | 0.0189 | 0.0289 | 0.1937 | 0.2215 |
| `gnn_greedy` | 97.39% (±0.74%) | 95.83% (±0.92%) | -1.56% | 0.0072 | 0.0122 | 0.0709 | 0.1366 |
| `gnn_matching` | **97.67%** (±0.70%) | **96.50%** (±0.85%) | **-1.17%** | **0.0078** | **0.0106** | **0.0541** | **0.1139** |

Both `mlp_matching` and `gnn_matching` generalize cleanly to the unseen 14×14 `u_shaped_courtyard` and 16×16 `four_quadrant_cross` geometries (within `1.28%` and `1.17%` of their in-distribution zero-regret rates, respectively) because pairwise features normalize coordinates by map dimensions and consume verified A* distances and detour ratios rather than memorizing raw grid coordinates.

### 4.3 Separate Historical Comparison on PR #5 PPO Held-Out Suite (Seeds 901–904 Only)

To avoid comparing metrics across different test suites, OR-Tools, Nearest, `mlp_matching`, and `gnn_matching` were also evaluated on the exact 4 held-out scenarios (`seeds 901..904`, 10×10 grid with center block) from the PR #5 PPO experiment:

| Contender | Total Assigned (4 seeds) | Total Priority Points | Total Travel Distance (m) | Committed Violations | Notes |
|---|---:|---:|---:|---:|---|
| `ortools` | 8 | 16.0 | 47.000 | 0 | Exact lexicographic optimum on all 4 seeds |
| `nearest` | 8 | 14.0 | 35.000 | 0 | Misses high-priority casualty on seed 901 |
| `mlp_matching` (seed 42) | 8 | 16.0 | 47.000 | 0 | Matches exact OR-Tools optimum on all 4 seeds |
| `gnn_matching` (seed 42) | 8 | 16.0 | 47.000 | 0 | Matches exact OR-Tools optimum on all 4 seeds |
| `ppo_train101` (PR #5 historical) | 4 | 8.0 | 35.000 | 0 | Sequential RL rollout (48 filtered invalid attempts) |
| `ppo_train102` (PR #5 historical) | 6 | 12.0 | 45.000 | 0 | Sequential RL rollout (41 filtered invalid attempts) |

---

## 5. Limitations, Negative Results & Recommendation

1. **Approximate Surrogate vs. Exact Optimizer**: Even the best model (`gnn_matching`) has a **2.92%** non-zero lexicographic regret rate across the 1,200 held-out scenarios (`0.0092` mean priority-point shortfall and `0.0839 m` conditional extra travel when count and priority match). Specifically, when two feasible matchings have nearly identical A* total distances (differing by `1–2 m` across multiple responders), continuous neural logits occasionally rank the slightly longer matching higher.
2. **No Latency Advantage at Indoor Incident Scale**: For \(C \le 6, R \le 5\), exact OR-Tools CP-SAT solves the full 4-stage lexicographic problem in **1.237 ms** end-to-end (including A* routing), whereas `mlp_matching` takes **1.138 ms** and `gnn_matching` takes **2.076 ms**. Because verified A* route computation (`~0.94 ms`) is required by the hard feasibility gate regardless of the scorer, neural inference does not provide a meaningful runtime speedup at this scale.
3. **Safety Comes from the Hard Gate, Not Neural Weights**: While the trained models rarely score infeasible pairs above `0.0` (`0` to `1` raw unfiltered invalid proposals across 1,200 scenarios), adversarial logit tests (`test_constrained_decoders_filter_adversarial_logits_and_maximize_matching`) prove that safety is guaranteed **exclusively** by the explicit hard-constraint mask and independent validator, never by learned weights.
4. **Recommendation**: Retain **OR-Tools CP-SAT** as the sole production/demo optimization engine in AEGIS. Keep `experiments/neural_assignment/` offline as a reproducible research benchmark and static evaluation artifact (`reports/benchmark_panel.json`). Further neural assignment work would only be warranted if AEGIS scaled to large regional multi-incident dispatch (\(C, R \gg 100\)) where exact CP-SAT hits stage time budgets.
