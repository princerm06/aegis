#!/usr/bin/env node
// Browser + API regression test for building walls as the routing map (review fixes).
// DESTRUCTIVE: resets the simulation several times — point it at an ISOLATED backend, never the demo.
// Run: BASE=http://127.0.0.1:3300 BACKEND=http://127.0.0.1:8300 node scripts/e2e-walls.mjs
//
// Covers: no shared-state change from opening the dashboard; explicit confirm (cancel = no change);
// existing restrictions preserved; incompatible grid needs a warned opt-in; walls never opened under
// people (responder snap is an opt-in demo correction); revision-safe restore (server history,
// another operator's later edit, missing browser backup); stale-dialog guard; dispatched route
// invalidation + fresh approval still required.
import fs from "node:fs";
import puppeteer from "puppeteer-core";

const BASE = process.env.BASE ?? "http://127.0.0.1:3000";
const BACKEND = process.env.BACKEND ?? "http://127.0.0.1:8000";
const CHROME = process.env.CHROME_PATH ?? "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome";
const SHOTS = process.env.SHOTS;
const SIM = `${BACKEND}/api/v1/simulations/demo`;
const SEEDED = "sim-demo-map-1";

const ok = (m) => console.log(`  ✓ ${m}`);
const fail = (m) => {
  throw new Error(m);
};
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

async function call(method, path, body, idempotencyKey) {
  const headers = { "content-type": "application/json", ...(idempotencyKey ? { "Idempotency-Key": idempotencyKey } : {}) };
  const r = await fetch(`${SIM}${path}`, { method, headers, body: body ? JSON.stringify(body) : undefined });
  const j = await r.json().catch(() => null);
  if (!r.ok) fail(`${method} ${path} → ${r.status} ${JSON.stringify(j)}`);
  return j;
}
const state = () => call("GET", "/state");
const reset = () => call("POST", "/reset", { mode: "simulation" });
const mapEvents = async () => (await call("GET", "/events?after_sequence=0&limit=1000")).events.filter((e) => e.type === "simulation.map_updated");
const cellOf = (g, x, y) => g.cells[Math.floor((y - g.origin.y_m) / g.resolution_m) * g.width + Math.floor((x - g.origin.x_m) / g.resolution_m)];

/** Wait until the live map satisfies `pred` and the current proposal was planned on it. */
async function waitMap(pred, what, { proposal = true } = {}) {
  for (let i = 0; i < 60; i++) {
    const st = await state();
    const prop = st.proposals?.find((p) => p.id === st.current_proposal_id);
    if (pred(st.scenario.scene_map?.revision ?? "") && (!proposal || prop?.map_revision === st.scenario.scene_map.revision)) return { st, prop };
    await sleep(250);
  }
  fail(`timed out waiting for ${what}`);
}

// L1 wall grid straight from the generated data (rows from the north edge).
const outlines = fs.readFileSync(new URL("../src/lib/floorOutlines.ts", import.meta.url), "utf8");
const L1 = /L1: \{[\s\S]*?walls: \[([\s\S]*?)\]/.exec(outlines)[1].match(/"([#.]+)"/g).map((s) => s.slice(1, -1));
function interiorWallCell() {
  const H = L1.length;
  for (let i = 25; i < 55; i++) for (let c = 30; c < 60; c++) if (L1[i][c] === "#") return { x_m: (c + 0.5) * 0.25, y_m: (H - 1 - i + 0.5) * 0.25 };
  fail("no wall cell");
}

const browser = await puppeteer.launch({ executablePath: CHROME, headless: "new", args: ["--window-size=1440,900"] });
const errors = [];
try {
  const page = await browser.newPage();
  page.on("console", (m) => m.type() === "error" && !/favicon|WebSocket|ERR_CONNECTION_REFUSED|404|409/.test(m.text()) && errors.push(m.text()));
  page.on("pageerror", (e) => errors.push(String(e)));
  await page.setViewport({ width: 1440, height: 900 });
  const TOGGLE = '[data-testid="walls-toggle"]';
  const DIALOG = '[data-testid="walls-dialog"]';
  const open = async () => {
    await page.goto(`${BASE}/command`, { waitUntil: "networkidle2" });
    await page.waitForSelector(TOGGLE, { timeout: 15000 });
    await sleep(500);
  };
  const openDialog = async (mode) => {
    await page.waitForFunction((s) => !document.querySelector(s)?.disabled, { timeout: 10000 }, TOGGLE);
    await page.click(TOGGLE);
    await page.waitForFunction((d, m) => document.querySelector(d)?.dataset.mode === m, { timeout: 10000 }, DIALOG, mode);
  };
  const confirmDisabled = () => page.$eval('[data-testid="walls-confirm"]', (b) => b.disabled);
  const toastText = () => page.evaluate(() => [...document.querySelectorAll('[role="status"]')].map((t) => t.textContent).join(" | "));

  console.log("Opening the dashboard changes nothing:");
  await reset();
  await waitMap((r) => r === SEEDED, "seeded map");
  const before = (await mapEvents()).length;
  await open();
  await sleep(4000);
  let st = await state();
  if (st.scenario.scene_map.revision !== SEEDED || (await mapEvents()).length !== before) fail("dashboard load changed the shared map");
  if ((await page.$eval(TOGGLE, (b) => b.dataset.walls)) !== "off") fail("walls should start off");
  ok("map revision and map_updated events unchanged after load; toggle shows Walls off");

  console.log("Explicit activation:");
  await openDialog("on");
  const preserved = await page.$eval('[data-testid="walls-preserved"]', (e) => e.textContent);
  if (!/11 blocked/.test(preserved) || !/1 obstacle/.test(preserved)) fail(`preserve summary wrong: ${preserved}`);
  ok(`dialog states what is kept: "${preserved.trim().slice(0, 90)}…"`);
  for (const id of ["casualty-002", "casualty-003"]) if (!(await page.$(`[data-on-wall="${id}"]`))) fail(`${id} (seeded on a wall line) not listed`);
  ok("seeded C-002 and C-003 (on L1 wall lines) listed as unreachable — walls are not opened for them");
  await page.click('[data-testid="walls-cancel"]');
  await sleep(800);
  if ((await state()).scenario.scene_map.revision !== SEEDED) fail("cancel changed the map");
  ok("Cancel → nothing published");

  await openDialog("on");
  await page.click('[data-testid="walls-confirm"]');
  let { st: s1, prop } = await waitMap((r) => r.startsWith("bldg-L1-"), "walls published");
  const g = s1.scenario.scene_map.occupancy_grid;
  for (const y of [2.1, 5.5, 12.9]) if (cellOf(g, 8.5, y) === 0) fail(`seeded blocked cell (8, ${Math.floor(y)}) lost`);
  if (!s1.scenario.scene_map.obstacles.some((o) => o.id === "sim-wall-a")) fail("seeded obstacle dropped");
  ok("seeded blocked cells (x=8, y=2..12) and obstacle sim-wall-a preserved under the walls");
  for (const a of prop.assignments) {
    const w = a.route?.waypoints ?? [];
    if (w.slice(1, -1).some((q) => cellOf(g, q.x_m, q.y_m) !== 0)) fail(`${a.responder_id}→${a.casualty_id} crosses a wall`);
  }
  ok(`${prop.assignments.length} recommended route(s) avoid every wall/blocked cell; proposal is ${prop.status}`);
  if (SHOTS) await page.screenshot({ path: `${SHOTS}/walls-on.png` });

  console.log("Revision-safe restore:");
  // Another operator blocks a cell on top of the walls (Block route), straight through the API.
  const edited = structuredClone(s1.scenario.scene_map);
  edited.revision = `${edited.revision}-ui-e2e`;
  edited.occupancy_grid.cells[10 * g.width + 2] = 100;
  await call("PUT", "/map", edited);
  await waitMap((r) => r === edited.revision, "other operator's edit");
  await page.waitForFunction((s) => !document.querySelector(s)?.disabled, {}, TOGGLE);
  await openDialog("off");
  const src = await page.$eval('[data-testid="walls-restore-source"]', (e) => e.textContent);
  if (!/shared event log/.test(src) || !src.includes(SEEDED)) fail(`restore source wrong: ${src}`);
  if (!(await page.$('[data-testid="walls-later-edits"]'))) fail("later edit not reported");
  if (!(await confirmDisabled())) fail("restore allowed without accepting discard");
  ok("restore target verified from shared history; later edit reported; confirm blocked until discard is ticked");
  await page.click('[data-testid="walls-discard"]');
  await page.click('[data-testid="walls-confirm"]');
  await waitMap((r) => r === SEEDED, "restored seeded map");
  ok("restored seeded map after explicit discard");

  // Missing browser backup: activate, wipe local storage, reload, restore anyway.
  await openDialog("on");
  await page.click('[data-testid="walls-confirm"]');
  await waitMap((r) => r.startsWith("bldg-L1-"), "walls again");
  await page.evaluate(() => localStorage.clear());
  await open();
  await openDialog("off");
  if (!/shared event log/.test(await page.$eval('[data-testid="walls-restore-source"]', (e) => e.textContent))) fail("no-backup restore not from history");
  await page.click('[data-testid="walls-confirm"]');
  await waitMap((r) => r === SEEDED, "restore without backup");
  ok("no browser backup → still restored from the shared event log");

  console.log("Stale dialog guard:");
  await openDialog("on");
  const other = structuredClone((await state()).scenario.scene_map);
  other.revision = "e2e-other-operator-1";
  other.occupancy_grid.cells[3] = 100;
  await call("PUT", "/map", other);
  await page.click('[data-testid="walls-confirm"]');
  await sleep(1500);
  st = await state();
  if (st.scenario.scene_map.revision !== other.revision) fail(`stale confirm published over another operator's map (${st.scenario.scene_map.revision})`);
  if (!/changed while this was open/.test(await toastText())) fail("stale guard toast missing");
  ok("map changed while dialog open → nothing published, operator told to review again");

  console.log("Incompatible grid needs a warned opt-in:");
  const offset = structuredClone(other);
  offset.revision = "e2e-offset-grid-1";
  offset.occupancy_grid.origin = { ...offset.occupancy_grid.origin, x_m: 0.1 };
  await call("PUT", "/map", offset);
  await waitMap((r) => r === offset.revision, "offset grid");
  await openDialog("on");
  if (!(await page.$('[data-testid="walls-incompatible"]'))) fail("incompatible grid not flagged");
  if (!(await confirmDisabled())) fail("replacement allowed without opt-in");
  await page.click('[data-testid="walls-replace"]');
  if (await confirmDisabled()) fail("opt-in did not enable confirm");
  await page.click('[data-testid="walls-cancel"]');
  ok("offset grid → warning with dropped counts; publish disabled until 'Replace' is ticked");

  console.log("People on a wall line:");
  await reset();
  await waitMap((r) => r === SEEDED, "seeded after reset");
  const wall = interiorWallCell();
  await call("PATCH", "/responders/responder-002", { mode: "simulation", position: { frame_id: st.scenario.frame_id, ...wall }, reported_at: new Date().toISOString() });
  await sleep(500);
  await open();
  await openDialog("on");
  if (!(await page.$('[data-on-wall="responder-002"]'))) fail("responder on wall not listed");
  await page.click('[data-testid="walls-snap-responder-002"]');
  await page.click('[data-testid="walls-confirm"]');
  ({ st: s1 } = await waitMap((r) => r.startsWith("bldg-L1-"), "walls with snap"));
  for (let i = 0; i < 20; i++) {
    s1 = await state();
    const p = s1.scenario.responders.find((r) => r.id === "responder-002").position;
    if (p.x_m !== wall.x_m || p.y_m !== wall.y_m) break;
    await sleep(250);
  }
  const g2 = s1.scenario.scene_map.occupancy_grid;
  if (cellOf(g2, wall.x_m, wall.y_m) === 0) fail("wall was opened under the responder");
  const moved = s1.scenario.responders.find((r) => r.id === "responder-002").position;
  if (cellOf(g2, moved.x_m, moved.y_m) !== 0) fail("responder not moved to a walkable cell");
  ok(`wall cell stays blocked; Bravo moved ${Math.hypot(moved.x_m - wall.x_m, moved.y_m - wall.y_m).toFixed(2)} m to a walkable cell (opt-in demo correction)`);

  console.log("Route invalidation + approval still work:");
  await reset();
  let { st: s2, prop: p2 } = await waitMap((r) => r === SEEDED, "seeded proposal");
  const appr = await call("POST", `/proposals/${p2.id}/decision`, { mode: "simulation", proposal_revision: p2.revision, decision: "approve", actor: "e2e" }, `e2e-${p2.id}-r${p2.revision}-approve`);
  await call("POST", "/dispatches", { mode: "simulation", proposal_id: p2.id, proposal_revision: p2.revision, approval_id: appr.result.id, actor: "e2e" }, `e2e-dispatch-${appr.result.id}`);
  s2 = await state();
  if (!s2.assignments.length) fail("dispatch created no assignments");
  await open();
  await openDialog("on");
  if (!(await page.$('[data-testid="walls-invalidates"]'))) fail("dialog did not warn about invalidated routes");
  await page.click('[data-testid="walls-confirm"]');
  // Invalidated dispatched routes are never auto-replaced: no fresh proposal is required here.
  await waitMap((r) => r.startsWith("bldg-L1-"), "walls after dispatch", { proposal: false });
  s2 = await state();
  const inval = s2.assignments.filter((a) => a.route_status === "invalidated").length + (s2.route_invalidations?.length ?? 0);
  if (!inval) fail("dispatched routes crossing walls were not invalidated");
  await page.waitForFunction(() => /ROUTE INVALIDATED/.test(document.body.textContent), { timeout: 10000 });
  const dispatchesBefore = s2.dispatches.length;
  // Operator asks for a replan from the banner. Busy responders are never re-dispatched or
  // re-approved automatically: the backend records advisory alternatives that need fresh approval.
  await page.evaluate(() => [...document.querySelectorAll("button")].find((b) => /Replan now/.test(b.textContent))?.click());
  await sleep(2500);
  s2 = await state();
  const g3 = s2.scenario.scene_map.occupancy_grid;
  const alts = (s2.route_invalidations ?? []).filter((r) => r.alternative_route);
  for (const r of alts) {
    if (r.alternative_route.map_revision !== s2.scenario.scene_map.revision) fail("alternative not planned on the walls map");
    if (r.alternative_route.waypoints.slice(1, -1).some((q) => cellOf(g3, q.x_m, q.y_m) !== 0)) fail("alternative route crosses a wall");
  }
  if (s2.dispatches.length !== dispatchesBefore) fail("replan re-dispatched without approval");
  if (s2.approvals.some((a) => a.status === "active")) fail("an approval became active without a human decision");
  const cur = s2.proposals.find((p) => p.id === s2.current_proposal_id);
  if (cur && cur.status !== "pending_approval") fail(`recommendation should await approval, is ${cur.status}`);
  ok(`dispatched routes flagged invalidated (banner shown); ${alts.length} advisory alternative(s) avoid the walls; no auto-approval or re-dispatch`);
  // Victims on a wall line are reported, not given an artificial passage.
  const flagged = await page.evaluate(() => /NO ROUTE \(ON WALL\)/.test(document.querySelector('[data-feed-id="map"]')?.textContent ?? ""));
  if (!flagged) fail("victim on a wall line not labelled NO ROUTE");
  ok("victims standing on a wall line are labelled NO ROUTE (ON WALL) on the map");

  if (errors.length) fail(`console errors: ${errors.join(" | ")}`);
  ok("no console errors");
  await reset();
  console.log("PASS");
} finally {
  await browser.close();
}
