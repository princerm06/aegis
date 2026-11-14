#!/usr/bin/env node
// Browser click-through of the command center against the live backend.
// Requires: backend on :8000, `npm run dev` on :3000, Google Chrome installed.
//   npm run e2e             (CHROME_PATH=/path/to/chrome to override, HEADFUL=1 to watch)
// SIMULATION ONLY — resets the local `demo` scenario.
import puppeteer from "puppeteer-core";

const BASE = process.env.BASE ?? "http://127.0.0.1:3000";
const API = `${BASE}/aegis-api/api/v1/simulations/demo`;
const CHROME = process.env.CHROME_PATH ?? "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome";

async function api(method, path, body) {
  const res = await fetch(API + path, { method, headers: { "Content-Type": "application/json" }, body: body ? JSON.stringify(body) : undefined });
  if (!res.ok) throw new Error(`${method} ${path} → ${res.status} ${await res.text()}`);
  return res.json();
}
const ok = (m) => console.log(`  ✓ ${m}`);
const text = (t) => `::-p-text(${t})`;

await api("POST", "/reset", { mode: "simulation" });
const browser = await puppeteer.launch({ executablePath: CHROME, headless: !process.env.HEADFUL, args: ["--window-size=1440,900"] });
const page = await browser.newPage();
await page.setViewport({ width: 1440, height: 900 });
const errors = [];
page.on("pageerror", (e) => errors.push(e.message));

try {
  await page.goto(`${BASE}/command`, { waitUntil: "domcontentloaded" });
  await page.locator(text("Scene map")).wait();
  ok("dashboard loaded with live state");

  // 1. Human priority from the victim strip editor.
  await page.locator('[data-cid="casualty-001"]').click();
  await page.locator(text("Command priority (human decision)")).wait();
  await page.locator("button::-p-text(Immediate)").click();
  await page.waitForFunction(() => document.querySelector('[data-cid="casualty-001"]')?.textContent?.includes("Immediate"));
  ok("priority entered via editor → card shows IMMEDIATE");

  // 2. Operator observation is stored and shown.
  const bleedingRow = await page.waitForSelector("xpath///span[text()='Bleeding']/following-sibling::div//button[1]");
  await bleedingRow.click();
  await page.waitForFunction(() => document.querySelector('[data-cid="casualty-001"]')?.textContent?.toLowerCase().includes("bleed yes"));
  ok("operator observation (bleeding: yes) shown on card");
  await page.keyboard.press("Escape");

  // 3. Replan diff after a map change on the pending route.
  let st = await api("GET", "/state");
  const p = st.proposals.find((x) => x.id === st.current_proposal_id);
  const wp = p.assignments[0].route.waypoints;
  const mid = wp[Math.floor(wp.length / 2)];
  const map = st.scenario.scene_map;
  const cells = map.occupancy_grid.cells.slice();
  cells[Math.floor(mid.y_m) * map.occupancy_grid.width + Math.floor(mid.x_m)] = 100;
  await api("PUT", "/map", { ...map, revision: `${map.revision}-e2e`, occupancy_grid: { ...map.occupancy_grid, cells } });
  await page.locator(text("Changed since rev")).wait();
  ok("map change → 'Changed since rev' diff shown");

  // 4. Approve (keyboard) then dispatch (button) — separate explicit steps.
  await page.locator(text("Approve rev")).wait();
  await page.keyboard.press("a");
  const dispatchBtn = await page.locator("button::-p-text(Dispatch)").waitHandle();
  ok("A approves exact revision; Dispatch appears as a separate step");
  st = await api("GET", "/state");
  if (st.dispatches.length !== 0) throw new Error("dispatched without explicit dispatch action");
  await dispatchBtn.click();
  await page.locator(text("awaiting ack")).wait().catch(() => page.locator(text("sent")).wait());
  ok("dispatch clicked → tasks awaiting acknowledgement");

  // 5. Acknowledgement from an external bridge (like the Arduino) is labelled as such.
  st = await api("GET", "/state");
  const a = st.assignments[0];
  const at = new Date().toISOString();
  for (const kind of ["delivered", "acknowledged"]) {
    await api("POST", "/device-events", {
      id: `e2e-bridge-${a.id}-${kind}`, scenario_id: "demo", device_id: a.device_id, dispatch_id: a.dispatch_id,
      command_id: a.command_id, casualty_id: a.casualty_id, responder_id: a.responder_id, kind, occurred_at: at, mode: "simulation",
    });
  }
  await page.locator(text("via device")).wait();
  await page.locator(text("acknowledged")).wait();
  ok("bridge acknowledgement → 'via device' + alert");

  // 6. Shortcut help + timeline drawer open.
  await page.keyboard.press("?");
  await page.locator(text("Keyboard shortcuts")).wait();
  await page.keyboard.press("Escape");
  await page.keyboard.press("l");
  await page.locator(text("Incident timeline")).wait();
  await page.keyboard.press("Escape");
  ok("shortcut help and timeline drawer open");

  if (errors.length) throw new Error(`page errors: ${errors.join(" | ")}`);
  console.log("\nE2E passed (SIMULATION ONLY).");
} catch (e) {
  await page.screenshot({ path: "e2e-failure.png" }).catch(() => {});
  console.error(`\nE2E FAILED: ${e.message}\n(screenshot: frontend/e2e-failure.png)`);
  process.exitCode = 1;
} finally {
  await browser.close();
}
