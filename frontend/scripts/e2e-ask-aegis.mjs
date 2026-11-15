#!/usr/bin/env node
// Browser test: the Ask AEGIS (ElevenLabs) voice assistant is visible and usable in the Command Center,
// and opening it never hides responder Comms. Non-destructive (does not reset the simulation).
// Requires: the Next app (BASE, default :3000), backend (BACKEND, default :8000) and the Voice
// service behind /aegis-voice (default :8010). Run: node scripts/e2e-ask-aegis.mjs
// Works with or without ElevenLabs credentials: in text-only mode it verifies the mic explains itself
// and never records; with speech (real ElevenLabs, or the scripts/mock-voice-speech.mjs test stub) it
// runs record → transcript → answer → spoken playback → Stop/Play/Mute, cancel, and mic cleanup.
// LAN_BASE (optional, e.g. http://192.168.x.y:3000): also checks the plain-HTTP LAN page explains the mic.
import puppeteer from "puppeteer-core";

const BASE = process.env.BASE ?? "http://127.0.0.1:3000";
const BACKEND = process.env.BACKEND ?? "http://127.0.0.1:8000";
const LAN_BASE = process.env.LAN_BASE;
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
  args: ["--use-fake-device-for-media-stream", "--use-fake-ui-for-media-stream", "--autoplay-policy=no-user-gesture-required"],
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

  // 3. Microphone. Track every stream the page opens so cleanup can be checked.
  const speech = health.operational_mode === "full_speech";
  const voiceTurnRequests = [];
  page.on("request", (r) => r.url().includes("/aegis-voice/api/v1/voice/audio-turn") && voiceTurnRequests.push(Date.now()));
  const status = () => page.$eval('[data-testid="ask-aegis-status"]', (e) => e.textContent.trim());
  const banner = () => page.evaluate(() => document.querySelector('[data-testid="ask-aegis-banner"]')?.innerText ?? "");
  const turnCount = () => page.$$eval('[data-testid="ask-aegis-turn"]', (t) => t.length);
  const liveTracks = () => page.evaluate(() => (window.__aegisStreams ?? []).flatMap((s) => s.getTracks()).filter((t) => t.readyState !== "ended").length);
  await page.evaluateOnNewDocument(() => {
    const orig = navigator.mediaDevices?.getUserMedia?.bind(navigator.mediaDevices);
    if (!orig) return;
    window.__aegisStreams = [];
    navigator.mediaDevices.getUserMedia = async (c) => {
      const s = await orig(c);
      window.__aegisStreams.push(s);
      return s;
    };
  });
  await page.reload({ waitUntil: "networkidle0" });
  await page.click('[aria-label="Expand Ask AEGIS panel"]');
  await page.waitForSelector('[data-testid="ask-aegis-capabilities"]');
  const caps = await page.$eval('[data-testid="ask-aegis-capabilities"]', (e) => e.textContent);
  ok(`Capability line shown up front: "${caps.trim()}"`);

  if (!speech) {
    const mic = await page.$eval('[data-testid="ask-aegis-ptt"]', (e) => [e.dataset.mic, e.textContent]);
    if (mic[0] !== "speech-off" || !/Mic off/.test(mic[1])) fail(`text-only host: mic should be off with a reason, got ${mic}`);
    await page.click('[data-testid="ask-aegis-ptt"]');
    await sleep(500);
    if (!/voice\/\.env/.test(await banner())) fail(`speech-off explanation missing: ${await banner()}`);
    if (voiceTurnRequests.length || (await liveTracks())) fail("text-only host: mic must not record or send");
    ok("Text-only host: 🎙 Mic off explains voice/.env setup on press; nothing recorded or sent (typed questions still work)");
  } else {
    // Record → transcript → grounded answer → spoken playback.
    const n0 = await turnCount();
    await page.click('[data-testid="ask-aegis-ptt"]');
    await page.waitForFunction(() => /Stop & Send/.test(document.querySelector('[data-testid="ask-aegis-ptt"]').textContent), { timeout: 5000 });
    await sleep(1300);
    if (!/LISTENING 0:0[1-9]/.test(await status())) fail(`listening timer not shown: ${await status()}`);
    ok(`Recording: status "${await status()}" with timer; Cancel (✕) offered`);
    await page.click('[data-testid="ask-aegis-ptt"]');
    await page.waitForFunction((n) => document.querySelectorAll('[data-testid="ask-aegis-turn"]').length > n, { timeout: 25000 }, n0);
    const q = await page.$eval('[data-testid="ask-aegis-question"]', (e) => e.textContent);
    if (/spoken audio/.test(q)) fail("transcript not shown");
    ok(`Voice turn: transcript "${q.replace(/^You:\s*/, "").trim()}" → grounded answer`);
    if (await liveTracks()) fail("microphone still live after Stop & Send");
    ok("Microphone released after Stop & Send (all tracks ended)");
    await page.waitForFunction(() => /SPEAKING/.test(document.querySelector('[data-testid="ask-aegis-status"]').textContent), { timeout: 8000 });
    await page.click('[data-testid="ask-aegis-stop"]');
    await page.waitForFunction(() => !/SPEAKING/.test(document.querySelector('[data-testid="ask-aegis-status"]').textContent), { timeout: 3000 });
    ok("Spoken answer played; ■ Stop ends playback immediately");
    await page.click('[data-testid="ask-aegis-play"]');
    await page.waitForFunction(() => /SPEAKING/.test(document.querySelector('[data-testid="ask-aegis-status"]').textContent), { timeout: 3000 });
    await page.click('[data-testid="ask-aegis-mute"]');
    await page.waitForFunction(() => !/SPEAKING/.test(document.querySelector('[data-testid="ask-aegis-status"]').textContent), { timeout: 3000 });
    await page.click('[data-testid="ask-aegis-mute"]');
    ok("▶ Play replays an answer; muting stops playback");

    // Contextual follow-up.
    const n1 = await turnCount();
    await page.type('[data-testid="ask-aegis-input"]', "Tell me more.");
    await page.click('[data-testid="ask-aegis-send"]');
    await page.waitForFunction((n) => document.querySelectorAll('[data-testid="ask-aegis-turn"]').length > n, { timeout: 20000 }, n1);
    ok("Follow-up 'Tell me more.' answered in the same session");

    // Cancel with Escape: nothing sent, mic released.
    await page.evaluate(() => document.querySelector('[data-testid="ask-aegis-stop"]')?.click());
    const sentBefore = voiceTurnRequests.length;
    const n2 = await turnCount();
    await page.click('[data-testid="ask-aegis-ptt"]');
    await page.waitForFunction(() => /Stop & Send/.test(document.querySelector('[data-testid="ask-aegis-ptt"]').textContent), { timeout: 5000 });
    await sleep(800);
    await page.keyboard.press("Escape");
    await page.waitForFunction(() => /nothing was sent/.test(document.querySelector('[data-testid="ask-aegis-banner"]')?.innerText ?? ""), { timeout: 3000 });
    await sleep(500);
    if (voiceTurnRequests.length !== sentBefore || (await turnCount()) !== n2) fail("cancelled recording was sent");
    if (await liveTracks()) fail("microphone still live after cancel");
    ok("Esc cancels a recording: nothing sent, microphone released");

    // Leaving the page mid-recording: never sends, mic released.
    await page.click('[data-testid="ask-aegis-ptt"]');
    await page.waitForFunction(() => /Stop & Send/.test(document.querySelector('[data-testid="ask-aegis-ptt"]').textContent), { timeout: 5000 });
    await sleep(600);
    await page.click('a[href="/"]');
    await page.waitForFunction(() => location.pathname === "/", { timeout: 10000 });
    await sleep(800);
    if (voiceTurnRequests.length !== sentBefore) fail("recording was sent after leaving the page");
    if (await liveTracks()) fail("microphone still live after leaving the page");
    ok("Leaving the Command Center mid-recording: nothing sent, microphone released");
    await page.goto(`${BASE}/command`, { waitUntil: "networkidle0" });
  }

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

  // 6. Microphone denied → recovery steps, typed input still works (only reachable when speech is on).
  if (speech) {
    const ctx = await browser.createBrowserContext();
    const p2 = await ctx.newPage();
    await p2.setViewport({ width: 1366, height: 768 });
    await p2.evaluateOnNewDocument(() => {
      navigator.mediaDevices.getUserMedia = () => Promise.reject(new DOMException("Permission denied", "NotAllowedError"));
    });
    await p2.goto(`${BASE}/command`, { waitUntil: "networkidle0" });
    await p2.waitForFunction(() => document.querySelector('[data-testid="ask-aegis-ptt"]')?.dataset.mic === "ready", { timeout: 10000 });
    await p2.click('[data-testid="ask-aegis-ptt"]');
    await p2.waitForFunction(() => /allow Microphone/.test(document.querySelector('[data-testid="ask-aegis-banner"]')?.innerText ?? ""), { timeout: 5000 });
    await p2.type('[data-testid="ask-aegis-input"]', "What's our situation?");
    await p2.click('[data-testid="ask-aegis-send"]');
    await p2.waitForSelector('[data-testid="ask-aegis-turn"]', { timeout: 20000 });
    ok("Mic denied: recovery steps ('allow Microphone…') shown; typed question still answered");
    await ctx.close();
  } else ok("Mic denial: not reachable on a text-only host (mic is off before any permission prompt)");

  // 6b. Voice service offline → clear state + Retry; recovers when it's back.
  {
    const p3 = await browser.newPage();
    await p3.setViewport({ width: 1366, height: 768 });
    await p3.setRequestInterception(true);
    let block = true;
    p3.on("request", (r) => (block && r.url().includes("/aegis-voice/") ? r.abort() : r.continue()));
    await p3.goto(`${BASE}/command`, { waitUntil: "networkidle0" });
    await p3.waitForFunction(() => /VOICE OFFLINE/.test(document.querySelector('[data-testid="ask-aegis-status"]').textContent), { timeout: 10000 });
    await p3.click('[aria-label="Expand Ask AEGIS panel"]');
    await p3.type('[data-testid="ask-aegis-input"]', "What's our situation?");
    await p3.click('[data-testid="ask-aegis-send"]');
    await p3.waitForSelector('[data-testid="ask-aegis-retry"]', { timeout: 5000 });
    if (!/Command Center is unaffected/.test(await p3.$eval('[data-testid="ask-aegis-banner"]', (e) => e.innerText))) fail("offline banner wording");
    block = false;
    await p3.click('[data-testid="ask-aegis-retry"]');
    await p3.waitForFunction(() => !/OFFLINE/.test(document.querySelector('[data-testid="ask-aegis-status"]').textContent), { timeout: 5000 });
    ok("Voice service offline: VOICE OFFLINE status, explanation + Retry; Retry recovers when it's back");
    await p3.close();
  }

  // 6c. Plain-HTTP LAN page (teammates on the hotspot): mic explains the HTTPS/localhost fix.
  if (LAN_BASE) {
    const p4 = await browser.newPage();
    await p4.setViewport({ width: 1366, height: 768 });
    await p4.goto(`${LAN_BASE}/command`, { waitUntil: "networkidle0" });
    await p4.waitForFunction(() => document.querySelector('[data-testid="ask-aegis-ptt"]')?.dataset.mic === "insecure", { timeout: 10000 });
    await p4.click('[data-testid="ask-aegis-ptt"]');
    await p4.waitForFunction(() => /localhost/.test(document.querySelector('[data-testid="ask-aegis-banner"]')?.innerText ?? ""), { timeout: 5000 });
    await p4.type('[data-testid="ask-aegis-input"]', "Where is Responder Alpha?");
    await p4.click('[data-testid="ask-aegis-send"]');
    await p4.waitForSelector('[data-testid="ask-aegis-turn"]', { timeout: 20000 });
    ok(`Plain-HTTP LAN page (${LAN_BASE}): 🎙 Mic off explains localhost/HTTPS; typed question answered`);
    await p4.close();
  } else ok("Plain-HTTP LAN check skipped (set LAN_BASE to run it)");

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
