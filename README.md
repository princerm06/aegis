# AEGIS

HackPrinceton 2026 prototype for human-supervised emergency response coordination.
The backend MVP runs a complete synthetic coordination loop, **not a clinical or
production emergency-response system**. All included people and coordinates are synthetic.

## Repository layout and ownership

| Directory | Responsibility | Owner |
| --- | --- | --- |
| `backend/` | FastAPI incident state, simulation approvals, events and explanations | Prince |
| `optimization/` | Pure A* routing, OR-Tools allocation, deterministic fallback | Prince |
| `cv/` | Visual observations and spatial mapping; no medical severity inference | AskAr |
| `hardware/` | Device integration and responder telemetry | Muhammad |
| `frontend/` | Operator UI and explicit human dispatch approval | Hillary |
| `shared/` | Versioned integration contract and synthetic scenario fixtures | All four |
| `docs/` | Local setup and architecture decisions | All four |

`shared/CONTRACT.md` is the integration source of truth. Propose contract changes
in a PR and have affected module owners review them before implementation.
The module READMEs identify boundaries. The backend/optimization MVP adds a
separate **SIMULATION ONLY** coordinator; it does not implement teammates' CV,
hardware, or frontend applications or any real dispatch service.

## Quick start

Install Python 3.11+ and `uv`, then run from the repository root:

```sh
uv sync --locked
uv run uvicorn backend.app.main:app --reload --host 127.0.0.1 --port 8000
```

In another terminal:

```sh
curl http://127.0.0.1:8000/health
curl http://127.0.0.1:8000/api/v1/scenarios/demo
uv run pytest
uv run python -m scripts.demo
uv run python -m scripts.live_demo
uv run python -m scripts.benchmark --output reports/benchmark.json
```

Interactive API docs: `http://127.0.0.1:8000/docs`.
See `docs/LOCAL_SETUP.md` for a standard-library venv alternative.
See `docs/INTEGRATION.md` for actual simulation routes and event payloads,
`docs/BENCHMARKS.md` for evaluation definitions, `docs/ADVISORY.md` for grounded
explanations, and `docs/RL_RESEARCH.md` for isolated research boundaries.

## Intelligence enhancements

For the optional, disabled-by-default model genuinely trained on historical NYC
EMS dispatch records, see [the EMS research pipeline and benchmark](ml/ems/README.md).
Its read-only simulation advisory is separate from indoor routing and dispatch authority.

The demo's default allocation and approval workflow remains unchanged. Temporal
uncertainty reports distinguish camera detections, zone estimates, human-known
locations, and unknown positions without inventing medical assessments. Optional
stricter gates exclude unreliable new work, never cancel approved tasks, and
require new approval after an impactful policy or freshness change.

The solver supports distance-first, stability-first, and travel-budgeted stability
policies. Feasibility and protected human priority always dominate these soft
preferences. Counterfactuals rerun the real solver against copied inputs; they
cannot approve or dispatch assignments. See `docs/UNCERTAINTY.md` and
`docs/OPTIMIZATION_DEPTH.md` for model semantics, tradeoffs and fallback limits.

Reproduce deterministic evaluations from the repository root:

```sh
uv run python -m scripts.evaluate_uncertainty --check
uv run python -m scripts.compare_scenarios --output /tmp/aegis-comparison.json
```

Confidence decay is an explicit heuristic, not a calibrated probability or a
medical score. Synthetic evaluation reports are not evidence of clinical benefit.
Any authorized PPO experiment lives on a separate experimental branch with its
own dependencies; the backend never imports or requires a trained policy.
See `docs/INTELLIGENCE_API.md` for dashboard payloads and
`docs/DEPTH_VALIDATION.md` for measured evidence and interpretation limits.

## Demo-first integration

`uv run python -m scripts.demo` proves the real HTTP/solver workflow without
hardware: three unassessed casualties → human-entered labels → OR-Tools/A* →
explicit approval → simulated dispatch/acknowledgment → scene disruption →
new recommendation requiring new approval. It uses an isolated TestClient app,
not the separately running dashboard server. `docs/INTEGRATION.md` supplies
CV, dashboard, and device payloads for teammates to wire the same loop live.
The two-actor/two-camera rehearsal and live handoff are documented in
`docs/LIVE_DEMO.md`. Camera inputs require external calibration or explicitly
registered zones; no cross-camera identity or clinical state is inferred.
Actual Arduino alerts/button input and the dashboard remain teammate integration
work; this backend cannot send physical commands.

## Safety boundaries

- CV reports observations and coordinates, never medical severity or triage.
- Medical assessments, when added, require an identified human assessor.
- Future recommendations are advisory. Dispatch requires explicit, authenticated
  human approval of the exact proposal revision; it must never execute implicitly.
- Reserved production interfaces in `shared/CONTRACT.md` remain **unimplemented**.
  Mutable actions and events live under `/api/v1/simulations/` and cannot send
  hardware commands. A simulated approval is never real dispatch authorization.
- Bind only to loopback: the prototype has no authentication or durable storage.
  Use one worker; in-memory scenes are not shared across processes or restarts.
- Routing uses known occupancy and conservative obstacle blocking; no map means
  no new route. This is point-agent planning, not certified responder clearance.
- Allocation priorities and their limits are explicit in `optimization/README.md`;
  no LLM, learned policy, or detector confidence supplies medical labels.

## Development workflow

Use feature branches and review PRs before merging. Keep changes inside your
module when possible; coordinate shared-contract changes with affected owners.
Run `uv run pytest` before review. Commit `uv.lock` with dependency changes.
Do not commit credentials, real casualty data, or captured imagery.
