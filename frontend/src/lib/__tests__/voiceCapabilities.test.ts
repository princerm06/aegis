import { describe, expect, it } from "vitest";
import { formatElapsed, micErrorCopy, micStatus, speechOutput } from "../voiceCapabilities";
import type { MediaEnvironment } from "../mediaCapabilities";

const gum = () => {};
const secure: MediaEnvironment = { secureContext: true, mediaDevices: { getUserMedia: gum, enumerateDevices: gum } };
const lanHttp: MediaEnvironment = { secureContext: false, mediaDevices: null };

describe("micStatus", () => {
  it("is ready on a secure page with speech configured", () => {
    expect(micStatus(secure, true, "full_speech").kind).toBe("ready");
  });

  it("explains the plain-HTTP LAN case with the localhost/HTTPS fix, whatever the service mode", () => {
    for (const mode of ["full_speech", "text_only", "offline"] as const) {
      const s = micStatus(lanHttp, true, mode);
      expect(s.kind).toBe("insecure");
      expect(s.help).toMatch(/localhost/);
      expect(s.help).toMatch(/HTTPS/);
      expect(s.help).toMatch(/Typed questions work/);
    }
  });

  it("reports browsers that cannot record", () => {
    expect(micStatus({ secureContext: true, mediaDevices: null }, true, "full_speech").kind).toBe("unsupported");
    expect(micStatus({ secureContext: true, mediaDevices: {} }, true, "full_speech").kind).toBe("unsupported");
    expect(micStatus(secure, false, "full_speech").kind).toBe("unsupported");
  });

  it("does not require camera listing to record audio", () => {
    expect(micStatus({ secureContext: true, mediaDevices: { getUserMedia: gum } }, true, "full_speech").kind).toBe("ready");
  });

  it("blocks recording up front when speech-to-text is not configured (instead of failing after send)", () => {
    const s = micStatus(secure, true, "text_only");
    expect(s.kind).toBe("speech-off");
    expect(s.help).toMatch(/voice\/\.env/);
    expect(s.help).toMatch(/never in the browser/);
  });

  it("reports an offline voice service", () => {
    expect(micStatus(secure, true, "offline").kind).toBe("service-offline");
  });
});

describe("micErrorCopy", () => {
  it("gives recovery steps for permission denial", () => {
    expect(micErrorCopy(new DOMException("denied", "NotAllowedError"))).toMatch(/allow Microphone/);
  });
  it("handles missing and busy microphones", () => {
    expect(micErrorCopy(new DOMException("x", "NotFoundError"))).toMatch(/No microphone/);
    expect(micErrorCopy(new DOMException("x", "NotReadableError"))).toMatch(/busy/);
  });
  it("never surfaces a raw TypeError", () => {
    const msg = micErrorCopy(new TypeError("Cannot read properties of undefined (reading 'getUserMedia')"));
    expect(msg).not.toMatch(/Cannot read/);
  });
});

describe("speechOutput", () => {
  it("is unavailable without text-to-speech on the host", () => {
    expect(speechOutput("text_only", false)).toMatchObject({ available: false, label: "🔊 n/a" });
    expect(speechOutput("offline", false).available).toBe(false);
  });
  it("reflects mute when available", () => {
    expect(speechOutput("full_speech", false).label).toBe("🔊 On");
    expect(speechOutput("full_speech", true).label).toBe("🔇 Muted");
  });
});

it("formats recording time", () => {
  expect(formatElapsed(0)).toBe("0:00");
  expect(formatElapsed(3.7)).toBe("0:03");
  expect(formatElapsed(75)).toBe("1:15");
});
