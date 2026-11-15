#!/usr/bin/env node
// TEST STUB ONLY — never used by the app. Stands in for ElevenLabs so the browser test can exercise
// Ask AEGIS's speech paths (record → transcript → grounded answer → spoken playback → Stop/Play/Mute)
// on a machine without an API key.
//
// - /health reports full_speech (speech configured).
// - Audio turns get a FIXED transcript (MOCK_TRANSCRIPT) instead of real speech-to-text.
// - Answers come from the REAL voice service (UPSTREAM, default :8010) so they stay grounded in live
//   simulation data; this stub only adds a silent WAV as the "spoken" audio.
// Run: UPSTREAM=http://127.0.0.1:8410 PORT=8420 node scripts/mock-voice-speech.mjs
import http from "node:http";

const UPSTREAM = process.env.UPSTREAM ?? "http://127.0.0.1:8010";
const PORT = Number(process.env.PORT ?? 8420);
const TRANSCRIPT = process.env.MOCK_TRANSCRIPT ?? "What's the current situation?";
const SECONDS = Number(process.env.MOCK_AUDIO_SECONDS ?? 3);

/** Silent 8 kHz mono 16-bit PCM WAV. */
function silentWav(seconds) {
  const rate = 8000;
  const samples = Math.round(rate * seconds);
  const buf = Buffer.alloc(44 + samples * 2);
  buf.write("RIFF", 0);
  buf.writeUInt32LE(36 + samples * 2, 4);
  buf.write("WAVEfmt ", 8);
  buf.writeUInt32LE(16, 16);
  buf.writeUInt16LE(1, 20);
  buf.writeUInt16LE(1, 22);
  buf.writeUInt32LE(rate, 24);
  buf.writeUInt32LE(rate * 2, 28);
  buf.writeUInt16LE(2, 32);
  buf.writeUInt16LE(16, 34);
  buf.write("data", 36);
  buf.writeUInt32LE(samples * 2, 40);
  return buf.toString("base64");
}
const AUDIO = silentWav(SECONDS);

const json = (res, code, body) => {
  res.writeHead(code, { "content-type": "application/json" });
  res.end(JSON.stringify(body));
};
const readBody = (req) =>
  new Promise((resolve) => {
    const chunks = [];
    req.on("data", (c) => chunks.push(c));
    req.on("end", () => resolve(Buffer.concat(chunks).toString("utf8")));
  });

async function upstreamQuery(body, text) {
  const r = await fetch(`${UPSTREAM}/api/v1/voice/query`, {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({ mode: "simulation", session_id: body.session_id, scenario_id: body.scenario_id, text, speak: false }),
  });
  return r.json();
}
const withSpeech = (turn, speak) => ({
  ...turn,
  delivery_mode: "full_speech",
  audio_base64: speak ? AUDIO : null,
  audio_mime_type: speak ? "audio/wav" : null,
  warnings: [],
  error_category: null,
});

http
  .createServer(async (req, res) => {
    try {
      if (req.method === "GET" && req.url === "/health") {
        const h = await (await fetch(`${UPSTREAM}/health`)).json();
        return json(res, 200, { ...h, speech_Configured: true, operational_mode: "full_speech", mock_speech: true });
      }
      if (req.method === "POST" && req.url === "/api/v1/voice/query") {
        const body = JSON.parse(await readBody(req));
        return json(res, 200, withSpeech(await upstreamQuery(body, body.text), body.speak));
      }
      if (req.method === "POST" && req.url === "/api/v1/voice/audio-turn") {
        const body = JSON.parse(await readBody(req));
        const turn = await upstreamQuery(body, TRANSCRIPT);
        return json(res, 200, withSpeech({ ...turn, user_transcript: TRANSCRIPT }, body.speak));
      }
      if (req.method === "POST" && /^\/api\/v1\/voice\/session\/[^/]+\/reset$/.test(req.url)) {
        const r = await fetch(`${UPSTREAM}${req.url}`, { method: "POST" });
        return json(res, r.status, await r.json());
      }
      json(res, 404, { detail: "not found" });
    } catch (e) {
      json(res, 502, { detail: `mock upstream error: ${e.message}` });
    }
  })
  .listen(PORT, "127.0.0.1", () => console.log(`mock speech stub on :${PORT} → ${UPSTREAM}`));
