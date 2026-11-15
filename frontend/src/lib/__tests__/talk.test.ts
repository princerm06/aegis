import { describe, expect, it, vi } from "vitest";
import {
  ICE_SERVERS,
  OPERATOR_TOKEN_KEY,
  TalkAuthError,
  fetchRtcConfig,
  operatorStep,
  phoneLink,
  phoneOrigin,
  phoneTokenKey,
  readToken,
  responderStep,
  takeTokenFromUrl,
  talkApi,
  talkErrorText,
  waitForIce,
  writeToken,
  type ChannelView,
  type TalkSession,
} from "../talk";

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
  const enrollment = { phone_path: "/responder?id=responder-001#token=rt1.abc" };
  expect(phoneLink("https://aegis-host.tail1234.ts.net/", enrollment)).toBe("https://aegis-host.tail1234.ts.net/responder?id=responder-001#token=rt1.abc");
  expect(talkErrorText(new DOMException("x", "NotAllowedError"))).toMatch(/permission denied/);
  expect(talkErrorText(new Error("Can't reach the AEGIS backend."))).toBe("Can't reach the AEGIS backend.");
  expect(talkErrorText(undefined)).toBe("Talk link failed.");
});

describe("auth contract (PR #37)", () => {
  const ok = (body: unknown = view(null), status = 200) => new Response(body === null ? null : JSON.stringify(body), { status });

  it("sends the current credential as a bearer token on every route, and none when signed out", async () => {
    const headers: (string | null)[] = [];
    let token: string | null = "operator-secret";
    const api = talkApi(
      async (_url, init) => {
        headers.push(new Headers(init?.headers).get("Authorization"));
        return ok();
      },
      () => token,
    );
    await api.status("responder-001");
    await api.offer("responder-001", "v=0");
    token = null;
    await api.status("responder-001");
    expect(headers).toEqual(["Bearer operator-secret", "Bearer operator-secret", null]);
  });

  it("maps 401 / 403 / 503 to typed auth errors without echoing the token", async () => {
    const at = (status: number, detail = "x") => talkApi(async () => ok({ detail }, status), () => "s3cret-value");
    await expect(at(401).status("responder-001")).rejects.toMatchObject({ kind: "signin", message: "Talk link sign-in required" });
    await expect(at(403, "This device isn't signed in for that responder").heartbeat("responder-002")).rejects.toMatchObject({ kind: "forbidden" });
    await expect(at(503).status("responder-001")).rejects.toMatchObject({ kind: "unconfigured", message: "Talk link is not configured on this host" });
    const err = await at(401).status("responder-001").catch((e: unknown) => e);
    expect(err).toBeInstanceOf(TalkAuthError);
    expect(String((err as Error).message)).not.toContain("s3cret");
  });

  it("enrolls a phone and revokes (204, no body)", async () => {
    const seen: string[] = [];
    const api = talkApi(
      async (url, init) => {
        seen.push(`${init?.method} ${url}`);
        return url.endsWith("/revoke")
          ? new Response(null, { status: 204 })
          : ok({ channel: "responder-001", token: "rt1.t", expires_at: "2026-11-15T18:00:00Z", phone_path: "/responder?id=responder-001#token=rt1.t" });
      },
      () => "operator-secret",
    );
    const e = await api.enroll("responder-001");
    expect(e.phone_path).toContain("#token=rt1.t");
    await expect(api.revoke("responder-001")).resolves.toBeUndefined();
    expect(seen).toEqual([
      "POST /aegis-api/api/v1/simulations/talk/channels/responder-001/enroll",
      "POST /aegis-api/api/v1/simulations/talk/channels/responder-001/revoke",
    ]);
  });

  it("hang-up can be sent with keepalive so it survives the page closing", async () => {
    let keepalive: boolean | undefined;
    const api = talkApi(async (_u, init) => {
      keepalive = init?.keepalive;
      return ok();
    });
    await api.hangup("responder-001", "s1", "operator", true);
    expect(keepalive).toBe(true);
  });

  it("asks for ICE servers with the participant's token", async () => {
    let auth: string | null = null;
    await fetchRtcConfig(async (_u, init) => {
      auth = new Headers(init?.headers).get("Authorization");
      return ok({ ice_servers: [{ urls: ["stun:x"] }], ice_transport_policy: "all" });
    }, "rt1.phone");
    expect(auth).toBe("Bearer rt1.phone");
  });

  it("takes the phone token out of the link fragment and cleans the URL", () => {
    expect(takeTokenFromUrl("https://h.ts.net/responder?id=responder-001#token=rt1.a.b")).toEqual({
      token: "rt1.a.b",
      cleaned: "https://h.ts.net/responder?id=responder-001",
    });
    expect(takeTokenFromUrl("https://h.ts.net/responder?id=responder-001")).toEqual({
      token: null,
      cleaned: "https://h.ts.net/responder?id=responder-001",
    });
  });

  it("only offers an HTTPS phone origin", () => {
    expect(phoneOrigin(undefined, { protocol: "https:", origin: "https://h.ts.net" })).toBe("https://h.ts.net");
    expect(phoneOrigin(undefined, { protocol: "http:", origin: "http://192.168.137.1:3000" })).toBeNull();
    expect(phoneOrigin("https://h.ts.net/", { protocol: "http:", origin: "http://x" })).toBe("https://h.ts.net");
  });

  it("stores credentials per key in session storage and tolerates blocked storage", () => {
    const m = new Map<string, string>();
    const store = { getItem: (k: string) => m.get(k) ?? null, setItem: (k: string, v: string) => void m.set(k, v), removeItem: (k: string) => void m.delete(k) };
    writeToken(store, OPERATOR_TOKEN_KEY, "op");
    writeToken(store, phoneTokenKey("responder-001"), "rt1");
    expect(readToken(store, OPERATOR_TOKEN_KEY)).toBe("op");
    expect(readToken(store, phoneTokenKey("responder-002"))).toBeNull();
    writeToken(store, OPERATOR_TOKEN_KEY, null);
    expect(readToken(store, OPERATOR_TOKEN_KEY)).toBeNull();
    const blocked = { getItem: () => { throw new Error("blocked"); }, setItem: () => { throw new Error("blocked"); }, removeItem: () => {} };
    expect(readToken(blocked, OPERATOR_TOKEN_KEY)).toBeNull();
    expect(() => writeToken(blocked, OPERATOR_TOKEN_KEY, "x")).not.toThrow();
  });
});
