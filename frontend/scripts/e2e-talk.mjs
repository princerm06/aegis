#!/usr/bin/env node
// Browser test: responder talk link (operator dashboard ⇄ /responder phone page) against the
// signed-in relay from PR #37. Two isolated browser contexts with fake microphones stand in for the
// operator laptop and the phone. Does not change simulation state.
//
// Needs: the Next app (BASE) proxying /aegis-api to a backend that has the talk relay WITH auth
// (#34 + #37) and AEGIS_TALK_OPERATOR_TOKEN set. Pass the same secret here as TALK_OPERATOR_TOKEN.
// Never commit a real secret; generate a throwaway one for testing.
// Run: TALK_OPERATOR_TOKEN=... BASE=http://127.0.0.1:3500 node scripts/e2e-talk.mjs
import puppeteer from "puppeteer-core";

const BASE = process.env.BASE ?? "http://127.0.0.1:3000";
const SECRET = process.env.TALK_OPERATOR_TOKEN;
const CHROME = process.env.CHROME_PATH ?? "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome";
const API = `${BASE}/aegis-api/api/v1/simulations/talk`;
if (!SECRET) {
  console.error("Set TALK_OPERATOR_TOKEN to the backend's AEGIS_TALK_OPERATOR_TOKEN.");
  process.exit(2);
}

const ok = (m) => console.log(`  ✓ ${m}`);
const fail = (m) => {
  throw new Error(m);
};
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const code = async (method, path, token) =>
  (await fetch(`${API}/${path}`, { method, headers: token ? { Authorization: `Bearer ${token}` } : {} })).status;

/** Records every RTCPeerConnection and mic stream a page creates (for audio + cleanup checks). */
const instrument = () => {
  window.__pcs = [];
  window.__streams = [];
  const PC = window.RTCPeerConnection;
  window.RTCPeerConnection = function (...a) {
    const pc = new PC(...a);
    window.__pcs.push(pc);
    return pc;
  };
  window.RTCPeerConnection.prototype = PC.prototype;
  const gum = navigator.mediaDevices?.getUserMedia?.bind(navigator.mediaDevices);
  if (gum)
    navigator.mediaDevices.getUserMedia = async (c) => {
      const s = await gum(c);
      window.__streams.push(s);
      return s;
    };
};
const audioBytes = (page) =>
  page.evaluate(async () => {
    const pc = window.__pcs.at(-1);
    if (!pc) return { rx: 0, tx: 0, state: "none" };
    let rx = 0;
    let tx = 0;
    (await pc.getStats()).forEach((r) => {
      if (r.type === "inbound-rtp" && r.kind === "audio") rx += r.bytesReceived ?? 0;
      if (r.type === "outbound-rtp" && r.kind === "audio") tx += r.bytesSent ?? 0;
    });
    return { rx, tx, state: pc.connectionState };
  });
const liveMicTracks = (page) => page.evaluate(() => window.__streams.flatMap((s) => s.getTracks()).filter((t) => t.readyState === "live").length);
const openPcs = (page) => page.evaluate(() => window.__pcs.filter((p) => p.connectionState !== "closed").length);
const attr = (page, sel, name) => page.$eval(sel, (e, n) => e.getAttribute(n), name).catch(() => null);
const pages = new Set();
const describe = async (p) =>
  p.isClosed()
    ? "closed"
    : p
        .evaluate(() => ({
          url: location.pathname,
          talk: document.querySelector('[data-testid="talk-link"]')?.dataset,
          talkText: document.querySelector('[data-testid="talk-link"]')?.innerText.replace(/\s+/g, " ").slice(0, 200),
          phone: document.querySelector('[data-testid="phone-status"]')?.textContent,
          phoneErr: document.querySelector('[data-testid="phone-error"]')?.textContent,
          phoneInfo: document.querySelector('[data-testid="phone-info"]')?.textContent,
          pcs: (window.__pcs ?? []).map((x) => `${x.connectionState}/${x.iceConnectionState}/${x.signalingState}`),
        }))
        .then((d) => JSON.stringify(d))
        .catch((e) => `unreadable: ${e.message}`);
const waitAttr = async (page, sel, name, val, timeout = 15000) => {
  pages.add(page);
  try {
    await page.waitForFunction((s, n, v) => document.querySelector(s)?.getAttribute(n) === v, { timeout }, sel, name, val);
  } catch {
    const states = await Promise.all([...pages].map(describe));
    fail(`waited ${timeout} ms for ${sel}[${name}=${val}]\n    ${states.join("\n    ")}`);
  }
};

const browser = await puppeteer.launch({
  executablePath: CHROME,
  headless: true,
  args: ["--use-fake-device-for-media-stream", "--use-fake-ui-for-media-stream", "--autoplay-policy=no-user-gesture-required"],
});
const errors = [];
/** Every talk-relay request any page sends, with whether it carried a Bearer credential. */
const talkRequests = [];
const watchTalk = (p, who) =>
  p.on("request", (r) => {
    const u = new URL(r.url());
    if (!u.pathname.includes("/simulations/talk/")) return;
    const auth = r.headers().authorization ?? "";
    talkRequests.push({ who, method: r.method(), path: u.pathname.replace(/^.*\/simulations\/talk/, ""), bearer: /^Bearer \S{16,}$/.test(auth) });
  });
try {
  console.log("Relay authorization through the dashboard proxy:");
  for (const [m, p] of [["GET", "channels/responder-001"], ["POST", "channels/responder-001/heartbeat"], ["GET", "ice-servers"], ["POST", "channels/responder-001/enroll"]])
    if ((await code(m, p)) !== 401) fail(`${m} ${p} without credentials should be 401`);
  if ((await code("GET", "channels/responder-001", "wrong-secret")) !== 401) fail("wrong operator secret should be 401");
  if ((await code("GET", "channels/responder-001", SECRET)) !== 200) fail("operator secret rejected (is Authorization forwarded by the proxy?)");
  ok("no/wrong credentials → 401 on status, heartbeat, ice-servers, enroll; operator secret → 200 (proxy forwards Authorization)");

  const opCtx = await browser.createBrowserContext();
  const phCtx = await browser.createBrowserContext();
  for (const c of [opCtx, phCtx]) await c.overridePermissions(BASE, ["microphone"]);
  const op = await opCtx.newPage();
  const ph = await phCtx.newPage();
  for (const [p, who] of [[op, "operator"], [ph, "phone"]]) {
    await p.evaluateOnNewDocument(instrument);
    watchTalk(p, who);
    p.on("pageerror", (e) => errors.push(`${who} pageerror: ${e.message}`));
    p.on("console", (m) => m.type() === "error" && !/favicon|401|403|Failed to load resource/.test(m.text()) && errors.push(`${who}: ${m.text()}`));
  }
  await op.setViewport({ width: 1440, height: 900 });
  await ph.setViewport({ width: 390, height: 844, isMobile: true, hasTouch: true });
  ph.on("dialog", (d) => d.accept());
  op.on("dialog", (d) => d.accept());

  console.log("Operator sign-in:");
  await op.goto(`${BASE}/command`, { waitUntil: "networkidle2" });
  await waitAttr(op, '[data-testid="talk-link"]', "data-access", "signin");
  const distinct = await op.evaluate(() => ({
    talk: document.querySelector('[data-testid="talk-link"]').textContent,
    ask: document.querySelector('[data-testid="ask-aegis-panel"]')?.textContent ?? "",
  }));
  if (!/two-way call/.test(distinct.talk) || /two-way/.test(distinct.ask)) fail("Talk not clearly distinguished from Ask AEGIS");
  ok("Talk panel shows 'Talk link sign-in required'; labelled '📞 Responder talk link · two-way call · not Ask AEGIS'");
  await op.type('[data-testid="talk-secret"]', "definitely-not-the-secret");
  await op.click('[data-testid="talk-signin-submit"]');
  await op.waitForFunction(() => /sign-in required/.test(document.querySelector('[data-testid="talk-notice"]')?.textContent ?? ""), { timeout: 10000 });
  await waitAttr(op, '[data-testid="talk-link"]', "data-access", "signin");
  ok("wrong secret → rejected, asked again");
  await op.type('[data-testid="talk-secret"]', SECRET);
  await op.click('[data-testid="talk-signin-submit"]');
  await waitAttr(op, '[data-testid="talk-link"]', "data-online", "false");
  const stored = await op.evaluate(() => ({ s: !!sessionStorage.getItem("aegis.talk.operator"), l: Object.keys(localStorage).some((k) => k.includes("talk")) }));
  if (!stored.s || stored.l) fail(`secret storage wrong: ${JSON.stringify(stored)}`);
  const leaked = await op.evaluate((s) => document.documentElement.outerHTML.includes(s), SECRET);
  if (leaked) fail("operator secret rendered into the page");
  ok("operator secret accepted; kept in sessionStorage only (not localStorage, not in the DOM); presence: phone not connected");

  console.log("Phone link (enroll):");
  await op.click('[data-testid="talk-enroll"]');
  await op.waitForSelector('[data-testid="talk-phone-url"]');
  const link = await op.$eval('[data-testid="talk-phone-url"]', (e) => e.textContent.trim());
  if (!/\/responder\?id=responder-001#token=rt1\./.test(link)) fail(`unexpected phone link ${link}`);
  const phoneToken = decodeURIComponent(link.split("#token=")[1]);
  ok(`phone link minted: ${link.replace(/#token=.*/, "#token=rt1.…")} (token redacted here)`);
  if ((await code("POST", "channels/responder-002/heartbeat", phoneToken)) !== 403) fail("phone token worked on another responder's channel");
  if ((await code("GET", "channels/responder-001", phoneToken)) !== 403) fail("phone token can read the operator view");
  if ((await code("POST", "channels/responder-001/offer", phoneToken)) !== 403) fail("phone token can start calls");
  if ((await code("POST", "channels/responder-001/heartbeat", phoneToken)) !== 200) fail("phone token rejected on its own channel");
  ok("phone token: own channel heartbeat 200; other responder 403; operator routes (status, offer) 403 — no cross-responder access");

  console.log("Phone joins:");
  await ph.goto(link, { waitUntil: "networkidle2" });
  await ph.waitForSelector('[data-testid="phone-join"]');
  const after = await ph.evaluate(() => ({ hash: location.hash, stored: !!sessionStorage.getItem("aegis.talk.phone.responder-001") }));
  if (after.hash.includes("token") || !after.stored) fail(`token not moved out of the URL: ${JSON.stringify(after)}`);
  ok("phone page stored the token for responder-001 and removed it from the address bar");
  await ph.click('[data-testid="phone-join"]');
  await waitAttr(ph, '[data-testid="phone-status"]', "data-phase", "waiting");
  await waitAttr(op, '[data-testid="talk-link"]', "data-online", "true");
  if (await op.$('[data-testid="talk-phone-link"]')) fail("phone link still shown after the phone joined");
  ok("phone joined; operator sees 'phone ready' and the link (a credential) is hidden");

  console.log("Call:");
  await op.click('[data-testid="talk-start"]');
  await op.waitForFunction(() => /Connecting|Ringing|CONNECTED/.test(document.querySelector('[data-testid="talk-status"]')?.textContent ?? ""), { timeout: 5000 });
  await waitAttr(op, '[data-testid="talk-link"]', "data-phase", "live", 20000);
  await waitAttr(ph, '[data-testid="phone-status"]', "data-phase", "talking", 20000);
  await sleep(2500);
  const [a1, b1] = [await audioBytes(op), await audioBytes(ph)];
  if (!(a1.rx > 1000 && a1.tx > 1000 && b1.rx > 1000 && b1.tx > 1000)) fail(`audio not flowing both ways: op ${JSON.stringify(a1)} phone ${JSON.stringify(b1)}`);
  ok(`connected both sides; audio both ways (operator rx ${a1.rx} B / tx ${a1.tx} B, phone rx ${b1.rx} B / tx ${b1.tx} B)`);

  await op.click('[data-testid="talk-mute"]');
  await ph.click('[data-testid="phone-mute"]');
  const mutedOk = await Promise.all([op, ph].map((p) => p.evaluate(() => window.__streams.at(-1).getAudioTracks().every((t) => !t.enabled))));
  if (!mutedOk.every(Boolean)) fail("mute did not disable the microphone track");
  if (!/MUTED/.test(await op.$eval('[data-testid="talk-status"]', (e) => e.textContent))) fail("operator muted state not shown");
  if (!/muted/.test(await ph.$eval('[data-testid="phone-status"]', (e) => e.textContent))) fail("phone muted state not shown");
  await op.click('[data-testid="talk-mute"]');
  await ph.click('[data-testid="phone-mute"]');
  ok("both sides can mute (mic track disabled, 'muted' shown) and unmute");

  await op.click('[data-testid="talk-end"]');
  await waitAttr(ph, '[data-testid="phone-status"]', "data-phase", "waiting", 10000);
  await sleep(300);
  if ((await liveMicTracks(op)) || (await openPcs(op))) fail("operator mic/peer connection not released after End");
  ok("operator End → phone back to waiting; operator mic released and peer connection closed");

  await op.click('[data-testid="talk-start"]');
  await waitAttr(op, '[data-testid="talk-link"]', "data-phase", "live", 20000);
  await ph.click('[data-testid="phone-leave"]');
  await waitAttr(op, '[data-testid="talk-link"]', "data-phase", "idle", 10000);
  const note = await op.$eval('[data-testid="talk-notice"]', (e) => e.textContent);
  await sleep(300);
  if ((await liveMicTracks(ph)) || (await openPcs(ph))) fail("phone mic/peer connection not released after Leave");
  ok(`phone Leave ends the call for the operator ("${note.trim()}"); phone mic released`);

  console.log("Interrupted session:");
  await ph.click('[data-testid="phone-join"]');
  await waitAttr(op, '[data-testid="talk-link"]', "data-online", "true");
  await op.click('[data-testid="talk-start"]');
  await waitAttr(op, '[data-testid="talk-link"]', "data-phase", "live", 20000);
  const t0 = Date.now();
  await ph.close(); // phone tab closed abruptly mid-call
  await waitAttr(op, '[data-testid="talk-link"]', "data-phase", "idle", 20000);
  await sleep(300);
  if ((await liveMicTracks(op)) || (await openPcs(op))) fail("operator not cleaned up after the phone vanished");
  ok(`phone closed mid-call → operator call ended in ${((Date.now() - t0) / 1000).toFixed(1)} s, mic released`);

  console.log("Revoke:");
  const ph2 = await phCtx.newPage();
  await ph2.evaluateOnNewDocument(instrument);
  watchTalk(ph2, "phone-2");
  await ph2.setViewport({ width: 390, height: 844, isMobile: true, hasTouch: true });
  // A new tab has no token (sessionStorage is per tab): without the link it cannot join…
  await ph2.goto(`${BASE}/responder?id=responder-001`, { waitUntil: "networkidle2" });
  if (!(await ph2.$('[data-testid="phone-needs-link"]')) || !(await ph2.$eval('[data-testid="phone-join"]', (b) => b.disabled))) fail("phone without a link should not be able to join");
  ok("a phone tab without the link can't join ('Ask the operator for your phone link')");
  await waitAttr(op, '[data-testid="talk-link"]', "data-online", "false", 20000);

  // Orphaned call: an operator tab posts a real offer, then vanishes without hanging up.
  const orphan = await opCtx.newPage();
  await orphan.goto(`${BASE}/responder`, { waitUntil: "domcontentloaded" }); // any same-origin page
  const posted = await orphan.evaluate(async (secret) => {
    const pc = new RTCPeerConnection();
    const mic = await navigator.mediaDevices.getUserMedia({ audio: true });
    mic.getTracks().forEach((t) => pc.addTrack(t, mic));
    await pc.setLocalDescription(await pc.createOffer());
    await new Promise((r) => setTimeout(r, 1500));
    const res = await fetch("/aegis-api/api/v1/simulations/talk/channels/responder-001/offer", {
      method: "POST",
      headers: { "Content-Type": "application/json", Authorization: `Bearer ${secret}` },
      body: JSON.stringify({ sdp: pc.localDescription.sdp }),
    });
    return res.status;
  }, SECRET);
  if (posted !== 200) fail(`orphan offer not posted (${posted})`);
  await orphan.close();

  // Opening the link in the already-open tab only changes the #fragment (no reload): it must still sign in.
  await ph2.goto(link, { waitUntil: "networkidle2" });
  await ph2.waitForFunction(() => !document.querySelector('[data-testid="phone-join"]')?.disabled, { timeout: 5000 });
  if ((await ph2.evaluate(() => location.hash)).includes("token")) fail("token left in the address bar after hashchange");
  ok("link opened in an already-open phone tab (fragment-only change) still signs the phone in and cleans the URL");
  await ph2.click('[data-testid="phone-join"]');
  const tStale = Date.now();
  await ph2.waitForFunction(() => /didn't connect|couldn't connect/.test(document.querySelector('[data-testid="phone-info"]')?.textContent ?? ""), { timeout: 30000 }).catch(async () => {
    fail(`phone never gave up on the orphaned call: ${await describe(ph2)}`);
  });
  await waitAttr(ph2, '[data-testid="phone-status"]', "data-phase", "waiting", 5000);
  ok(`orphaned ringing call (operator gone) → phone gives up after ${((Date.now() - tStale) / 1000).toFixed(0)} s, hangs it up, stays connected`);
  await waitAttr(op, '[data-testid="talk-link"]', "data-online", "true");
  await op.click('[data-testid="talk-start"]');
  await waitAttr(op, '[data-testid="talk-link"]', "data-phase", "live", 20000);
  await waitAttr(ph2, '[data-testid="phone-status"]', "data-phase", "talking", 20000);
  await op.click('[data-testid="talk-end"]');
  await waitAttr(ph2, '[data-testid="phone-status"]', "data-phase", "waiting", 10000);
  ok("…and a real operator call right after connects normally (stale call not answered again)");
  await op.click('[data-testid="talk-revoke"]');
  await ph2.waitForFunction(() => /expired or was revoked/.test(document.querySelector('[data-testid="phone-error"]')?.textContent ?? ""), { timeout: 10000 });
  await sleep(300);
  if (await liveMicTracks(ph2)) fail("phone mic still live after revoke");
  if ((await code("POST", "channels/responder-001/heartbeat", phoneToken)) !== 401) fail("revoked phone token still accepted");
  ok("operator 'sign out phone' → phone shows 'This link has expired or was revoked…', stops and releases the mic; token now 401");

  console.log("Operator session + other features:");
  await op.reload({ waitUntil: "networkidle2" });
  await waitAttr(op, '[data-testid="talk-link"]', "data-access", "ok");
  const fresh = await (await browser.createBrowserContext()).newPage();
  const freshTalk = [];
  fresh.on("request", (r) => r.url().includes("/simulations/talk/") && freshTalk.push(r.url()));
  await fresh.goto(`${BASE}/command`, { waitUntil: "networkidle2" });
  await waitAttr(fresh, '[data-testid="talk-link"]', "data-access", "signin");
  await sleep(3500);
  if (freshTalk.length) fail(`signed-out dashboard sent ${freshTalk.length} talk request(s) without credentials`);
  ok("reload keeps this tab signed in; a new browser session must sign in again and sends no talk request until it does");
  const comms = await op.evaluate(() => ({
    ptt: [...document.querySelectorAll("button")].some((b) => /hold to talk|talk \(space\)|push/i.test(b.textContent + (b.title ?? ""))),
    quick: ["Stop", "Evacuate now"].every((l) => [...document.querySelectorAll("button")].some((b) => b.textContent.trim() === l)),
  }));
  if (!comms.quick) fail(`Comms quick commands missing: ${JSON.stringify(comms)}`);
  ok(`Comms quick commands still present${comms.ptt ? " (push-to-talk present)" : ""}`);

  const p503 = await (await browser.createBrowserContext()).newPage();
  await p503.evaluateOnNewDocument(() => sessionStorage.setItem("aegis.talk.operator", "some-operator-secret-value"));
  await p503.setRequestInterception(true);
  p503.on("request", (r) =>
    r.url().includes("/simulations/talk/") ? r.respond({ status: 503, contentType: "application/json", body: '{"detail":"x"}' }) : r.continue(),
  );
  await p503.goto(`${BASE}/command`, { waitUntil: "networkidle2" });
  await waitAttr(p503, '[data-testid="talk-link"]', "data-access", "unconfigured");
  ok("host without a talk secret (503) → 'Talk link is not configured on this host' (no sign-in loop)");

  // Every talk request the operator and phone pages sent carried a Bearer credential, including /ice-servers.
  const missing = talkRequests.filter((r) => !r.bearer);
  const ice = talkRequests.filter((r) => r.path === "/ice-servers");
  const byWho = Object.entries(talkRequests.reduce((a, r) => ((a[r.who] = (a[r.who] ?? 0) + 1), a), {})).map(([w, n]) => `${w} ${n}`).join(", ");
  if (missing.length) fail(`talk requests without Bearer: ${JSON.stringify(missing.slice(0, 5))}`);
  if (ice.length < 2 || !ice.some((r) => r.who.startsWith("phone")) || !ice.some((r) => r.who === "operator")) fail(`expected /ice-servers from both sides: ${JSON.stringify(ice)}`);
  const routes = [...new Set(talkRequests.map((r) => `${r.method} ${r.path.replace(/responder-\d+/, "{id}")}`))].sort();
  ok(`all ${talkRequests.length} talk requests carried Bearer (${byWho}); incl. /ice-servers ×${ice.length} from operator and phone; routes: ${routes.join(", ")}`);

  if (errors.length) fail(`console errors: ${errors.join(" | ")}`);
  ok("no unexpected console errors");
  console.log("\nTalk link e2e passed (SIMULATION ONLY).");
} catch (e) {
  console.error(`\nTalk link e2e FAILED: ${e.message}`);
  process.exitCode = 1;
} finally {
  await browser.close();
}
