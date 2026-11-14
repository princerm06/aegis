/**
 * Same-origin client for the standalone read-only AEGIS Voice V1 service.
 *
 * All requests go through the Next.js rewrite (`/aegis-voice/*` -> `http://127.0.0.1:8010/*`),
 * keeping ElevenLabs credentials strictly server-side inside `voice/.env` and never exposing
 * them to the browser.
 */

import { SCENARIO_ID } from "./api";

const VOICE_BASE = "/aegis-voice";

export type VoiceDeliveryMode =
  | "full_speech"
  | "text_only"
  | "partial_text_fallback"
  | "backend_unavailable"
  | "stt_failed";

export interface VoiceHealthResponse {
  status: "ok";
  service: "aegis-voice";
  version: string;
  mode: "simulation";
  read_only: true;
  aegis_backend_url: string;
  default_scenario_id: string;
  speech_Configured: boolean;
  operational_mode: "full_speech" | "text_only";
  elevenlabs_voice_id: string;
  elevenlabs_tts_model: string;
  elevenlabs_stt_model: string;
}

export interface VoiceTurnResponse {
  mode: "simulation";
  read_only: true;
  state_mutated: false;
  session_id: string;
  scenario_id: string;
  user_transcript: string;
  intent: string;
  spoken_text: string;
  display_text: string;
  delivery_mode: VoiceDeliveryMode;
  audio_base64: string | null;
  audio_mime_type: string | null;
  tts_cached: boolean;
  referenced_entities: {
    responder_id: string | null;
    casualty_id: string | null;
    proposal_id: string | null;
    assignment_id: string | null;
  };
  trace_fields: string[];
  warnings: string[];
  error_category: string | null;
}

export class VoiceServiceError extends Error {
  constructor(
    public status: number,
    public detail: string,
  ) {
    super(detail);
  }
}

async function voiceFetch<T>(method: "GET" | "POST", path: string, body?: unknown): Promise<T> {
  let res: Response;
  try {
    res = await fetch(`${VOICE_BASE}${path}`, {
      method,
      headers: body !== undefined ? { "Content-Type": "application/json" } : undefined,
      body: body !== undefined ? JSON.stringify(body) : undefined,
      cache: "no-store",
    });
  } catch {
    throw new VoiceServiceError(0, "Voice service unreachable (port 8010)");
  }
  const text = await res.text();
  let json: unknown = null;
  if (text) {
    try {
      json = JSON.parse(text);
    } catch {
      json = null;
    }
  }
  if (!res.ok) {
    const detail =
      json && typeof json === "object" && "detail" in json
        ? String((json as { detail: unknown }).detail)
        : `Voice service returned HTTP ${res.status}`;
    throw new VoiceServiceError(res.status, detail);
  }
  if (!json || typeof json !== "object") {
    throw new VoiceServiceError(502, "Voice service unavailable (port 8010)");
  }
  return json as T;
}

export const voiceApi = {
  health: () => voiceFetch<VoiceHealthResponse>("GET", "/health"),

  query: (sessionId: string, text: string, speak: boolean, scenarioId = SCENARIO_ID) =>
    voiceFetch<VoiceTurnResponse>("POST", "/api/v1/voice/query", {
      mode: "simulation",
      session_id: sessionId,
      scenario_id: scenarioId,
      text,
      speak,
    }),

  audioTurn: (
    sessionId: string,
    audioBase64: string,
    mimeType: string,
    filename: string,
    durationSeconds: number,
    speak: boolean,
    scenarioId = SCENARIO_ID,
  ) =>
    voiceFetch<VoiceTurnResponse>("POST", "/api/v1/voice/audio-turn", {
      mode: "simulation",
      session_id: sessionId,
      scenario_id: scenarioId,
      audio_base64: audioBase64,
      mime_type: mimeType,
      filename,
      duration_seconds: Math.min(Math.max(durationSeconds, 0.2), 20.0),
      speak,
    }),

  resetSession: (sessionId: string) =>
    voiceFetch<{ session_reset: boolean }>("POST", `/api/v1/voice/session/${encodeURIComponent(sessionId)}/reset`),
};
