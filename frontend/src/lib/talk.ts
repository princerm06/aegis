/**
 * Talk link: operator dashboard ⇄ responder phone, browser to browser (WebRTC).
 * The backend only relays the call setup (see backend/app/api/talk.py); audio goes
 * directly between the two devices. The phone page (/responder) answers by itself.
 */

const BASE = "/aegis-api/api/v1/simulations/talk/channels";

export const POLL_MS = 1500;
export const RING_TIMEOUT_MS = 20_000;
export const ICE_SERVERS: RTCIceServer[] = [{ urls: "stun:stun.l.google.com:19302" }];
export const MIC: MediaTrackConstraints = { echoCancellation: true, noiseSuppression: true, autoGainControl: true };

export interface TalkSession {
  session_id: string;
  state: "ringing" | "connected" | "ended";
  offer: string | null;
  answer: string | null;
  ended_by: "operator" | "responder" | null;
}

export interface ChannelView {
  channel: string;
  responder_online: boolean;
  session: TalkSession | null;
}

type Fetch = (input: string, init?: RequestInit) => Promise<Response>;

export function talkApi(doFetch: Fetch = (input, init) => fetch(input, init)) {
  const call = async (method: "GET" | "POST", path: string, body?: unknown): Promise<ChannelView> => {
    let res: Response;
    try {
      res = await doFetch(`${BASE}/${path}`, {
        method,
        cache: "no-store",
        headers: body === undefined ? undefined : { "Content-Type": "application/json" },
        body: body === undefined ? undefined : JSON.stringify(body),
      });
    } catch {
      throw new Error("Can't reach the AEGIS backend.");
    }
    const json = await res.json().catch(() => null);
    if (!res.ok) throw new Error(typeof json?.detail === "string" ? json.detail : `Talk link error (${res.status})`);
    return json as ChannelView;
  };
  const ch = encodeURIComponent;
  return {
    status: (channel: string) => call("GET", ch(channel)),
    heartbeat: (channel: string) => call("POST", `${ch(channel)}/heartbeat`),
    offer: (channel: string, sdp: string) => call("POST", `${ch(channel)}/offer`, { sdp }),
    answer: (channel: string, sessionId: string, sdp: string) => call("POST", `${ch(channel)}/answer`, { session_id: sessionId, sdp }),
    hangup: (channel: string, sessionId: string, by: "operator" | "responder") =>
      call("POST", `${ch(channel)}/hangup`, { session_id: sessionId, by }),
  };
}

interface IceConfig {
  ice_servers: { urls: string[]; username?: string | null; credential?: string | null }[];
  ice_transport_policy: "all" | "relay";
}

/** The host's network-path settings (STUN, plus a TURN relay if configured); never throws. */
export async function fetchRtcConfig(doFetch: Fetch = (input, init) => fetch(input, init)): Promise<RTCConfiguration> {
  try {
    const res = await doFetch("/aegis-api/api/v1/simulations/talk/ice-servers", { cache: "no-store" });
    if (!res.ok) throw new Error(String(res.status));
    const body = (await res.json()) as IceConfig;
    const iceServers = body.ice_servers.map(({ urls, username, credential }) => ({
      urls,
      ...(username ? { username } : {}),
      ...(credential ? { credential } : {}),
    }));
    return { iceServers, iceTransportPolicy: body.ice_transport_policy === "relay" ? "relay" : "all" };
  } catch {
    return { iceServers: ICE_SERVERS };
  }
}

/** What the phone should do with the latest heartbeat, given the call it is handling (if any). */
export function responderStep(current: string | null, view: ChannelView): "answer" | "close" | "none" {
  const s = view.session;
  if (s && s.session_id !== current && s.state === "ringing" && s.offer) return "answer";
  if (current && (!s || s.session_id !== current || s.state === "ended")) return "close";
  return "none";
}

/** What the dashboard should do with the latest status, for the call it started. */
export function operatorStep(mine: string, answered: boolean, view: ChannelView): "apply-answer" | "close" | "none" {
  const s = view.session;
  if (!s || s.session_id !== mine || s.state === "ended") return "close";
  if (!answered && s.state === "connected" && s.answer) return "apply-answer";
  return "none";
}

type Gathering = Pick<RTCPeerConnection, "iceGatheringState" | "addEventListener" | "removeEventListener">;

/** Waits until ICE candidates are gathered (so one offer/answer carries them all), at most `ms`. */
export function waitForIce(pc: Gathering, ms = 2500): Promise<void> {
  if (pc.iceGatheringState === "complete") return Promise.resolve();
  return new Promise((resolve) => {
    const done = () => {
      if (pc.iceGatheringState !== "complete") return;
      pc.removeEventListener("icegatheringstatechange", done);
      clearTimeout(timer);
      resolve();
    };
    const timer = setTimeout(() => {
      pc.removeEventListener("icegatheringstatechange", done);
      resolve();
    }, ms);
    pc.addEventListener("icegatheringstatechange", done);
  });
}

/** The address a responder opens on their phone. Phones need HTTPS for the microphone. */
export function phonePageUrl(origin: string, responderId: string): string {
  return `${origin.replace(/\/$/, "")}/responder?id=${encodeURIComponent(responderId)}`;
}

export function talkErrorText(err: unknown): string {
  const name = err instanceof DOMException || err instanceof Error ? err.name : "";
  if (name === "NotAllowedError") return "Microphone permission denied: allow it in the browser, then try again.";
  if (name === "NotFoundError") return "No microphone found on this device.";
  if (name === "NotReadableError") return "The microphone is busy in another app.";
  const message = err instanceof Error ? err.message.replace(/\s+/g, " ").trim() : "";
  return message.slice(0, 160) || "Talk link failed.";
}
