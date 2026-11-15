import { describe, expect, it } from "vitest";
import { assessMediaSupport, friendlyMediaError, mediaBlockerCopy, type MediaEnvironment } from "../mediaCapabilities";

const gum = () => {};
const list = () => {};

describe("assessMediaSupport", () => {
  it("blocks insecure contexts before touching any media API", () => {
    const env: MediaEnvironment = { secureContext: false, mediaDevices: { getUserMedia: gum, enumerateDevices: list } };
    expect(assessMediaSupport(env)).toEqual({ kind: "insecure-context" });
  });

  it("reports insecure context when mediaDevices is hidden (plain LAN HTTP)", () => {
    expect(assessMediaSupport({ secureContext: false, mediaDevices: undefined })).toEqual({ kind: "insecure-context" });
    expect(assessMediaSupport({ secureContext: false, mediaDevices: null })).toEqual({ kind: "insecure-context" });
  });

  it("reports unavailable when mediaDevices is missing on a secure origin", () => {
    expect(assessMediaSupport({ secureContext: true, mediaDevices: undefined })).toEqual({ kind: "unavailable" });
  });

  it("reports partial implementations", () => {
    expect(assessMediaSupport({ secureContext: true, mediaDevices: {} })).toEqual({ kind: "no-capture" });
    expect(assessMediaSupport({ secureContext: true, mediaDevices: { getUserMedia: gum } })).toEqual({ kind: "no-list" });
  });

  it("allows fully supported environments", () => {
    expect(assessMediaSupport({ secureContext: true, mediaDevices: { getUserMedia: gum, enumerateDevices: list } })).toEqual({
      kind: "none",
    });
  });
});

describe("mediaBlockerCopy", () => {
  it("explains the secure-context requirement without raw API names as the headline", () => {
    const copy = mediaBlockerCopy("insecure-context");
    expect(copy.title).toMatch(/secure|connection|localhost|https/i);
    expect(copy.body).toMatch(/localhost/i);
    expect(copy.body).toMatch(/https/i);
    expect(copy.body).toMatch(/stream url/i);
  });
});

describe("friendlyMediaError", () => {
  it("maps permission denial to guidance", () => {
    const err = new DOMException("Permission denied", "NotAllowedError");
    expect(friendlyMediaError(err)).toMatch(/permission denied/i);
  });

  it("maps missing/busy devices to guidance", () => {
    expect(friendlyMediaError(new DOMException("x", "NotFoundError"))).toMatch(/not found/i);
    expect(friendlyMediaError(new DOMException("x", "OverconstrainedError"))).toMatch(/not found/i);
    expect(friendlyMediaError(new DOMException("x", "NotReadableError"))).toMatch(/busy/i);
  });

  it("maps SecurityError to the insecure-context explanation", () => {
    expect(friendlyMediaError(new DOMException("x", "SecurityError"))).toMatch(/localhost|https/i);
  });

  it("never surfaces the raw undefined-getUserMedia TypeError", () => {
    const raw = new TypeError("Cannot read properties of undefined (reading 'getUserMedia')");
    const friendly = friendlyMediaError(raw);
    expect(friendly).not.toMatch(/cannot read properties/i);
    expect(friendly).not.toMatch(/undefined/i);
    expect(friendly.length).toBeGreaterThan(0);
  });

  it("keeps unknown errors to one short line", () => {
    const friendly = friendlyMediaError(new Error("weird\nmultiline\nfailure"));
    expect(friendly).not.toMatch(/\n/);
    expect(friendly.length).toBeLessThanOrEqual(200);
  });
});
