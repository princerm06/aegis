#!/usr/bin/env node
// Browser test for the historical NYC EMS XGBoost research panel (PR #20), plus a
// non-destructive regression pass over the Command Center, Voice and Neural panels.
//
// Run once per backend mode, matching how the backend was started:
//   Run: node scripts/e2e-ems.mjs
//   EXPECT=disabled     (default backend; AEGIS_EMS_ADVISORY_ENABLED unset)
//   EXPECT=enabled      (AEGIS_EMS_ADVISORY_ENABLED=true + `uv sync --group ems`)
//   EXPECT=unavailable  (enabled, but AEGIS_EMS_MODEL_DIR points at a missing checkpoint)
// Every mode also checks a network failure of the advisory request (intercepted in the browser).
// Optional: EXPECT_SECONDS=62.51 to compare the UI against a value computed directly from the model.
// Does NOT reset the shared simulation scenario. Requires the Next app (BASE) and backend (BACKEND).
import puppeteer from "puppeteer-core";

const BASE = process.env.BASE ?? "http://127.0.0.1:3000";
const BACKEND = process.env.BACKEND ?? "http://127.0.0.1:8000";
const CHROME = process.env.CHROME_PATH ?? "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome";
const EXPECT = process.env.EXPECT ?? "disabled";
const EXPECT_SECONDS = process.env.EXPECT_SECONDS ? Number(process.env.EXPECT_SECONDS) : null;
const ENDPOINT = "/api/v1/simulations/advisories/ems-dispatch";
const EXAMPLE = "incident_datetime=2023-03-20T12%3A00%3A00&initial_call_type=RESPIR&initial_severity_level_code=4&borough=BRONX";

if (!["enabled", "disabled", "unavailable"].includes(EXPECT)) throw new Error(`EXPECT must be enabled|disabled|unavailable, got ${EXPECT}`);

const ok = (m) => console.log(`  ✓ ${m}`);
const fail = (m) => {
  throw new Error(m);
};
const text = (t) => `::-p-text(${t})`;
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

console.log(`EMS research panel browser test — EXPECT=${EXPECT} — ${BASE}/command (SIMULATION ONLY)`);

const browser = await puppeteer.launch({ executablePath: CHROME, headless: true, args: ["--window-size=1440,900"] });
const page = await browser.newPage();
await page.setViewport({ width: 1440, height: 900 });
const errors = [];
page.on("pageerror", (e) => errors.push(e.message));

// Request interception, toggled per check.
let intercept = null; // (req) => boolean (true = handled)
await page.setRequestInterception(true);
page.on("request", (req) => {
  if (req.isInterceptResolutionHandled()) return;
  if (intercept && intercept(req)) return;
  req.continue();
});

const panelStatus = () => page.evaluate(() => document.querySelector("#ems-research")?.getAttribute("data-ems-status") ?? null);
const waitStatus = async () => {
  await page.waitForFunction(() => {
    const s = document.querySelector("#ems-research")?.getAttribute("data-ems-status");
    return s && s !== "Checking…";
  }, { timeout: 15000 });
  return panelStatus();
};
const panelText = () => page.evaluate(() => document.querySelector("#ems-research")?.innerText ?? "");
const prediction = () => page.evaluate(() => document.querySelector("#ems-research [data-ems-prediction]")?.getAttribute("data-ems-prediction") ?? null);
const runDisabled = () =>
  page.evaluate(() => [...document.querySelectorAll("#ems-research button")].find((b) => /Run trained model|Calculating/.test(b.textContent))?.disabled ?? null);
const clickRun = () =>
  page.evaluate(() => [...document.querySelectorAll("#ems-research button")].find((b) => b.textContent.includes("Run trained model")).click());
const setField = async (label, value) => {
  const sel = `#ems-research input[aria-label="${label}"]`;
  await page.click(sel, { clickCount: 3 });
  await page.keyboard.press("Backspace");
  if (value) await page.type(sel, value);
};
const closeDrawer = async () => {
  // Dashboard shortcuts (incl. Escape) are ignored while typing, so leave any input first.
  await page.evaluate(() => document.activeElement instanceof HTMLElement && document.activeElement.blur());
  await page.keyboard.press("Escape");
  await page.waitForFunction(() => !document.querySelector("#ems-research"), { timeout: 5000 });
};
const openViaHeader = async () => {
  await page.locator('[aria-label="Open historical NYC EMS XGBoost research advisory"]').click();
  await page.waitForSelector("#ems-research", { timeout: 5000 });
};

try {
  await page.goto(`${BASE}/command`, { waitUntil: "domcontentloaded", timeout: 20000 });
  await page.locator(text("Recommendation")).wait();
  ok("Command Center loaded");

  // ---------- 1. Header entry opens the drawer at the panel ----------
  await openViaHeader();
  await sleep(150);
  const inView = await page.evaluate(() => {
    const r = document.querySelector("#ems-research").getBoundingClientRect();
    return r.top >= 0 && r.top < window.innerHeight;
  });
  if (!inView) fail("Header button opened the drawer but the EMS panel is not scrolled into view");
  ok("Header 'EMS ML' button opens the details drawer scrolled to the EMS panel");

  // ---------- 2. Disclaimers ----------
  const t0 = await panelText();
  for (const phrase of ["HISTORICAL · ILLUSTRATIVE", "not live emergency intelligence", "Not a medical assessment or a dispatch recommendation", "RESEARCH ONLY · NO DISPATCH AUTHORITY", "Cannot set casualty priority"]) {
    if (!t0.includes(phrase)) fail(`Disclaimer missing: "${phrase}"`);
  }
  ok("Historical / research-only / no-dispatch-authority disclaimers are visible");

  // ---------- 3. Mode-specific behaviour ----------
  const status = await waitStatus();
  if (status !== { enabled: "available", disabled: "disabled", unavailable: "unavailable" }[EXPECT]) fail(`Expected status for ${EXPECT}, got "${status}"`);
  ok(`Status badge reads "${status}"`);

  if (EXPECT === "disabled") {
    if (!(await panelText()).includes("Predictions are disabled")) fail("Disabled notice not shown");
    if ((await runDisabled()) !== true) fail("Run button should be disabled when predictions are disabled");
    if (await prediction()) fail("A prediction is shown while disabled");
    ok("Disabled: clear notice, Run disabled, no prediction rendered");
  }

  if (EXPECT === "unavailable") {
    const tx = await panelText();
    if (!tx.includes("Model unavailable (model_unavailable)")) fail("Unavailable notice not shown");
    if ((await runDisabled()) !== true) fail("Run button should be disabled when the model is unavailable");
    if (await prediction()) fail("A prediction is shown while unavailable");
    ok("Unavailable: 'Model unavailable (model_unavailable)' notice, Run disabled, no prediction");
  }

  if (EXPECT === "enabled") {
    if ((await runDisabled()) !== false) fail("Run button should be enabled");
    // ground truth straight from the backend API (which runs the shipped checkpoint)
    const api = await (await fetch(`${BACKEND}${ENDPOINT}?${EXAMPLE}`)).json();
    if (api.status !== "available" || typeof api.prediction_seconds !== "number") fail(`Backend did not return a prediction: ${JSON.stringify(api)}`);

    // loading state: hold the request briefly so "Calculating…" is observable
    let release;
    const held = new Promise((r) => (release = r));
    intercept = (req) => {
      if (req.url().includes(ENDPOINT) && req.url().includes("incident_datetime")) {
        held.then(() => req.continue());
        return true;
      }
      return false;
    };
    await clickRun();
    await page.waitForFunction(() => document.querySelector("#ems-research")?.innerText.includes("Calculating…"), { timeout: 3000 });
    if ((await runDisabled()) !== true) fail("Run button should be disabled while calculating");
    ok("Loading: 'Calculating…' shown and Run disabled while the request is in flight");
    release();
    intercept = null;
    await page.waitForSelector("#ems-research [data-ems-prediction]", { timeout: 15000 });
    const shown = Number(await prediction());
    if (Math.abs(shown - api.prediction_seconds) > 1e-6) fail(`UI prediction ${shown} != API ${api.prediction_seconds}`);
    if (EXPECT_SECONDS !== null && Math.abs(shown - EXPECT_SECONDS) > 0.01) fail(`UI prediction ${shown} != direct model ${EXPECT_SECONDS}`);
    const tx = await panelText();
    if (!tx.includes(`${api.prediction_seconds.toFixed(1)} sec`)) fail("Formatted prediction not rendered");
    if (!tx.includes(api.model_id)) fail("Model id not shown");
    for (const w of api.warnings) if (!tx.includes(w)) fail(`Backend warning not shown: ${w}`);
    if (!/Historical holdout MAE: XGBoost \d+\.\ds vs baseline \d+\.\ds/.test(tx)) fail("Benchmark caveat missing");
    ok(`Enabled: trained XGBoost prediction ${api.prediction_seconds.toFixed(2)} s rendered — matches the API${EXPECT_SECONDS !== null ? ` and the directly-computed model value (${EXPECT_SECONDS})` : ""}; model id, warnings and benchmark caveat shown`);

    // editing an input must clear a prediction that no longer matches
    await setField("NYC borough", "MANHATTAN");
    if (await prediction()) fail("Stale prediction still shown after editing inputs");
    ok("Editing an input clears the now-stale prediction");

    // blank optional field -> model's explicit missing category (not a 422)
    await setField("Initial NYC call type", "");
    await clickRun();
    await page.waitForSelector("#ems-research [data-ems-prediction]", { timeout: 15000 });
    if (!(await panelText()).includes("Missing inputs use explicit missing category")) fail("Missing-input warning not shown");
    ok("Blank optional field runs with the model's missing category and shows the backend warning");

    // invalid timestamp -> readable error, no prediction
    await setField("NYC incident timestamp", "not-a-date");
    await clickRun();
    await page.waitForFunction(() => document.querySelector("#ems-research [role=alert]"), { timeout: 10000 });
    if (await prediction()) fail("Prediction shown for invalid input");
    if (!(await panelText()).includes("Inputs rejected by the advisory API")) fail("Invalid-input error not readable");
    ok("Invalid timestamp: readable validation error, no prediction");

    // empty timestamp -> Run disabled
    await setField("NYC incident timestamp", "");
    if ((await runDisabled()) !== true) fail("Run should be disabled without a timestamp");
    ok("Run disabled when the timestamp is empty");

    // failed prediction request (proxy returns an HTML 500) -> readable error
    await setField("NYC incident timestamp", "2023-03-20T12:00:00");
    intercept = (req) => {
      if (req.url().includes(ENDPOINT) && req.url().includes("incident_datetime")) {
        req.respond({ status: 500, contentType: "text/html", body: "<html>Internal Server Error</html>" });
        return true;
      }
      return false;
    };
    await clickRun();
    await page.waitForFunction(() => document.querySelector("#ems-research")?.innerText.includes("Advisory service unreachable"), { timeout: 10000 });
    intercept = null;
    if (await prediction()) fail("Prediction shown after a failed request");
    ok("Failed prediction request (HTML 500): 'Advisory service unreachable' message, no prediction, no crash");
  }
  await closeDrawer();

  // ---------- 4. Advisory unreachable (network failure) in every mode ----------
  intercept = (req) => {
    if (req.url().includes(ENDPOINT)) {
      req.abort("connectionrefused");
      return true;
    }
    return false;
  };
  await openViaHeader();
  const st = await waitStatus();
  intercept = null;
  if (st !== "unreachable") fail(`Expected "unreachable" on network failure, got "${st}"`);
  if (!(await panelText()).includes("Advisory service unreachable")) fail("Unreachable message missing");
  if ((await runDisabled()) !== true || (await prediction())) fail("Unreachable state should disable Run and show no prediction");
  const drawerOthers = await page.evaluate(() => { const a = document.querySelector("aside"); return !!a && a.textContent.includes("Details") && a.textContent.toLowerCase().includes("neural"); });
  if (!drawerOthers) fail("Drawer did not render its other panels");
  ok("Network failure: badge 'unreachable', clear message, Run disabled, rest of drawer renders");
  await closeDrawer();

  // ---------- 5. Regression: Command Center, Neural, Voice ----------
  // feed swap + help overlay still work
  const swapped = await page.evaluate(async () => {
    const tile = document.querySelector('[data-feed-id="surveillance"] [role=button]');
    if (!tile) return "no-feed-stage";
    const r = tile.getBoundingClientRect();
    tile.dispatchEvent(new MouseEvent("click", { bubbles: true, clientX: r.x + 10, clientY: r.y + 10 }));
    await new Promise((res) => setTimeout(res, 600));
    const primary = document.querySelector('[data-feed-slot="primary"]')?.getAttribute("data-feed-id");
    document.querySelector('[data-feed-id="map"] [role=button]')?.dispatchEvent(new MouseEvent("click", { bubbles: true }));
    await new Promise((res) => setTimeout(res, 600));
    return `${primary}->${document.querySelector('[data-feed-slot="primary"]')?.getAttribute("data-feed-id")}`;
  });
  if (swapped !== "surveillance->map") fail(`Camera feed swap regression: ${swapped}`);
  ok("Command Center: camera feed swap works (Building Cam ↔ Scene Map)");
  await page.keyboard.press("?");
  await page.locator(text("Keyboard shortcuts")).wait();
  await page.keyboard.press("Escape");
  ok("Command Center: shortcut help opens and closes");

  // Neural panel: opens via 'details ›', renders, collapses and expands
  await page.locator(text("details ›")).click();
  await page.locator(text("Details & Optimization Intelligence")).wait();
  const neural = await page.evaluate(() => document.body.innerText.toLowerCase().includes("neural optimization intelligence"));
  if (!neural) fail("Neural Optimization Intelligence panel missing");
  await page.locator('aside [aria-label="Collapse panel"]').click();
  await page.locator('aside [aria-label="Expand panel"]').wait();
  await page.locator('aside [aria-label="Expand panel"]').click();
  await page.locator('aside [aria-label="Collapse panel"]').wait();
  const emsBelow = await page.evaluate(() => !!document.querySelector("#ems-research"));
  if (!emsBelow) fail("EMS panel missing from the drawer when opened via details");
  ok("Neural panel: renders in the drawer, collapse/expand works; EMS panel coexists");
  await closeDrawer();

  // Voice panel: expand, ask a quick prompt, get a turn back (read-only)
  const exp = await page.$('[aria-label="Expand Ask AEGIS panel"]');
  if (exp) await exp.click();
  await page.waitForSelector('[data-testid="ask-aegis-input"]', { timeout: 5000 });
  await page.type('[data-testid="ask-aegis-input"]', "What's our situation?");
  await page.click('[data-testid="ask-aegis-send"]');
  const voiceOutcome = await page
    .waitForFunction(
      () => document.querySelector('[data-testid="ask-aegis-turn"]') || document.querySelector('[data-testid="ask-aegis-banner"]'),
      { timeout: 20000 },
    )
    .then(() => page.evaluate(() => (document.querySelector('[data-testid="ask-aegis-turn"]') ? "turn" : `banner: ${document.querySelector('[data-testid="ask-aegis-banner"]').innerText}`)));
  if (voiceOutcome !== "turn") fail(`Voice panel did not answer: ${voiceOutcome}`);
  ok("Voice panel: typed question answered with a read-only turn");

  if (errors.length) fail(`Page errors: ${errors.join(" | ")}`);
  ok("No page errors");
  console.log(`\nEMS e2e passed (EXPECT=${EXPECT}, SIMULATION ONLY).`);
} catch (e) {
  console.error(`\nEMS e2e FAILED (EXPECT=${EXPECT}): ${e.message}`);
  process.exitCode = 1;
} finally {
  await browser.close();
}
