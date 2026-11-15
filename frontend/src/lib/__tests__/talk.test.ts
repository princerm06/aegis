import { describe, expect, it, vi } from "vitest";
import { ICE_SERVERS, fetchRtcConfig, operatorStep, phonePageUrl, responderStep, talkApi, talkErrorText, waitForIce, type ChannelView, type TalkSession } from "../talk";

const session = (over: Partial<TalkSession> = {}): TalkSession => ({
  session_id: "s1",
  state: "ringing",
  offer: "v=0 offer",
  answer: null,
  ended_by: null,
  ...over,
});
const view = (s: TalkSession | null, online = true): ChannelView => ({ channel: "responder-001", responder_online: online, session: s });

describe("talkApi", () => {
  it("posts each step to the backend relay for that responder", async () => {
    const seen: string[] = [];
    const api = talkApi(async (url, init) => {
      seen.push(`${init?.method} ${url} ${init?.body ?? ""}`);
      return new Response(JSON.stringify(view(null)));
    });
    await api.status("responder-001");
    await api.heartbeat("responder-001");
    await api.offer("responder-001", "v=0 offer");
    await api.answer("responder-001", "s1", "v=0 answer");
    await api.hangup("responder-001", "s1", "responder");
    expect(seen).toEqual([
      "GET /aegis-api/api/v1/simulations/talk/channels/responder-001 ",
      "POST /aegis-api/api/v1/simulations/talk/channels/responder-001/heartbeat ",
      'POST /aegis-api/api/v1/simulations/talk/channels/responder-001/offer {"sdp":"v=0 offer"}',
      'POST /aegis-api/api/v1/simulations/talk/channels/responder-001/answer {"session_id":"s1","sdp":"v=0 answer"}',
      'POST /aegis-api/api/v1/simulations/talk/channels/responder-001/hangup {"session_id":"s1","by":"responder"}',
    ]);
  });

  it("explains backend errors and an unreachable backend", async () => {
    const stale = talkApi(async () => new Response(JSON.stringify({ detail: "That call is no longer active" }), { status: 409 }));
    await expect(stale.answer("responder-001", "old", "v=0")).rejects.toThrow("That call is no longer active");
    const down = talkApi(async () => {
      throw new TypeError("Failed to fetch");
    });
    await expect(down.heartbeat("responder-001")).rejects.toThrow("Can't reach the AEGIS backend.");
  });
});

describe("responderStep", () => {
  it("answers a new ringing call, once", () => {
    expect(responderStep(null, view(session()))).toBe("answer");
    expect(responderStep("s1", view(session()))).toBe("none");
    expect(responderStep("s1", view(session({ state: "connected", offer: null })))).toBe("none");
  });

  it("drops the call when it ends or the operator starts a new one", () => {
    expect(responderStep("s1", view(session({ state: "ended", offer: null })))).toBe("close");
    expect(responderStep("s1", view(null))).toBe("close");
    expect(responderStep("s1", view(session({ session_id: "s2" })))).toBe("answer");
    expect(responderStep("s1", view(session({ session_id: "s2", state: "ended" })))).toBe("close");
    expect(responderStep(null, view(session({ state: "ended" })))).toBe("none");
  });
});

describe("operatorStep", () => {
  it("applies the phone's answer once, and closes when the call is gone", () => {
    const answered = session({ state: "connected", offer: null, answer: "v=0 answer" });
    expect(operatorStep("s1", false, view(session({ offer: null })))).toBe("none");
    expect(operatorStep("s1", false, view(answered))).toBe("apply-answer");
    expect(operatorStep("s1", true, view(answered))).toBe("none");
    expect(operatorStep("s1", true, view(session({ state: "ended", ended_by: "responder" })))).toBe("close");
    expect(operatorStep("s1", true, view(session({ session_id: "other" })))).toBe("close");
  });
});

describe("waitForIce", () => {
  const fakePc = (initial: RTCIceGatheringState) => {
    const target = new EventTarget() as EventTarget & { iceGatheringState: RTCIceGatheringState };
    target.iceGatheringState = initial;
    return target as unknown as RTCPeerConnection & { iceGatheringState: RTCIceGatheringState };
  };

  it("resolves when gathering completes, or after the time limit", async () => {
    expect(await waitForIce(fakePc("complete"))).toBeUndefined();
    const pc = fakePc("gathering");
    const done = waitForIce(pc, 10_000);
    (pc as { iceGatheringState: RTCIceGatheringState }).iceGatheringState = "complete";
    pc.dispatchEvent(new Event("icegatheringstatechange"));
    await done;

    vi.useFakeTimers();
    const slow = waitForIce(fakePc("gathering"), 2500);
    vi.advanceTimersByTime(2500);
    await slow;
    vi.useRealTimers();
  });
});

describe("fetchRtcConfig", () => {
  it("uses the host's TURN relay when it has one", async () => {
    const config = await fetchRtcConfig(async () =>
      new Response(
        JSON.stringify({
          ice_servers: [
            { urls: ["stun:stun.l.google.com:19302"], username: null, credential: null },
            { urls: ["turn:100.64.0.5:3478"], username: "aegis", credential: "s3cret" },
          ],
          ice_transport_policy: "relay",
        }),
      ),
    );
    expect(config).toEqual({
      iceServers: [{ urls: ["stun:stun.l.google.com:19302"] }, { urls: ["turn:100.64.0.5:3478"], username: "aegis", credential: "s3cret" }],
      iceTransportPolicy: "relay",
    });
  });

  it("falls back to STUN when the backend has no such endpoint or is unreachable", async () => {
    expect(await fetchRtcConfig(async () => new Response("", { status: 404 }))).toEqual({ iceServers: ICE_SERVERS });
    expect(
      await fetchRtcConfig(async () => {
        throw new TypeError("Failed to fetch");
      }),
    ).toEqual({ iceServers: ICE_SERVERS });
  });
});

it("builds the phone link and readable errors", () => {
  expect(phonePageUrl("https://aegis-host.tail1234.ts.net/", "responder-001")).toBe(
    "https://aegis-host.tail1234.ts.net/responder?id=responder-001",
  );
  expect(talkErrorText(new DOMException("x", "NotAllowedError"))).toMatch(/permission denied/);
  expect(talkErrorText(new Error("Can't reach the AEGIS backend."))).toBe("Can't reach the AEGIS backend.");
  expect(talkErrorText(undefined)).toBe("Talk link failed.");
});
