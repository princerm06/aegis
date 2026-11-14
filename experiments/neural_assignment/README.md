# AEGIS — Isolated Supervised Neural Assignment Intelligence Experiment

Offline simulation and research benchmark investigating whether supervised neural scoring models (**Pairwise MLP** and **Compact Bipartite GNN**) paired with an independent hard-constraint feasibility gate and constrained bipartite matching decoder can learn responder-casualty assignment policies from proven-optimal **OR-Tools CP-SAT** teacher solutions.

> **Safety & Integration Boundary**: This package is strictly offline, synthetic, and advisory. It never modifies production backend routes, never replaces the authoritative OR-Tools CP-SAT solver, never infers medical triage severity from computer vision, and never authorizes dispatch without human approval.

---

## Directory Structure

```text
experiments/neural_assignment/
├── .gitignore
├── pyproject.toml
├── uv.lock
├── README.md
├── reports/
│   ├── TECHNICAL_REPORT.md
│   ├── eval_results.md
│   ├── eval_results.json
│   ├── training_summary.json
│   └── benchmark_panel.json
├── src/
│   └── neural_assignment/
│       ├── __init__.py
│       ├── _rootpath.py
│       ├── generator.py      # Reproducible scenario generator & OR-Tools teacher labeling
│       ├── features.py       # 48-dim pairwise + node + competition feature engineering
│       ├── models.py         # PyTorch PairwiseAssignmentMLP & BipartiteAssignmentGNN
│       ├── decoder.py        # Constrained greedy & exact max-cardinality matching decoders
│       ├── validator.py      # Independent hard-constraint safety validator
│       ├── train.py          # Tie-aware weighted BCE + ranking loss & early stopping
│       └── evaluate.py       # Deterministic evaluation harness with 95% CIs
└── tests/
    └── test_neural_assignment.py
```

---

## Reproducible Commands

### 1. Environment Setup

```bash
cd experiments/neural_assignment
uv sync
```

### 2. Run Unit & Integration Tests

```bash
cd experiments/neural_assignment
PYTHONPATH=src uv run pytest -v
```

### 3. Run Full Dataset Generation, Multi-Seed Training (MLP + GNN), and Evaluation

```bash
cd experiments/neural_assignment
PYTHONPATH=src uv run python -m neural_assignment.evaluate \
  --train-count 5000 \
  --val-count 600 \
  --test-id-count 600 \
  --test-ood-count 600 \
  --seeds 42 43 44 \
  --epochs 35 \
  --batch-size 128 \
  --weights-dir weights \
  --reports-dir reports
```

---

## Summary of Measured Results (1,200 Held-Out Scenarios, 3 Training Seeds)

- **Dataset**: `5,000` training scenarios (`seeds 10000..14999`), `600` validation scenarios (`seeds 20000..20599`, held-out `diagonal_choke` 14x14 geometry), `600` in-distribution test scenarios (`seeds 30000..30599`), and `600` out-of-distribution unseen-map test scenarios (`seeds 40000..40599`, held-out `u_shaped_courtyard` 14x14 and `four_quadrant_cross` 16x16 geometries). Every scenario has a proven-optimal OR-Tools CP-SAT teacher solution (`0` unvalidated or failed solves).
- **Combined 1,200-Scenario Held-Out Test Performance**:

| Contender | Mean Assigned | Mean Priority Pts | Mean Travel (m) | Zero-Regret Optimal Rate (95% CI) | Exact Pair Match | Tier-2 Prio Shortfall | Tier-3 Extra Travel (m) | Raw Invalid (Pre-Filter) | Committed Violations | End-to-End (ms) |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| `ortools` (Teacher) | 1.661 | 2.711 | 14.758 | **100.00%** (±0.00%) | 100.00% | 0.0000 | 0.0000 | 0.000/scen | **0** | 1.237 |
| `nearest` (Baseline) | 1.597 | 2.393 | 12.540 | **73.92%** (±2.49%) | 70.83% | 0.3183 | 0.2260 | 0.000/scen | **0** | 0.536 |
| `mlp_greedy` | 1.646 | 2.670 | 14.596 | **89.31%** (±1.01%) | 85.08% | 0.0406 | 0.3546 | 0.000/scen | **0** | 1.119 |
| `mlp_matching` | 1.661 | 2.687 | 14.755 | **93.42%** (±0.81%) | 88.92% | 0.0239 | 0.2075 | 0.000/scen | **0** | 1.138 |
| `gnn_greedy` | 1.659 | 2.701 | 14.752 | **96.61%** (±0.59%) | 92.50% | 0.0097 | 0.1036 | 0.000/scen | **0** | 2.064 |
| `gnn_matching` | 1.661 | 2.702 | 14.773 | **97.08%** (±0.55%) | 92.86% | 0.0092 | 0.0839 | 0.000/scen | **0** | 2.076 |

See [`reports/TECHNICAL_REPORT.md`](reports/TECHNICAL_REPORT.md) for the complete analysis, training configurations, generalization breakdown, and negative findings.
