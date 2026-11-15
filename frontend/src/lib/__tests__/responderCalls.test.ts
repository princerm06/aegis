import { describe, expect, it } from "vitest";
import { NOT_SET_UP, callAvailability, callClock, callErrorText, fetchCallStatus, fetchCallToken, type CallStatus } from "../responderCalls";

const json = (status: number, body: unknown) => async () => new Response(JSON.stringify(body), { status });

const READY: CallStatus = {
  configured: true,
  responders: [{ responder_id: "responder-001", number_hint: "•••• 4567" }],
  note: "Calls ring the responder's real phone.",
};

describe("fetchCallStatus", () => {
  it("reads the backend's callable responders", async () => {
    expect(await fetchCallStatus(json(200, READY))).toEqual(READY);
  });

  it("treats an older backend, an error or an unreachable backend as not set up", async () => {
    expect(await fetchCallStatus(json(404, { detail: "Not Found" }))).toEqual(NOT_SET_UP);
    expect(await fetchCallStatus(json(200, { unexpected: true }))).toEqual(NOT_SET_UP);
    expect(
      await fetchCallStatus(async () => {
        throw new TypeError("Failed to fetch");
      }),
    ).toEqual(NOT_SET_UP);
  });
});

describe("fetchCallToken", () => {
  it("posts for one responder and returns the token", async () => {
    let seen = "";
    const token = await fetchCallToken("responder 001", async (url, init) => {
      seen = `${init?.method} ${url}`;
      return new Response(JSON.stringify({ responder_id: "responder 001", number_hint: "•••• 4567", token: "a.b.c", expires_in_s: 600 }));
    });
    expect(seen).toBe("POST /aegis-api/api/v1/simulations/responder-calls/responder%20001/token");
    expect(token.token).toBe("a.b.c");
  });

  it("surfaces the backend's reason when calls are not available", async () => {
    await expect(fetchCallToken("responder-001", json(503, { detail: "Phone calls are not set up on this host" }))).rejects.toThrow(
      "Phone calls are not set up on this host",
    );
    await expect(fetchCallToken("responder-001", async () => new Response("oops", { status: 500 }))).rejects.toThrow("(500)");
  });
});

describe("callAvailability", () => {
  it("offers the call with the masked number only for a configured responder", () => {
    expect(callAvailability(READY, "responder-001", "Alpha")).toEqual({ kind: "ready", hint: "•••• 4567" });
    expect(callAvailability(READY, "responder-002", "Bravo")).toEqual({ kind: "no-number", text: "No phone number set for Bravo." });
    expect(callAvailability(NOT_SET_UP, "responder-001", "Alpha").kind).toBe("unconfigured");
  });
});

describe("callErrorText", () => {
  it("turns Twilio error codes into what the operator should do", () => {
    expect(callErrorText({ code: 20101, message: "AccessTokenInvalid (20101)" }, "Alpha")).toMatch(/TWILIO_\* values in backend\/\.env/);
    expect(callErrorText({ code: 31402 }, "Alpha")).toMatch(/Microphone permission denied/);
    expect(callErrorText(new DOMException("denied", "NotAllowedError"), "Alpha")).toMatch(/Microphone permission denied/);
    expect(callErrorText({ code: 31486 }, "Alpha")).toBe("Alpha's phone is busy.");
    expect(callErrorText({ code: 13224 }, "Alpha")).toMatch(/Verified Caller ID/);
    expect(callErrorText({ code: 31005 }, "Alpha")).toMatch(/internet/);
  });

  it("falls back to the message, or a generic line", () => {
    expect(callErrorText(new Error("Backend unreachable"), "Alpha")).toBe("Backend unreachable");
    expect(callErrorText(undefined, "Alpha")).toBe("Call failed.");
  });
});

it("formats call time as m:ss", () => {
  expect([callClock(7), callClock(83.9), callClock(725), callClock(-3)]).toEqual(["0:07", "1:23", "12:05", "0:00"]);
});
