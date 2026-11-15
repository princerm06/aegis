import assert from "node:assert/strict";
import { spawn } from "node:child_process";
import { once } from "node:events";
import { connect } from "node:net";
import test, { before, after } from "node:test";

let server;
let base;
before(async () => {
  server = spawn(process.execPath, [new URL("../scripts/serve.mjs", import.meta.url).pathname], {
    env: { ...process.env, PORT: "0" }, stdio: ["ignore", "pipe", "inherit"],
  });
  const [line] = await once(server.stdout, "data");
  base = line.toString().match(/http:\/\/127\.0\.0\.1:\d+/)[0];
});
after(() => server?.kill());

test("only landing and interactive demo application routes are available", async () => {
  for (const path of ["/", "/demo"]) assert.equal((await fetch(base + path)).status, 200);
  const html = await (await fetch(base)).text();
  assert.match(html, /Launch Interactive Command Center/);
  assert.match(html, /Try Live Simulation/);
  assert.doesNotMatch(html, /href="\/command|Checking simulation backend/);
});

test("operational routes and encoded/nested variants return real 404s", async () => {
  for (const path of ["/command", "/command/", "/command/tasks", "/command?tab=live", "/%63ommand", "/aegis-api", "/aegis-api/api/v1/simulations/demo/state", "/aegis-voice", "/aegis-voice/health", "/api/v1/scenarios/demo", "/api/health", "/health", "/ws", "/ws/demo", "/socket.io/", "/.env", "/backend/app/main.py", "/anything"]) {
    const response = await fetch(base + path, { redirect: "manual" });
    assert.equal(response.status, 404, path);
    assert.doesNotMatch(await response.text(), /Launch Interactive Command Center|Scene map/);
  }
});

test("no operational POST endpoints exist and hardware permissions are denied", async () => {
  for (const path of ["/command", "/aegis-api/api/v1/simulations/demo/reset", "/aegis-voice", "/api/dispatch"]) {
    assert.equal((await fetch(base + path, { method: "POST" })).status, 405, path);
  }
  const headers = (await fetch(base)).headers;
  assert.match(headers.get("permissions-policy"), /camera=\(\), microphone=\(\)/);
  assert.match(headers.get("content-security-policy"), /connect-src 'self'/);
});

test("WebSocket upgrades cannot connect", async () => {
  const url = new URL(base);
  for (const path of ["/ws", "/aegis-api/ws", "/aegis-voice/ws"]) {
    const socket = connect(Number(url.port), url.hostname);
    socket.setTimeout(3000, () => socket.destroy(new Error("WebSocket rejection timeout")));
    await once(socket, "connect");
    socket.write(`GET ${path} HTTP/1.1\r\nHost: ${url.host}\r\nConnection: Upgrade\r\nUpgrade: websocket\r\nSec-WebSocket-Version: 13\r\nSec-WebSocket-Key: dGhlIHNhbXBsZSBub25jZQ==\r\n\r\n`);
    const [data] = await once(socket, "data");
    assert.match(data.toString(), /^HTTP\/1\.1 404/);
    socket.destroy();
  }
});
