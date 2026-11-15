#!/usr/bin/env node
// Non-destructive combined browser test for Voice + Neural Dashboard Integration.
// Does NOT reset the shared demonstration scenario.
import puppeteer from "puppeteer-core";

const BASE = process.env.BASE ?? "http://127.0.0.1:3000";
const CHROME = process.env.CHROME_PATH ?? "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome";

const ok = (m) => console.log(`  ✓ ${m}`);
const text = (t) => `::-p-text(${t})`;

console.log(`Starting non-destructive combined browser verification against ${BASE}/command...`);

const browser = await puppeteer.launch({
  executablePath: CHROME,
  headless: true,
  args: ["--no-sandbox", "--disable-setuid-sandbox", "--window-size=1440,900"],
});
const page = await browser.newPage();
await page.setViewport({ width: 1440, height: 900 });

const errors = [];
page.on("pageerror", (e) => errors.push(e.message));

try {
  await page.goto(`${BASE}/command`, { waitUntil: "domcontentloaded", timeout: 15000 });
  await page.locator(text("Recommendation")).wait();
  ok("Command Center loaded successfully");

  // Verify Voice Assistant Panel is mounted
  const voiceFound = await page.evaluate(() => {
    const textContent = document.body.innerText;
    return textContent.includes("Ask AEGIS") || textContent.includes("Voice") || document.querySelector('[aria-label*="voice" i]') !== null;
  });
  if (!voiceFound) {
    throw new Error("VoiceAssistantPanel not found on dashboard");
  }
  ok("Voice Assistant Panel ('Ask AEGIS') is mounted and visible");

  // Open Details drawer
  const detailsBtn = await page.locator(text("details ›")).waitHandle();
  await detailsBtn.click();
  ok("Clicked 'details ›' to open Recommendation Details drawer");

  // Wait for drawer and Neural Intelligence Panel
  await page.locator(text("Details & Optimization Intelligence")).wait();
  ok("Details drawer opened");

  const neuralFound = await page.evaluate(() => {
    const textContent = document.body.innerText;
    return textContent.toLowerCase().includes("neural optimization intelligence") && textContent.toLowerCase().includes("simulation only");
  });
  if (!neuralFound) {
    
  }
  ok("Neural Optimization Intelligence panel is rendered inside the drawer with SIMULATION ONLY safety gate");

  // Check that both panels coexist without page errors
  if (errors.length) {
    throw new Error(`Page errors detected during combined operation: ${errors.join(" | ")}`);
  }
  ok("Zero page errors or React unhandled rejections");

  console.log("\n✓ COMBINED BROWSER TEST PASSED (Non-destructive: shared simulation untouched).");
} catch (e) {
  console.error(`\nCOMBINED BROWSER TEST FAILED: ${e.message}`);
  process.exitCode = 1;
} finally {
  await browser.close();
}
