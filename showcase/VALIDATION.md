# Public showcase validation and isolation evidence

Validated locally on November 14, 2026, in the isolated
`feat/public-aegis-showcase` worktree. No Vercel deployment was performed.

## Showcase validation

Node **22.23.5**, npm clean install from showcase's own lockfile:

| Check | Result |
| --- | --- |
| `npm ci` | Pass |
| `npm test` | **19 passed, 0 failed** |
| `npm run lint` | Pass; no diagnostics |
| `npm run typecheck` | Pass; no diagnostics |
| `npm run build` | Pass; static `/`, `/demo`, generated 404 |
| Mandatory output/source audit | **36 allowlisted static artifacts**, no operational source dependencies/configuration markers |
| `npm audit` | **0 vulnerabilities** at validation time |
| `npm run test:routes` | **4 passed, 0 failed**, isolated ephemeral static server |
| `npm run test:browser` | **6 successful coverage groups**, zero captured hydration/runtime errors or operational/external requests |

Unit tests cover independent nested initial/reset state, confirmation and priority
gates, priority-before-distance assignment, distinct tasks, availability, busy
reservations, unreachable targets, ordered receipt/ACK/DONE, blocked-route holding,
replacement approval, completed history, invalid transitions, and stale approval
invalidation including task progression.

Browser coverage:

- Desktop **1440×900**: initial-viewport hero/nav CTA, preview, fonts/CSS,
  persistent navigation, theme persistence, physical/keyboard workflow tabs, scene inspector.
- Mobile **390×844**, **360×800**: visible simulation navigation outside hidden
  menu, scrolling, anchors, accordion, responsive layout and no horizontal overflow.
- Every public simulation CTA targets `/demo`; desktop hero CTA clicked to enter
  the simulator directly. No account, credentials, installation or backend setup.
- Confirmation, human priority, two distinct Alpha/Bravo assignments, separate
  approval/dispatch, priority-change invalidation, corridor blockage, replan/new
  authorization, virtual receipt/ACK/DONE, preserved history, reset.
- Active route overlays remain visible when an empty recommendation is generated
  while both responders are busy.
- Mobile scenario interaction and dispatch run with the browser **offline** after
  loading; independent browser contexts share no casualty/task state.
- Normal/reduced-motion checks; no hydration/runtime errors; no external/API
  requests, WebSockets or EventSource attempts, including a **16-second** idle
  interval beyond the removed operational poll interval.
- Direct browser navigation to operational and unknown URLs returns real 404s.

Route tests include nested/encoded operational paths, missing APIs, nonfunctional
mutation requests, denied camera/mic/device permissions, and rejected WebSocket
upgrades. They test the repository's static server, **not a deployed Vercel edge**.
Vercel clean-URL/header behavior must be rechecked on an explicitly approved preview.

Native collaborative-browser captures show the initial landing viewport and direct
simulator entry on desktop and mobile. Screenshots are attached to the review
handoff, not copied into the shipped static artifact.

## Landing preservation

The operational landing remains unchanged. Its presentational components, CSS,
Framer Motion effects, Geist typography, light/dark theme, map scene, workflow,
hardware/technology descriptions, and disclaimers are independently copied.
Intentional public-only differences: prominent simulation CTAs, sandbox preview,
static simulation notice instead of backend polling, independent theme storage,
scoped CSS scanning, SSR-stable reduced-motion behavior, and physical workflow
tab hit-testing. No claim of pixel-identical screenshots is made.

## Security boundary

- No source imports outside showcase; no shared package/workspace/root config.
- Only public-safe presentational components and synthetic grid fixtures included.
- No operational environment variables, checkpoints, credentials, private keys,
  model predictions or operational static data intentionally included.
- Build audit rejects common credential/config markers, operational paths,
  application networking/device APIs, unapproved imports, symlinks and unexpected
  output files. This is defense in depth, not a universal secret-detection proof.
- Browser source makes no network/device calls. Next navigation can fetch static
  same-origin payloads. CSP allows only same-origin connections; permissions deny
  camera, microphone, geolocation, USB, serial and Bluetooth.
- No operational dashboard, server runtime, proxy, endpoint, function or socket
  is exported. Unknown routes do not fall back to the application.
- No Vercel project/deployment created. Showcase Git auto-deployment is disabled
  in `vercel.json`; unrelated external integrations are outside this config's control.

## Git scope and operational evidence

Original base was `028c0981a80ec7f96fdd65df945069f465a7409e`. A final fetch found
upstream CV-only commit `d922b09e45a2114651a4acf6c30df468d465d264`; this unpushed
feature branch was fast-forwarded before its showcase commit. No upstream code
was authored, edited or reverted by this work. No shared branch or other worktree
was modified. Frontend/backend code and tests were identical across those bases.

Review commands:

```sh
git diff --name-status origin/main...HEAD
git diff --exit-code origin/main...HEAD -- . ':!showcase'
git diff --exit-code -- . ':!showcase'
git diff --cached --exit-code -- . ':!showcase'
git ls-files --others --exclude-standard -- . ':!showcase'
```

The commit inventory must contain only `showcase/` additions; `FILES.md` enumerates
them. Generated dependency/build caches are ignored locally, not committed.

Existing AEGIS tests ran unmodified against isolated ports, not the live team
backend/hardware. Frontend build/lint/typecheck and **10 tests** passed; backend
default suite **475 passed, 23 skipped**; integration smoke/dashboard/combined
checks passed. Voice-dependent suites and the additionally attempted CV suites
(missing optional `cv2`) are environment-blocked; optional model dependencies
were not installed. See `REGRESSION_RESULTS.md` for precise
commands, errors, skips, baseline vulnerabilities and the post-fetch rerun.

**Operational safety answer:** no operational source/configuration was changed
by the showcase commit, and no interference or regression was observed in the
executed checks. This does not certify unavailable voice/model/hardware tests or
all possible runtime behavior. The live team system was not used for validation.
