/**
 * Capability checks for browser camera/microphone access (VideoFeed, push-to-talk).
 *
 * `navigator.mediaDevices` is undefined on insecure origins (plain LAN HTTP such as
 * 192.168.x.x:3000) and in old browsers, so every access must be guarded. These
 * checks use the secure-context flag and API availability — never hostname guesses.
 */

export interface MediaDevicesShape {
  getUserMedia?: unknown;
  enumerateDevices?: unknown;
}

export interface MediaEnvironment {
  /** `window.isSecureContext` — false on plain-http LAN hosts. */
  secureContext: boolean;
  /** `navigator.mediaDevices` when present. */
  mediaDevices?: MediaDevicesShape | null;
}

export type MediaBlockerKind = "none" | "insecure-context" | "unavailable" | "no-capture" | "no-list";

export interface MediaBlocker {
  kind: MediaBlockerKind;
}

/** Pure probe: safe to call with a mocked environment in tests. */
export function assessMediaSupport(env: MediaEnvironment): MediaBlocker {
  // Insecure contexts are blocked before any API call: browsers either hide
  // mediaDevices there or reject capture, so the cause is reported up front.
  if (!env.secureContext) return { kind: "insecure-context" };
  const md = env.mediaDevices ?? null;
  if (!md) return { kind: "unavailable" };
  if (typeof md.getUserMedia !== "function") return { kind: "no-capture" };
  if (typeof md.enumerateDevices !== "function") return { kind: "no-list" };
  return { kind: "none" };
}

/** Reads the real browser environment. Never throws. */
export function readMediaEnvironment(): MediaEnvironment {
  try {
    if (typeof window === "undefined" || typeof navigator === "undefined") {
      return { secureContext: false, mediaDevices: null };
    }
    return {
      secureContext: window.isSecureContext !== false,
      mediaDevices: (navigator as Navigator & { mediaDevices?: MediaDevicesShape }).mediaDevices ?? null,
    };
  } catch {
    return { secureContext: false, mediaDevices: null };
  }
}

/** Scope note: local capture is the viewing browser's camera, not the CV publisher. */
export const LOCAL_CAMERA_SCOPE_NOTE =
  "Local cameras come from this viewing device — the computer or tablet showing this dashboard — not from the remote CV publisher.";

/** Short status-line text plus the full setup-panel explanation for a blocker. */
export function mediaBlockerCopy(kind: Exclude<MediaBlockerKind, "none">): { short: string; title: string; body: string } {
  switch (kind) {
    case "insecure-context":
      return {
        short: "Camera blocked: insecure connection. Use localhost or HTTPS.",
        title: "Local camera unavailable on this connection",
        body: "This page is open over an insecure connection (plain HTTP on a network address), so the browser disables camera and microphone access. Open the command center on the host via localhost — or serve it over HTTPS — then use Connect again. Stream URLs below still work.",
      };
    case "unavailable":
      return {
        short: "Camera APIs unavailable in this browser.",
        title: "Local camera unavailable in this browser",
        body: "This browser does not expose camera APIs. Use a current Chrome or Edge, ideally on the host via localhost or HTTPS. Stream URLs below still work.",
      };
    case "no-capture":
      return {
        short: "This browser cannot capture camera input.",
        title: "Camera capture not supported here",
        body: "This browser exposes device APIs but not camera capture. Use a current Chrome or Edge, ideally on the host via localhost or HTTPS. Stream URLs below still work.",
      };
    case "no-list":
      return {
        short: "This browser cannot list cameras.",
        title: "Camera listing not supported here",
        body: "This browser can capture but cannot list cameras, so picking a specific device is unavailable. Use a current Chrome or Edge — or enter a stream URL below, which works regardless.",
      };
  }
}

/**
 * Maps getUserMedia/enumerateDevices failures to operator-readable guidance.
 * Never surfaces raw TypeErrors such as "Cannot read properties of undefined".
 */
export function friendlyMediaError(err: unknown): string {
  const name = err instanceof DOMException ? err.name : err instanceof Error ? err.name : "";
  const raw = err instanceof Error ? err.message : String(err);
  // A TypeError from a missing mediaDevices object means the same as "unavailable".
  if (name === "TypeError" || (/mediaDevices|getUserMedia/i.test(raw) && !name)) {
    return mediaBlockerCopy(readMediaEnvironment().secureContext ? "unavailable" : "insecure-context").short;
  }
  switch (name) {
    case "NotAllowedError":
      return "Permission denied: allow camera access in the browser prompt, then try Connect again.";
    case "NotFoundError":
    case "OverconstrainedError":
      return "Selected camera not found — it may be unplugged. Pick another source.";
    case "NotReadableError":
      return "Camera is busy in another app or tab. Close it and try again.";
    case "SecurityError":
      return mediaBlockerCopy("insecure-context").short;
    case "AbortError":
      return "Camera request was interrupted. Try Connect again.";
    default:
      return `Camera error: ${oneLine(raw) || "unknown failure"}`;
  }
}

function oneLine(s: string): string {
  return s.replace(/\s+/g, " ").trim().slice(0, 160);
}
