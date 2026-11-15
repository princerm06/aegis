#!/usr/bin/env node
// Browser test: the Ask AEGIS (ElevenLabs) voice assistant is visible and usable in the Command Center,
// and opening it never hides responder Comms. Non-destructive (does not reset the simulation).
// Requires: the Next app (BASE, default :3000), backend (BACKEND, default :8000) and the Voice
// service behind /aegis-voice (default :8010). Run: node scripts/e2e-ask-aegis.mjs
// Works with or without ElevenLabs credentials: without them it verifies the text-only fallback.
import puppeteer from "puppeteer-core";

const BASE = process.env.BASE ?? "http://127.0.0.1:3000";
const BACKEND = process.env.BACKEND ?? "http://127.0.0.1:8000";
const CHROME = process.env.CHROME_PATH ?? "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome";
const SIZES = [[1440, 900], [1366, 768], [1280, 800], [1280, 720]];

const ok = (m) => console.log(`  ✓ ${m}`);
const fail = (m) => {
  throw new Error(m);
};
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const PANEL = '[data-testid="ask-aegis-panel"]';
const SHEET = '[data-testid="ask-aegis-sheet"]';

/** Element is on screen and actually receives clicks at its centre (not covered). */
const usable = (page, sel) =>
  page.evaluate((sel) => {
    const el = typeof sel === "string" ? document.querySelector(sel) : null;
    if (!el) return "missing";
    const r = el.getBoundingClientRect();
    if (r.height === 0 || r.top < 0 || r.bottom > innerHeight || r.right > innerWidth) return `off-screen (${Math.round(r.top)}..${Math.round(r.bottom)})`;
    const hit = document.elementFromPoint(r.left + r.width / 2, r.top + Math.min(r.height / 2, 12));
    return el.contains(hit) ? "ok" : `covered by ${hit?.tagName}`;
  }, sel);
const commsUsable = (page) =>
  page.evaluate(() => {
    const out = {};
    for (const label of ["Evacuate now", "Say"]) {
      const el = [...document.querySelectorAll("button")].find((b) => b.textContent.trim() === label);
      if (!el) {
        out[label] = "missing";
        continue;
      }
      const r = el.getBoundingClientRect();
      const hit = document.elementFromPoint(r.left + r.width / 2, r.top + r.height / 2);
      out[label] = r.bottom <= innerHeight && el.contains(hit) ? "ok" : "hidden";
    }
    return out;
  });
const snapshot = async () => {
  const s = await (await fetch(`${BACKEND}/api/v1/simulations/demo/state`)).json();
  return JSON.stringify({
    epoch: s.epoch, rev: s.scene_revision, proposal: s.current_proposal_id, approvals: s.approvals?.length,
    assignments: s.assignments?.map((a) => [a.id, a.status]),
    severity: s.scenario.casualties.map((c) => [c.id, c.medical_assessment?.severity ?? null]),
    responders: s.scenario.responders.map((r) => [r.id, r.status]),
  });
};

console.log(`Ask AEGIS browser test — ${BASE}/command (SIMULATION ONLY)`);
const browser = await puppeteer.launch({
  executablePath: CHROME,
  headless: true,
  args: ["--use-fake-device-for-media-stream", "--use-fake-ui-for-media-stream"],
});
const errors = [];
try {
  const health = await (await fetch(`${BASE}/aegis-voice/health`)).json();
  if (health.service !== "aegis-voice" || health.read_only !== true) fail(`Voice proxy did not reach the Voice service: ${JSON.stringify(health)}`);
  ok(`Next.js /aegis-voice proxy reaches the Voice service (mode ${health.operational_mode}, speech configured: ${health.speech_Configured})`);

  const page = await browser.newPage();
  page.on("pageerror", (e) => errors.push(`pageerror: ${e.message}`));
  page.on("console", (m) => {
    if ((m.type() === "error" || m.type() === "warning") && !/favicon/.test(m.location()?.url ?? "")) errors.push(`${m.type()}: ${m.text()}`);
  });
  await browser.defaultBrowserContext().overridePermissions(BASE, ["microphone"]);

  // 1. Visible and openable at every laptop size; opening never displaces Comms.
  for (const [w, h] of SIZES) {
    await page.setViewport({ width: w, height: h });
    await page.goto(`${BASE}/command`, { waitUntil: "networkidle0" });
    await page.waitForSelector(PANEL);
    const bar = await usable(page, PANEL);
    if (bar !== "ok") fail(`${w}x${h}: Ask AEGIS bar not visible (${bar})`);
    const before = await commsUsable(page);
    if (before.Say !== "ok" && h >= 800) fail(`${w}x${h}: Comms not usable before opening (${JSON.stringify(before)})`);
    await page.click('[aria-label="Expand Ask AEGIS panel"]');
    await page.waitForSelector(SHEET);
    for (const sel of [SHEET, '[data-testid="ask-aegis-input"]', '[data-testid="ask-aegis-send"]']) {
      const st = await usable(page, sel);
      if (st !== "ok") fail(`${w}x${h}: ${sel} not usable when open (${st})`);
    }
    await page.click('[data-testid="ask-aegis-input"]'); // focus inside the sheet, then Escape closes it
    await page.keyboard.press("Escape");
    await page.waitForFunction((s) => !document.querySelector(s), {}, SHEET);
    const after = await commsUsable(page);
    if (JSON.stringify(after) !== JSON.stringify(before)) fail(`${w}x${h}: Comms changed after open/close (${JSON.stringify(before)} -> ${JSON.stringify(after)})`);
    ok(`${w}x${h}: bar visible; open sheet shows input + Ask; Escape closes; Comms unchanged (${JSON.stringify(after)})`);
  }

  // 2. Grounded text question + quick prompt, read-only.
  await page.setViewport({ width: 1440, height: 900 });
  await page.goto(`${BASE}/command`, { waitUntil: "networkidle0" });
  const writes = [];
  page.on("request", (r) => {
    const u = new URL(r.url());
    if (r.method() !== "GET" && r.method() !== "OPTIONS" && u.pathname.startsWith("/aegis-api")) writes.push(`${r.method()} ${u.pathname}`);
  });
  const stateBefore = await snapshot();
  await page.click('[aria-label="Expand Ask AEGIS panel"]');
  await page.type('[data-testid="ask-aegis-input"]', "Which victims are unassigned?");
  await page.click('[data-testid="ask-aegis-send"]');
  await page.waitForSelector('[data-testid="ask-aegis-turn"]', { timeout: 20000 });
  const first = await page.$eval('[data-testid="ask-aegis-turn"]', (e) => e.innerText);
  if (first.trim().length < 10) fail(`Empty answer: ${first}`);
  ok(`Typed question answered: "${first.replace(/\s+/g, " ").slice(0, 110)}…"`);
  await page.evaluate(() => [...document.querySelectorAll(`[data-testid="ask-aegis-sheet"] button`)].find((b) => /situation/i.test(b.textContent))?.click());
  await page.waitForFunction(() => document.querySelectorAll('[data-testid="ask-aegis-turn"]').length >= 2, { timeout: 20000 });
  ok("Quick-prompt chip answered (2 turns shown)");
  if ((await usable(page, '[data-testid="ask-aegis-turn"]')) !== "ok") fail("Newest answer not visible in the open sheet");
  ok("Newest answer is visible without scrolling");

  // 3. Microphone: permission granted (fake device) — records, then either speech or a clear text-only note.
  await page.click('[data-testid="ask-aegis-ptt"]');
  await page.waitForFunction(() => /Stop & Send/.test(document.querySelector('[data-testid="ask-aegis-ptt"]').textContent), { timeout: 5000 });
  ok("Mic permission granted: recording starts ('● Stop & Send')");
  await sleep(1500);
  await page.click('[data-testid="ask-aegis-ptt"]');
  const micOutcome = await page
    .waitForFunction(
      () => {
        const banner = document.querySelector('[data-testid="ask-aegis-banner"]')?.innerText;
        const turns = document.querySelectorAll('[data-testid="ask-aegis-turn"]').length;
        return banner || turns >= 3 ? banner || "turn" : false;
      },
      { timeout: 25000 },
    )
    .then((h) => h.jsonValue());
  ok(`Mic turn finished: ${micOutcome === "turn" ? "speech answered" : `fallback note — "${micOutcome.slice(0, 120)}"`}`);

  // 4. Mute toggle works.
  const m0 = await page.$eval('[data-testid="ask-aegis-mute"]', (e) => e.textContent);
  await page.click('[data-testid="ask-aegis-mute"]');
  const m1 = await page.$eval('[data-testid="ask-aegis-mute"]', (e) => e.textContent);
  if (m0 === m1) fail("Mute toggle did not change");
  ok(`Mute toggle: "${m0.trim()}" → "${m1.trim()}"`);

  // 5. Read-only: no writes to the AEGIS backend, state unchanged.
  await sleep(500);
  const stateAfter = await snapshot();
  if (writes.length) fail(`Voice caused backend writes: ${writes.join(", ")}`);
  if (stateBefore !== stateAfter) fail("Simulation state changed during Voice use");
  ok("Read-only: no POST/PUT/PATCH/DELETE to the AEGIS backend; priorities/approvals/assignments/dispatch unchanged");

  // 6. Microphone denied → clear message, typed input still works.
  const ctx = await browser.createBrowserContext();
  const p2 = await ctx.newPage();
  await p2.setViewport({ width: 1366, height: 768 });
  await p2.evaluateOnNewDocument(() => {
    navigator.mediaDevices.getUserMedia = () => Promise.reject(new DOMException("Permission denied", "NotAllowedError"));
  });
  await p2.goto(`${BASE}/command`, { waitUntil: "networkidle0" });
  await p2.click('[data-testid="ask-aegis-ptt"]');
  await p2.waitForFunction(() => /permission denied|unavailable/i.test(document.querySelector('[data-testid="ask-aegis-banner"]')?.innerText ?? ""), { timeout: 5000 });
  await p2.type('[data-testid="ask-aegis-input"]', "What's our situation?");
  await p2.click('[data-testid="ask-aegis-send"]');
  await p2.waitForSelector('[data-testid="ask-aegis-turn"]', { timeout: 20000 });
  ok("Mic denied: 'Microphone permission denied…' note shown; typed question still answered");
  await ctx.close();

  // 7. Light theme.
  await page.evaluate(() => localStorage.setItem("aegis.theme", "light"));
  await page.reload({ waitUntil: "networkidle0" });
  if ((await page.evaluate(() => document.documentElement.dataset.theme)) !== "light") fail("Light theme not applied");
  if ((await usable(page, PANEL)) !== "ok") fail("Ask AEGIS bar not visible in light theme");
  await page.evaluate(() => localStorage.setItem("aegis.theme", "dark"));
  ok("Light theme: Ask AEGIS bar visible");

  if (errors.length) fail(`Console errors/warnings: ${errors.join(" | ")}`);
  ok("No React errors, console errors or warnings (favicon 404 excluded)");
  console.log("\nAsk AEGIS e2e passed (SIMULATION ONLY).");
} catch (e) {
  console.error(`\nAsk AEGIS e2e FAILED: ${e.message}`);
  process.exitCode = 1;
} finally {
  await browser.close();
}
