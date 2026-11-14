#!/usr/bin/env node
// End-to-end smoke test of the dashboard's API calls, through the Next.js proxy.
// Requires: backend on :8000 and `npm run dev` on :3000.
//   npm run smoke            (or BASE=http://127.0.0.1:3000 npm run smoke)
// SIMULATION ONLY — resets the local `demo` scenario.

const BASE = process.env.BASE ?? "http://127.0.0.1:3000";
const S = `${BASE}/aegis-api/api/v1/simulations/demo`;
const actor = "smoke-test";

async function call(method, path, body, key) {
  const headers = { "Content-Type": "application/json" };
  if (key) headers["Idempotency-Key"] = key;
  const res = await fetch(S + path, { method, headers, body: body ? JSON.stringify(body) : undefined });
  const json = await res.json().catch(() => null);
  if (!res.ok) throw new Error(`${method} ${path} → ${res.status} ${JSON.stringify(json?.detail)}`);
  return json;
}
function check(cond, msg) {
  if (!cond) throw new Error(`FAIL: ${msg}`);
  console.log(`  ✓ ${msg}`);
}
const current = (st) => st.proposals.find((p) => p.id === st.current_proposal_id);

const t0 = Date.now();
let st = (await call("POST", "/reset", { mode: "simulation" })).state;
check(st.scenario.casualties.every((c) => c.medical_assessment === null), "reset: all casualties unassessed");

for (const [cid, severity] of [["casualty-001", "immediate"], ["casualty-002", "delayed"], ["casualty-003", "minor"]]) {
  st = (await call("POST", `/casualties/${cid}/assessment`, {
    severity, source: "human", assessed_by: actor, assessed_at: new Date().toISOString(),
  })).state;
}
check(st.scenario.casualties.every((c) => c.medical_assessment?.source === "human"), "human assessments recorded");

let p = current(st);
check(p && p.status === "pending_approval" && p.assignments.length > 0, `pending proposal ${p?.id} rev ${p?.revision}`);
check(p.assignments.every((a) => a.route?.algorithm === "astar"), "assignments carry A* routes");
check(st.dispatches.length === 0, "no dispatch before approval");

const key = `smoke-${st.epoch}-${p.id}-r${p.revision}-approve`;
const dec = await call("POST", `/proposals/${p.id}/decision`, { mode: "simulation", proposal_revision: p.revision, decision: "approve", actor }, key);
const replay = await call("POST", `/proposals/${p.id}/decision`, { mode: "simulation", proposal_revision: p.revision, decision: "approve", actor }, key);
check(replay.duplicate && replay.result.id === dec.result.id, "double-click approval replays, not duplicated");

const disp = await call("POST", "/dispatches", {
  mode: "simulation", proposal_id: p.id, proposal_revision: p.revision, approval_id: dec.result.id, actor,
}, `smoke-dispatch-${dec.result.id}`);
check(disp.result.dispatch.real_device_effect === false, "dispatch is simulation-only");
const a = disp.result.assignments[0];

for (const kind of ["delivered", "acknowledged"]) {
  st = (await call("POST", `/assignments/${a.id}/simulate-device-event`, { mode: "simulation", kind })).state;
}
check(st.assignments.find((x) => x.id === a.id).status === "acknowledged", "acknowledgement reflected in state");

st = (await call("PATCH", "/responders/responder-002", { mode: "simulation", status: "offline", reported_at: new Date().toISOString() })).state;
check(st.scenario.responders.find((r) => r.id === "responder-002").status === "offline", "responder marked offline");

const map = st.scenario.scene_map;
const cells = map.occupancy_grid.cells.slice();
cells[10 * map.occupancy_grid.width + 3] = 100;
const before = st.scene_revision;
st = (await call("PUT", "/map", { ...map, revision: `${map.revision}-smoke`, occupancy_grid: { ...map.occupancy_grid, cells } })).state;
check(st.scene_revision > before && st.scenario.scene_map.revision.endsWith("-smoke"), "map block published, scene revision advanced");

const ev = await call("GET", "/events?after_sequence=0&limit=1000");
const seqs = ev.events.map((e) => e.sequence);
check(seqs.every((s, i) => i === 0 || s > seqs[i - 1]), `${ev.events.length} events, strictly increasing sequence`);

console.log(`\nSmoke passed in ${Date.now() - t0} ms (SIMULATION ONLY).`);
