import assert from "node:assert/strict";
import puppeteer from "puppeteer-core";

const base = process.env.BASE ?? "http://127.0.0.1:4173";
const browser = await puppeteer.launch({
  executablePath: process.env.CHROME_PATH ?? "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
  headless: true,
});
const context = await browser.createBrowserContext();
const page = await context.newPage();
const errors = [];
const requests = [];
let inspectRequests = true;
const hook = async (target) => {
  target.on("pageerror", (error) => errors.push(error.message));
  target.on("request", (request) => {
    const url = new URL(request.url());
    if (inspectRequests && (url.origin !== new URL(base).origin || /\/(?:command|aegis-api|aegis-voice|api|ws)(?:\/|$)/.test(url.pathname))) requests.push(url.href);
  });
  await target.evaluateOnNewDocument(() => {
    window.__operationalAttempts = [];
    window.WebSocket = class {
      constructor(url) {
        window.__operationalAttempts.push(String(url));
        throw new Error("Public showcase cannot connect WebSockets");
      }
    };
    window.EventSource = class {
      constructor(url) {
        window.__operationalAttempts.push(String(url));
        throw new Error("Public showcase cannot connect EventSource");
      }
    };
  });
};
const selector = (id) => `[data-testid="${id}"]`;
const click = async (id) => {
  const target = selector(id);
  await page.waitForSelector(target, { visible: true });
  await page.$eval(target, (element) => element.scrollIntoView({ block: "center", behavior: "instant" }));
  await page.click(target);
};
const countTasks = () => page.$$eval('[data-testid^="task-"]', (elements) => elements.length);
const disabled = (id) => page.$eval(selector(id), (element) => element.disabled);
const checkCta = async (id) => {
  const info = await page.$eval(selector(id), (element) => {
    const rect = element.getBoundingClientRect();
    return { top: rect.top, bottom: rect.bottom, left: rect.left, right: rect.right, href: element.getAttribute("href"), visible: getComputedStyle(element).display !== "none", height: innerHeight, width: innerWidth };
  });
  assert.equal(info.href, "/demo");
  assert.ok(info.visible && info.top >= 0 && info.bottom <= info.height && info.left >= 0 && info.right <= info.width, `${id} must be visible without scrolling: ${JSON.stringify(info)}`);
};
const selectPriority = (id, value) => page.select(selector(`priority-${id}`), value);
const confirm = async (id, priority) => {
  await click(`confirm-${id}`);
  await selectPriority(id, priority);
};
const checkSystemContext = async (target) => {
  const intro = await target.$eval(selector('system-context'), element => {
    const rect = element.getBoundingClientRect();
    return { text: element.innerText, top: rect.top, bottom: rect.bottom, height: innerHeight };
  });
  assert.match(intro.text, /computer vision, human assessments, mathematical optimization, neural intelligence, and physical responder hardware/);
  assert.match(intro.text, /actual cameras, backend optimization, trained models, and connected Arduino/);
  assert.match(intro.text, /isolated, browser-only/);
  assert.ok(intro.top >= 0 && intro.bottom <= intro.height, 'system introduction visible on direct entry');
  assert.equal(await target.$$eval('[data-testid^="observation-C"]', elements => elements.length), 4);
  assert.match(await target.$eval(selector('camera-intelligence'), element => element.innerText), /SYNTHETIC FIXTURES/);
  assert.match(await target.$eval(selector('observation-C2'), element => element.innerText), /Partially occluded[\s\S]*Not testable/);
  for (const id of ['full-system-details', 'recorded-cv-details', 'neural-context', 'research-details']) {
    const toggle = `${selector(id)} summary`;
    await target.$eval(toggle, element => element.scrollIntoView({ block: 'center', behavior: 'instant' }));
    await target.click(toggle);
    assert.equal(await target.$eval(selector(id), element => element.open), true);
    assert.equal(await target.evaluate(() => document.documentElement.scrollWidth <= innerWidth), true);
    await target.click(toggle);
    assert.equal(await target.$eval(selector(id), element => element.open), false);
  }
  const research = await target.$eval(selector('research-intelligence'), element => element.innerText);
  for (const value of ['377,606', '67,798', '78.39 seconds', '82.63 seconds']) assert.ok(research.includes(value));
  assert.match(research, /incident creation to first assignment/);
  assert.match(research, /rare severity-8[\s\S]*does not execute XGBoost/);
};

try {
  await hook(page);
  await page.setViewport({ width: 1440, height: 900 });
  await page.emulateMediaFeatures([{ name: "prefers-reduced-motion", value: "reduce" }]);
  await page.goto(base, { waitUntil: "networkidle0" });
  await checkCta("hero-demo");
  await checkCta("nav-demo");
  assert.equal(await page.$eval("header", (element) => getComputedStyle(element).position), "fixed");
  assert.equal(await page.$eval("h1", (element) => getComputedStyle(element).fontSize), "66px");
  const destinations = await page.$$eval('a[href="/demo"]', (links) => links.map((link) => link.textContent.trim()));
  assert.ok(destinations.includes("Try the Simulation"));
  assert.ok(destinations.includes("Try Interactive Demo"));
  await page.click('button[aria-label="Switch to dark mode"]');
  await page.reload({ waitUntil: "networkidle0" });
  assert.equal(await page.evaluate(() => document.documentElement.dataset.theme), "dark");
  await page.click('button[aria-label="Switch to light mode"]');
  await page.$eval("#stage-tab-optimize", (element) => element.scrollIntoView({ block: "center", behavior: "instant" }));
  await page.click("#stage-tab-optimize");
  await page.waitForSelector('#stage-tab-optimize[aria-selected="true"]');
  await page.keyboard.press("ArrowRight");
  await page.waitForSelector('#stage-tab-approve[aria-selected="true"]');
  await page.$eval('g.lp-entity[aria-label^="C-001"]', (element) => element.focus());
  await page.waitForSelector('g.lp-entity[aria-label^="C-001"][data-on="true"]');
  console.log("PASS desktop landing: initial-viewport hero and persistent nav CTAs, CSS, preview, theme persistence, physical and keyboard workflow controls, scene inspector");

  await page.setViewport({ width: 390, height: 844 });
  await page.evaluate(() => window.scrollTo({ top: 0, behavior: "instant" }));
  await checkCta("hero-demo");
  await checkCta("nav-demo");
  await page.click('button[aria-label="Open menu"]');
  await page.waitForSelector("#mobile-nav");
  await page.click('#mobile-nav a[href="#technology"]');
  await page.waitForSelector("#mobile-nav", { hidden: true });
  assert.equal(new URL(page.url()).hash, "#technology");
  await page.$eval('button[aria-controls="stage-m-optimize"]', (element) => element.scrollIntoView({ block: "center", behavior: "instant" }));
  await page.click('button[aria-controls="stage-m-optimize"]');
  await page.waitForSelector('button[aria-controls="stage-m-optimize"][aria-expanded="true"]');
  assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), true);
  await checkCta("nav-demo");
  await page.emulateMediaFeatures([{ name: "prefers-reduced-motion", value: "no-preference" }]);
  await page.waitForFunction(() => getComputedStyle(document.querySelector(".lp-scan")).animationName !== "none");
  await page.setViewport({ width: 360, height: 800 });
  assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), true);
  await checkCta("nav-demo");
  await page.emulateMediaFeatures([{ name: "prefers-reduced-motion", value: "reduce" }]);
  console.log("PASS mobile landing: visible demo outside menu at 390px/360px, fixed CTA while scrolling, navigation, accordion, animations, no overflow");

  await page.setViewport({ width: 1440, height: 900 });
  await click("nav-demo");
  await page.waitForSelector(selector("recommend"));
  assert.equal(new URL(page.url()).pathname, "/demo");
  assert.match(await page.$eval("body", (element) => element.innerText), /Interactive Simulation — No Real Emergency Dispatch/);
  assert.match(await page.$eval("body", (element) => element.innerText), /Not live OR-Tools/);
  await checkSystemContext(page);
  assert.equal(await countTasks(), 0);
  await click("recommend");
  assert.equal(await disabled("approve"), true);
  await confirm("C1", "first");
  assert.equal(await page.$eval(selector('observation-confirmation-C1'), element => element.innerText), 'Human-confirmed in sandbox');
  await confirm("C2", "next");
  await click("recommend");
  assert.equal(await disabled("dispatch"), true);
  await click("approve");
  assert.equal(await countTasks(), 0);
  assert.equal(await disabled("dispatch"), false);
  await selectPriority("C1", "later");
  assert.equal(await page.$(selector("dispatch")), null);
  await click("recommend");
  assert.equal(await disabled("dispatch"), true);
  await click("approve");
  await click("dispatch");
  assert.equal(await countTasks(), 2);
  const assignments = await page.$$eval('[data-testid^="task-"] h3', (elements) => elements.map((element) => element.textContent.trim()));
  assert.equal(new Set(assignments).size, 2);
  assert.ok(assignments.some((text) => /alpha/i.test(text)) && assignments.some((text) => /bravo/i.test(text)));

  await click("recommend");
  assert.equal(await disabled("approve"), true);
  assert.equal(await page.$$eval('[aria-label^="Synthetic incident grid"] polyline', elements => elements.length), 2);

  await click("corridor-central");
  await page.waitForFunction(() => document.body.innerText.includes("Route invalidated."));
  assert.equal(await page.$(selector("dispatch")), null);
  await click("recommend");
  assert.equal(await disabled("dispatch"), true);
  await click("approve");
  await click("dispatch");
  assert.ok(await page.$('[data-testid^="history-"]'));
  const taskIds = await page.$$eval('[data-testid^="task-"]', (elements) => elements.map((element) => element.dataset.testid.slice(5)));
  for (const taskId of taskIds) {
    assert.equal(await disabled(`ack-${taskId}`), true);
    assert.equal(await disabled(`done-${taskId}`), true);
    await click(`receipt-${taskId}`);
    await click(`ack-${taskId}`);
    await click(`done-${taskId}`);
  }
  assert.equal(await countTasks(), 0);
  assert.match(await page.$eval("body", (element) => element.innerText), /DONE · task closed/);
  console.log("PASS sandbox desktop: confirmation, human priorities, distinct Alpha/Bravo tasks, explicit revision approval/dispatch, invalidation/replan/new authorization, virtual receipt/ACK/DONE, preserved history");

  const otherContext = await browser.createBrowserContext();
  const otherPage = await otherContext.newPage();
  await hook(otherPage);
  await otherPage.goto(base + "/demo", { waitUntil: "networkidle0" });
  await otherPage.setViewport({ width: 390, height: 844 });
  await checkSystemContext(otherPage);
  await otherPage.setViewport({ width: 360, height: 800 });
  await otherPage.evaluate(() => window.scrollTo({ top: 0, behavior: 'instant' }));
  await checkSystemContext(otherPage);
  console.log('PASS system context: direct desktop/mobile entry, synthetic/recorded camera distinction, expandable architecture/neural/research context, verified EMS metrics, no overflow');
  assert.equal(await otherPage.$eval(selector("confirm-C1"), (element) => element.getAttribute("aria-pressed")), "false");
  assert.equal(await otherPage.$$eval('[data-testid^="history-"]', (elements) => elements.length), 0);
  await otherContext.close();

  await click("reset");
  assert.equal(await page.$eval(selector('observation-confirmation-C1'), element => element.innerText), 'Awaiting explicit human confirmation');
  assert.equal(await countTasks(), 0);
  assert.equal(await page.$$eval('[data-testid^="history-"]', (elements) => elements.length), 0);
  assert.equal(await page.$eval(selector("priority-C1"), (element) => element.value), "unassessed");
  await page.setViewport({ width: 390, height: 844 });
  await page.setOfflineMode(true);
  await confirm("C1", "first");
  await confirm("C2", "next");
  await click("recommend");
  await click("approve");
  await click("dispatch");
  assert.equal(await countTasks(), 2);
  assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), true);
  await click("reset");
  assert.equal(await countTasks(), 0);
  await page.setOfflineMode(false);
  console.log("PASS sandbox mobile: full offline confirm/assess/optimize/approve/dispatch/reset, responsive layout; independent browser sessions share no scenario state");

  await new Promise((resolve) => setTimeout(resolve, 16000));
  assert.deepEqual(await page.evaluate(() => window.__operationalAttempts), []);
  assert.deepEqual(requests, []);
  assert.deepEqual(errors, []);
  console.log("PASS isolation: zero external/operational requests, sockets, EventSource, hydration/runtime errors, including former backend polling interval");
  inspectRequests = false;
  for (const path of ["/command", "/aegis-api/api/v1/simulations/demo/state", "/aegis-voice", "/api/health", "/ws", "/random-route"]) {
    const response = await page.goto(base + path, { waitUntil: "domcontentloaded" });
    assert.equal(response.status(), 404, path);
    assert.match(await page.$eval("body", (element) => element.innerText), /could not be found/);
  }
  console.log("PASS direct browser navigation: all operational/unknown routes 404, no dashboard or SPA fallback");
} finally {
  await browser.close();
}
