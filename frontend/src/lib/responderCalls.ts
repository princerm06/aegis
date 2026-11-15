/**
 * Operator → responder phone calls (Twilio; see backend/README.md "Responder phone calls").
 * The backend keeps the numbers and secrets; the dashboard only sees a masked hint
 * and a short-lived token that can call that one responder.
 */

const BASE = "/aegis-api/api/v1/simulations/responder-calls";

export interface CallableResponder {
  responder_id: string;
  number_hint: string;
}

export interface CallStatus {
  configured: boolean;
  responders: CallableResponder[];
  note: string;
}

export interface CallToken {
  responder_id: string;
  number_hint: string;
  token: string;
  expires_in_s: number;
}

export const NOT_SET_UP: CallStatus = { configured: false, responders: [], note: "Phone calls are not set up on this host." };

type Fetch = (input: string, init?: RequestInit) => Promise<Response>;

/** Never throws: an older backend (404) or an unreachable one reads as "not set up". */
export async function fetchCallStatus(doFetch: Fetch = fetch): Promise<CallStatus> {
  try {
    const res = await doFetch(BASE, { cache: "no-store" });
    if (!res.ok) return NOT_SET_UP;
    const body = (await res.json()) as Partial<CallStatus>;
    return Array.isArray(body.responders) ? { ...NOT_SET_UP, ...body } as CallStatus : NOT_SET_UP;
  } catch {
    return NOT_SET_UP;
  }
}

export async function fetchCallToken(responderId: string, doFetch: Fetch = fetch): Promise<CallToken> {
  let res: Response;
  try {
    res = await doFetch(`${BASE}/${encodeURIComponent(responderId)}/token`, { method: "POST", cache: "no-store" });
  } catch {
    throw new Error("Backend unreachable");
  }
  const body = await res.json().catch(() => null);
  if (!res.ok) throw new Error(typeof body?.detail === "string" ? body.detail : `Call setup failed (${res.status})`);
  return body as CallToken;
}

export type CallAvailability =
  | { kind: "unconfigured"; text: string }
  | { kind: "no-number"; text: string }
  | { kind: "ready"; hint: string };

export function callAvailability(status: CallStatus, responderId: string, name: string): CallAvailability {
  if (!status.configured) return { kind: "unconfigured", text: status.note || NOT_SET_UP.note };
  const match = status.responders.find((r) => r.responder_id === responderId);
  return match ? { kind: "ready", hint: match.number_hint } : { kind: "no-number", text: `No phone number set for ${name}.` };
}

/** Twilio errors carry a numeric `code`; turn the ones an operator can act on into plain instructions. */
export function callErrorText(err: unknown, name: string): string {
  const e = (err ?? {}) as { code?: number; name?: string; message?: string };
  const code = typeof e.code === "number" ? e.code : 0;
  if ((code >= 20101 && code <= 20107) || code === 31204 || code === 31205)
    return "Twilio rejected this host's call keys. Check the TWILIO_* values in backend/.env and restart the backend.";
  if (code === 31208 || code === 31401 || code === 31402 || e.name === "NotAllowedError")
    return "Microphone permission denied: allow it in the browser prompt, then call again.";
  if (code === 31486) return `${name}'s phone is busy.`;
  if (code === 31480 || code === 31603) return `${name} didn't answer.`;
  if (code === 13224 || code === 21219 || code === 21215)
    return `Twilio can't call ${name}'s number. On a trial account it must be a Verified Caller ID.`;
  if (code >= 31000 && code < 32000) return "Lost the connection to Twilio. Check this computer's internet and call again.";
  const raw = (e.message ?? "").replace(/\s+/g, " ").trim();
  return raw ? raw.slice(0, 160) : "Call failed.";
}

/** 0:07, 1:23, 12:05 */
export function callClock(seconds: number): string {
  const s = Math.max(0, Math.floor(seconds));
  return `${Math.floor(s / 60)}:${String(s % 60).padStart(2, "0")}`;
}
