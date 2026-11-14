# AEGIS Neural Assignment Evaluation Summary (Synthetic Simulation Only)

- **Generator version**: `aegis-neural-synth-v1.0`
- **Feature schema**: `aegis-pair-features-v1.0`
- **Train / Val / Test-ID / Test-OOD scenarios**: `5000` / `600` / `600` / `600`

## Combined Held-Out Test Suite (1,200 Scenarios: 600 ID + 600 OOD)

| Contender | Mean Assigned | Mean Priority Pts | Mean Travel (m) | Zero-Regret Optimal Rate (95% CI) | Exact Pair Match | Tier-2 Prio Shortfall | Tier-3 Extra Travel (m) | Raw Invalid (Pre-Filter) | Committed Violations | End-to-End (ms) |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| `ortools` | 1.661 | 2.711 | 14.758 | 100.00% (±0.00%) | 100.00% | 0.0000 | 0.0000 | 0.000/scen | 0 | 1.237 |
| `nearest` | 1.597 | 2.393 | 12.540 | 73.92% (±2.49%) | 70.83% | 0.3183 | 0.2260 | 0.000/scen | 0 | 0.536 |
| `mlp_greedy` | 1.646 | 2.670 | 14.596 | 89.31% (±1.01%) | 85.08% | 0.0406 | 0.3546 | 0.000/scen | 0 | 1.119 |
| `mlp_matching` | 1.661 | 2.687 | 14.755 | 93.42% (±0.81%) | 88.92% | 0.0239 | 0.2075 | 0.000/scen | 0 | 1.138 |
| `gnn_greedy` | 1.659 | 2.701 | 14.752 | 96.61% (±0.59%) | 92.50% | 0.0097 | 0.1036 | 0.000/scen | 0 | 2.064 |
| `gnn_matching` | 1.661 | 2.702 | 14.773 | 97.08% (±0.55%) | 92.86% | 0.0092 | 0.0839 | 0.000/scen | 0 | 2.076 |

## In-Distribution Geometries Held-Out Suite (600 Scenarios, Seeds 30000-30599)

| Contender | Mean Assigned | Mean Priority Pts | Mean Travel (m) | Zero-Regret Optimal Rate (95% CI) | Exact Pair Match | Tier-2 Prio Shortfall | Tier-3 Extra Travel (m) | Raw Invalid (Pre-Filter) | Committed Violations | End-to-End (ms) |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| `ortools` | 1.660 | 2.720 | 12.155 | 100.00% (±0.00%) | 100.00% | 0.0000 | 0.0000 | 0.000/scen | 0 | 1.119 |
| `nearest` | 1.600 | 2.435 | 10.503 | 74.67% (±3.48%) | 71.33% | 0.2850 | 0.2222 | 0.000/scen | 0 | 0.409 |
| `mlp_greedy` | 1.643 | 2.679 | 12.066 | 89.17% (±1.44%) | 84.67% | 0.0411 | 0.3379 | 0.000/scen | 0 | 0.915 |
| `mlp_matching` | 1.660 | 2.701 | 12.231 | 94.06% (±1.09%) | 89.28% | 0.0189 | 0.1937 | 0.000/scen | 0 | 0.933 |
| `gnn_greedy` | 1.659 | 2.713 | 12.186 | 97.39% (±0.74%) | 93.00% | 0.0072 | 0.0709 | 0.000/scen | 0 | 1.857 |
| `gnn_matching` | 1.660 | 2.712 | 12.199 | 97.67% (±0.70%) | 93.33% | 0.0078 | 0.0541 | 0.000/scen | 0 | 1.874 |

## Out-of-Distribution Unseen Map Geometries Suite (600 Scenarios, Seeds 40000-40599)

| Contender | Mean Assigned | Mean Priority Pts | Mean Travel (m) | Zero-Regret Optimal Rate (95% CI) | Exact Pair Match | Tier-2 Prio Shortfall | Tier-3 Extra Travel (m) | Raw Invalid (Pre-Filter) | Committed Violations | End-to-End (ms) |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| `ortools` | 1.662 | 2.702 | 17.362 | 100.00% (±0.00%) | 100.00% | 0.0000 | 0.0000 | 0.000/scen | 0 | 1.356 |
| `nearest` | 1.593 | 2.350 | 14.577 | 73.17% (±3.55%) | 70.33% | 0.3517 | 0.2299 | 0.000/scen | 0 | 0.662 |
| `mlp_greedy` | 1.649 | 2.662 | 17.126 | 89.44% (±1.42%) | 85.50% | 0.0400 | 0.3714 | 0.000/scen | 0 | 1.323 |
| `mlp_matching` | 1.662 | 2.673 | 17.279 | 92.78% (±1.20%) | 88.56% | 0.0289 | 0.2215 | 0.000/scen | 0 | 1.343 |
| `gnn_greedy` | 1.660 | 2.689 | 17.319 | 95.83% (±0.92%) | 92.00% | 0.0122 | 0.1366 | 0.001/scen | 0 | 2.271 |
| `gnn_matching` | 1.662 | 2.691 | 17.347 | 96.50% (±0.85%) | 92.39% | 0.0106 | 0.1139 | 0.001/scen | 0 | 2.278 |

## Separate 4-Seed Historical PPO Suite Comparison (Seeds 901-904 Only)

| Contender | Total Assigned (4 seeds) | Total Priority Points | Total Travel (m) | Committed Violations |
|---|---:|---:|---:|---:|
| `ortools` | 8 | 16.0 | 47.000 | 0 |
| `nearest` | 8 | 14.0 | 35.000 | 0 |
| `mlp_matching` | 8 | 16.0 | 47.000 | 0 |
| `gnn_matching` | 8 | 16.0 | 47.000 | 0 |
| `ppo_train101_historical` | 4 | 8.0 | 35.000 | 0 |
| `ppo_train102_historical` | 6 | 12.0 | 45.000 | 0 |
