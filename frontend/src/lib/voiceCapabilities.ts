import { assessMediaSupport, readMediaEnvironment, type MediaEnvironment } from "./mediaCapabilities";

/**
 * What Ask AEGIS can do on THIS browser + voice host, decided before the operator presses
 * anything, with plain recovery steps. Pure (except `readMediaEnvironment`), so it is unit-tested.
 *
 * Voice input needs three things: a secure context (HTTPS or localhost) so the browser exposes the
 * microphone, a browser that can record, and speech-to-text configured on the voice host
 * (ElevenLabs key in voice/.env — server-side only, never in the browser). Typed questions only
 * need the voice service.
 */
export type VoiceServiceMode = "full_speech" | "text_only" | "offline";
export type MicKind = "ready" | "insecure" | "unsupported" | "service-offline" | "speech-off";

export interface MicStatus {
  kind: MicKind;
  /** Short status for the capability line / button title. */
  short: string;
  /** What to do about it, shown when the operator presses the mic. */
  help: string;
}

export const DEFAULT_MAX_RECORDING_S = 20;

export function micStatus(env: MediaEnvironment, canRecord: boolean, service: VoiceServiceMode): MicStatus {
  const media = assessMediaSupport(env).kind;
  if (media === "insecure-context")
    return {
      kind: "insecure",
      short: "Mic blocked: needs HTTPS or localhost",
      help: "This dashboard is open over plain HTTP on a network address, so the browser blocks the microphone. To ask by voice, open the dashboard on the host laptop at http://localhost:3000/command, or serve it over HTTPS. Typed questions work here.",
    };
  // "no-list" only affects choosing a camera; recording audio needs getUserMedia + MediaRecorder.
  if (media === "unavailable" || media === "no-capture" || !canRecord)
    return {
      kind: "unsupported",
      short: "Mic: this browser can't record audio",
      help: "This browser can't record audio for Ask AEGIS. Use a current Chrome or Edge. Typed questions work here.",
    };
  if (service === "offline")
    return {
      kind: "service-offline",
      short: "Voice service offline",
      help: "The AEGIS Voice service isn't answering through /aegis-voice. Ask AEGIS resumes when it's back — press Retry to check again. The rest of the Command Center is unaffected.",
    };
  if (service === "text_only")
    return {
      kind: "speech-off",
      short: "Voice input off: speech not configured on the voice host",
      help: "Speech-to-text needs an ElevenLabs key in voice/.env on the voice host (kept server-side, never in the browser), then a restart of the voice service. Until then, type your question.",
    };
  return { kind: "ready", short: "Mic ready", help: "" };
}

/** Status for the current browser, read live. */
export function currentMicStatus(service: VoiceServiceMode): MicStatus {
  return micStatus(readMediaEnvironment(), typeof MediaRecorder !== "undefined", service);
}

/** Operator-readable microphone failure (getUserMedia / MediaRecorder). Never a raw TypeError. */
export function micErrorCopy(err: unknown): string {
  const name = err instanceof DOMException ? err.name : err instanceof Error ? err.name : "";
  switch (name) {
    case "NotAllowedError":
      return "Microphone permission denied. Click the lock icon in the address bar, allow Microphone, then press 🎙 Ask again. Typed questions still work.";
    case "NotFoundError":
    case "OverconstrainedError":
      return "No microphone found. Connect one (or pick an input in system sound settings), then press 🎙 Ask again.";
    case "NotReadableError":
      return "The microphone is busy in another app or tab (for example a call or responder push-to-talk). Free it, then try again.";
    case "SecurityError":
      return micStatus({ secureContext: false, mediaDevices: null }, true, "full_speech").help;
    case "TypeError": {
      // A missing mediaDevices object: insecure page or a browser without capture.
      const s = micStatus(readMediaEnvironment(), true, "full_speech");
      return s.kind === "ready" ? "Couldn't start the microphone. Type your question instead." : s.help;
    }
    case "AbortError":
      return "Microphone request was interrupted. Press 🎙 Ask again.";
    default:
      return "Couldn't start the microphone. Type your question instead.";
  }
}

export interface SpeechOutputStatus {
  available: boolean;
  label: string;
  title: string;
}

/** Spoken answers: only when the voice host has text-to-speech configured and the operator hasn't muted. */
export function speechOutput(service: VoiceServiceMode, muted: boolean): SpeechOutputStatus {
  if (service !== "full_speech")
    return {
      available: false,
      label: muted ? "🔇 Muted" : "🔊 n/a",
      title: "Spoken answers need ElevenLabs configured on the voice host; answers are shown as text.",
    };
  return muted
    ? { available: true, label: "🔇 Muted", title: "Spoken answers muted — answers are shown as text. Click to unmute." }
    : { available: true, label: "🔊 On", title: "Spoken answers on (ElevenLabs). Click to mute." };
}

export function formatElapsed(seconds: number): string {
  const s = Math.max(0, Math.floor(seconds));
  return `${Math.floor(s / 60)}:${String(s % 60).padStart(2, "0")}`;
}
