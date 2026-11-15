/**
 * Talk link: operator dashboard ⇄ responder phone, browser to browser (WebRTC).
 * The backend only relays the call setup (see backend/app/api/talk.py); audio goes
 * directly between the two devices. The phone page (/responder) answers by itself.
 *
 * Every relay route needs `Authorization: Bearer <credential>` (backend/app/api/talk_auth.py):
 * - operator: a secret the operator types once per browser session (sessionStorage only — never a
 *   NEXT_PUBLIC_* variable or the Next proxy, because the phone reaches the same origin);
 * - phone: a per-channel token the operator mints with `enroll`, delivered in the link's #fragment.
 */

const ROOT = "/aegis-api/api/v1/simulations/talk";
const BASE = `${ROOT}/channels`;

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

export interface Enrollment {
  channel: string;
  token: string;
  expires_at: string;
  /** `/responder?id=<channel>#token=<token>` — the link IS the credential. */
  phone_path: string;
}

export type TalkAuthKind = "signin" | "forbidden" | "unconfigured";

/** 401 (sign in), 403 (not allowed for this credential/channel) or 503 (talk link off on this host). */
export class TalkAuthError extends Error {
  constructor(
    public kind: TalkAuthKind,
    public status: number,
    message: string,
  ) {
    super(message);
    this.name = "TalkAuthError";
  }
}

export const isTalkAuthError = (e: unknown): e is TalkAuthError => e instanceof TalkAuthError;

type Fetch = (input: string, init?: RequestInit) => Promise<Response>;
type GetToken = () => string | null | undefined;
const defaultFetch: Fetch = (input, init) => fetch(input, init);

function authHeaders(token: string | null | undefined, json: boolean): Record<string, string> | undefined {
  const h: Record<string, string> = {};
  if (json) h["Content-Type"] = "application/json";
  if (token) h.Authorization = `Bearer ${token}`;
  return Object.keys(h).length ? h : undefined;
}

async function raise(res: Response): Promise<never> {
  const json = await res.json().catch(() => null);
  const detail = typeof json?.detail === "string" ? json.detail : "";
  if (res.status === 401) throw new TalkAuthError("signin", 401, "Talk link sign-in required");
  if (res.status === 403) throw new TalkAuthError("forbidden", 403, detail || "Not allowed for this sign-in");
  if (res.status === 503) throw new TalkAuthError("unconfigured", 503, "Talk link is not configured on this host");
  throw new Error(detail || `Talk link error (${res.status})`);
}

export function talkApi(doFetch: Fetch = defaultFetch, getToken: GetToken = () => null) {
  const send = async (method: "GET" | "POST", path: string, body?: unknown, keepalive = false): Promise<Response> => {
    let res: Response;
    try {
      res = await doFetch(`${BASE}/${path}`, {
        method,
        cache: "no-store",
        keepalive,
        headers: authHeaders(getToken(), body !== undefined),
        body: body === undefined ? undefined : JSON.stringify(body),
      });
    } catch {
      throw new Error("Can't reach the AEGIS backend.");
    }
    if (!res.ok) await raise(res);
    return res;
  };
  const view = async (method: "GET" | "POST", path: string, body?: unknown, keepalive = false) =>
    (await (await send(method, path, body, keepalive)).json()) as ChannelView;
  const ch = encodeURIComponent;
  return {
    status: (channel: string) => view("GET", ch(channel)),
    heartbeat: (channel: string) => view("POST", `${ch(channel)}/heartbeat`),
    offer: (channel: string, sdp: string) => view("POST", `${ch(channel)}/offer`, { sdp }),
    answer: (channel: string, sessionId: string, sdp: string) => view("POST", `${ch(channel)}/answer`, { session_id: sessionId, sdp }),
    /** `keepalive` lets the hang-up survive the page closing. */
    hangup: (channel: string, sessionId: string, by: "operator" | "responder", keepalive = false) =>
      view("POST", `${ch(channel)}/hangup`, { session_id: sessionId, by }, keepalive),
    /** Operator: a phone link for this channel (the link is the credential). */
    enroll: async (channel: string) => (await (await send("POST", `${ch(channel)}/enroll`)).json()) as Enrollment,
    /** Operator: sign out every phone on this channel and end its call. */
    revoke: async (channel: string) => {
      await send("POST", `${ch(channel)}/revoke`);
    },
  };
}

interface IceConfig {
  ice_servers: { urls: string[]; username?: string | null; credential?: string | null }[];
  ice_transport_policy: "all" | "relay";
}

/** The host's network-path settings (STUN, plus a TURN relay if configured); never throws. */
export async function fetchRtcConfig(doFetch: Fetch = defaultFetch, token?: string | null): Promise<RTCConfiguration> {
  try {
    const res = await doFetch(`${ROOT}/ice-servers`, { cache: "no-store", headers: authHeaders(token, false) });
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

/** Origin the phone should use: an explicit HTTPS phone origin, else this page if it is HTTPS. */
export function phoneOrigin(configured: string | undefined, location: Pick<Location, "protocol" | "origin">): string | null {
  if (configured) return configured.replace(/\/$/, "");
  return location.protocol === "https:" ? location.origin : null;
}

/** Full phone link from an enrollment. */
export function phoneLink(origin: string, enrollment: Pick<Enrollment, "phone_path">): string {
  return `${origin.replace(/\/$/, "")}${enrollment.phone_path}`;
}

/* ── Credential storage (sessionStorage only: gone when the tab closes) ── */

export const OPERATOR_TOKEN_KEY = "aegis.talk.operator";
export const phoneTokenKey = (channel: string) => `aegis.talk.phone.${channel}`;

type Store = Pick<Storage, "getItem" | "setItem" | "removeItem">;
const safe = <T>(f: () => T, fallback: T): T => {
  try {
    return f();
  } catch {
    return fallback;
  }
};
export const readToken = (store: Store | undefined, key: string) => safe(() => store?.getItem(key) ?? null, null);
export const writeToken = (store: Store | undefined, key: string, token: string | null) =>
  safe(() => (token ? store?.setItem(key, token) : store?.removeItem(key)), undefined);

/**
 * The phone token from a link's `#token=…`, and the same URL without it (to `history.replaceState`),
 * so the credential doesn't stay in the address bar, history or screenshots.
 */
export function takeTokenFromUrl(href: string): { token: string | null; cleaned: string } {
  const url = new URL(href);
  const hash = new URLSearchParams(url.hash.replace(/^#/, ""));
  const token = hash.get("token");
  if (!token) return { token: null, cleaned: href };
  hash.delete("token");
  const rest = hash.toString();
  url.hash = rest ? `#${rest}` : "";
  return { token, cleaned: url.toString() };
}

export function talkErrorText(err: unknown): string {
  if (isTalkAuthError(err)) return err.message;
  const name = err instanceof DOMException || err instanceof Error ? err.name : "";
  if (name === "NotAllowedError") return "Microphone permission denied: allow it in the browser (address bar → Microphone → Allow), then try again.";
  if (name === "NotFoundError") return "No microphone found on this device.";
  if (name === "NotReadableError") return "The microphone is busy in another app or tab.";
  const message = err instanceof Error ? err.message.replace(/\s+/g, " ").trim() : "";
  return message.slice(0, 160) || "Talk link failed.";
}
