# AEGIS public showcase

Independent, static-only public product demonstration. The authoritative AEGIS
system remains in the existing frontend, backend, CV, voice, ML, and hardware
directories. This application neither imports nor contacts those components.

## Public experience

- `/`: copied AEGIS landing page, typography, cinematic scene, animations,
  interactive workflow, theme switching, architecture and safety explanations.
  The hero, fixed desktop/mobile navigation, preview, and closing links enter `/demo`.
- `/demo`: synthetic incident with four casualty observations and two virtual
  responders: Alpha (Arduino UNO R4 WiFi) and Bravo (Arduino UNO Q).
- Confirm observations, set human priorities, generate recommendations, approve,
  dispatch, simulate receipt/ACK/DONE, block crossings, authorize replacement
  routes, and reset. No account or configuration required.
- Unknown/operational routes return 404. Generated HTML/RSC payloads, fonts,
  JavaScript and CSS are the only other public resources.

The landing architecture explains the **real local project**, not services running
on this website. The public simulator explicitly says **Interactive Simulation —
No Real Emergency Dispatch**. Its fixtures are synthetic, not clinical guidance.

## Computation and isolation

`src/lib/sandbox/engine.ts` uses browser-side exhaustive assignment enumeration
with a lexicographic human-priority objective, then grid travel-distance tie
breaking. Routes use breadth-first shortest paths on an unweighted synthetic grid.
This is demonstration logic, **not live OR-Tools CP-SAT, operational A*, or neural
inference**. No purported MLP/GNN/XGBoost predictions, scores, benchmarks, or
precomputed model outputs are supplied.

Only confirmed, assessed, reachable, unfinished casualties are eligible; responders
must be available and not reserved by an active task. Assignments are distinct.
Recommendations and approvals are revision-bound; scenario changes and task
progress invalidate authorization. Blocked routes cannot resume automatically,
even after reopening. Replacements require another recommendation, approval, and
dispatch. Task history survives replanning. Reset/reload clears scenario state.

All incident state lives in one page's React reducer. Tabs/visitors share no
scenario data. Only the independently named theme preference is stored locally.
There are no backend calls, WebSockets, camera/microphone access, physical-device
APIs, service workers, model files, credentials, or environment-variable reads.
Framework same-origin requests may fetch static Next navigation payloads; they
are not operational API calls. No source maps or operational dashboard are exported.

Presentational duplication is intentional: no operational files, shared state,
dependencies, or root configuration need changing to maintain this showcase.

## Local development and validation

Use Node **22.x**, independently of the operational environment:

```sh
cd showcase
npm ci
npm run dev                  # standalone landing + sandbox on localhost:3000
npm test                     # pure engine tests, Node 22 strip-types
npm run lint
npm run typecheck
npm run build                # static export + mandatory artifact/source audit
npm audit
npm run serve                # localhost:4173; no SPA fallback
# In another terminal, from showcase/:
npm run test:routes
npm run test:browser
```

`BASE` overrides the browser test origin; route tests start their own ephemeral server.
Browser tests use locally installed Google Chrome by default; set `CHROME_PATH`
to another installed Chromium executable.
`PORT` overrides the static test server port. These are **test tooling** variables,
not public build configuration. No `.env` file is needed or included.

## Exact Vercel handoff — do not deploy without approval

No Vercel project was created and no deployment was requested by this work.
After explicit approval, create a **separate** project, never reuse operational
deployment settings:

| Setting | Exact value |
| --- | --- |
| Suggested project name | `aegis-hackprinceton-showcase` |
| Repository | `princerm06/aegis` |
| Root Directory | `showcase` |
| Framework Preset | **Other** (`framework: null`) |
| Node.js | **22.x** |
| Install Command | `npm ci` |
| Build Command | `npm run build` |
| Output Directory | `out` |
| Environment Variables | **None**, all environments |
| Include files outside Root Directory | **Off** / unnecessary |
| Functions / redirects / proxy rewrites | **None** |
| Clean URLs / trailing slash | `true` / `false`, configured locally |
| Production branch | Keep `main`; do not promote or deploy this feature branch |

`vercel.json` deliberately sets `git.deploymentEnabled: false` to prevent automatic
Git deployments for this showcase. Leave it in place pending approval. A later
authorized manual deployment can use the Vercel dashboard/CLI; do not run `vercel`,
`vercel --prod`, click Deploy, or enable Git deployments as part of review setup.
Connecting a repository through an import wizard can initiate a deployment: defer
that action until approval, rather than treating it as harmless project setup.
Do not inherit team/shared environment variables or attach operational credentials.

The repository's feature-branch PR is the review artifact, not a deployment request.
This file cannot control unrelated Vercel projects/integrations configured outside
the repository; an owner must confirm those separately before authorizing release.

### Release boundary checks (after an approved preview exists)

1. Confirm build logs start in `showcase`, use its lockfile, and run the output audit.
2. Verify `/` and `/demo` render; desktop/mobile CTAs reach `/demo` without setup.
3. Directly request `/command`, `/aegis-api`, `/aegis-voice`, `/api/health`, `/ws`,
   nested API paths, and unknown URLs: all must be unavailable/404, not SPA fallback.
4. Verify mutation requests and WebSocket upgrades cannot operate any service.
5. Inspect Network while completing a scenario: only same-origin static resources;
   no local-network addresses, API calls, sockets, camera, voice, or hardware access.
6. Check response CSP/Permissions-Policy, then test independent browser sessions,
   mobile layout, blocked-route reauthorization, reset, and keyboard navigation.
7. Inspect the deployed artifact/file inventory. Only `/`, `/demo`, generated 404,
   static navigation payloads and `_next/static` assets belong in the output.

## Limits and evidence

This is a fixed synthetic grid, not a live emergency system or geographic map.
It has no continuous vehicle movement, clinical inference, recorded model outputs,
live voice, or hardware connectivity. Reload intentionally resets the scenario.
The copied landing is preserved, not claimed pixel-identical: public CTAs/preview,
honest static status, independent theme key, reduced-motion hydration and workflow
hit-testing corrections are isolated intentional changes.

See `VALIDATION.md` for public validation and scope evidence,
`REGRESSION_RESULTS.md` for unchanged operational-suite results, and `FILES.md`
for the complete reviewed file inventory. Hosting behavior still needs an approved
Vercel preview verification; localhost tests are not a production deployment test.
