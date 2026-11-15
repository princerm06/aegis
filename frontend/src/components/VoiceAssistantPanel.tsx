"use client";

import { useCallback, useEffect, useLayoutEffect, useRef, useState } from "react";
import { voiceApi, VoiceServiceError, type VoiceTurnResponse } from "@/lib/voiceApi";
import {
  currentMicStatus,
  DEFAULT_MAX_RECORDING_S,
  formatElapsed,
  micErrorCopy,
  speechOutput,
  type MicStatus,
  type VoiceServiceMode,
} from "@/lib/voiceCapabilities";
import { Badge } from "./ui";

type UiState = "idle" | "listening" | "transcribing" | "thinking" | "speaking" | "offline" | "error";

interface TurnRecord extends VoiceTurnResponse {
  turnId: string;
  at: string;
}

const QUICK_PROMPTS = [
  { label: "Situation", query: "AEGIS, what's our current situation?" },
  { label: "Explain plan", query: "Explain the current plan." },
  { label: "Where is Alpha?", query: "Where is Responder Alpha?" },
  { label: "Unassigned", query: "Which casualties are unassigned?" },
  { label: "Recent changes", query: "What changed recently?" },
  { label: "Ack status", query: "Who hasn't acknowledged?" },
  { label: "Tell me more", query: "Tell me more." },
];

function pickSupportedAudioMime(): string {
  if (typeof window === "undefined" || typeof MediaRecorder === "undefined") return "";
  const candidates = ["audio/webm;codecs=opus", "audio/webm", "audio/mp4", "audio/ogg;codecs=opus"];
  for (const c of candidates) {
    if (typeof MediaRecorder.isTypeSupported === "function" && MediaRecorder.isTypeSupported(c)) {
      return c;
    }
  }
  return "";
}

/**
 * Ask AEGIS — Read-only conversational situational-awareness assistant.
 *
 * Clearly distinct from Responder Comms (`CommsPanel`):
 * - Responder Comms sends instructions TO a firefighter (and, separately, the Talk link).
 * - Ask AEGIS answers the operator's questions ABOUT live simulation state
 *   using the standalone read-only AEGIS Voice service (`port 8010`).
 *
 * What works on this browser/host is decided up front (`voiceCapabilities`): the mic explains why
 * it is unavailable (plain-HTTP LAN page, no speech-to-text on the host, offline) instead of
 * failing after the operator has spoken. Typed questions always remain available.
 */
export function VoiceAssistantPanel({ scenarioId }: { scenarioId: string }) {
  const [expanded, setExpanded] = useState(false);
  // The right column has a fixed height: the open panel floats over Comms instead of pushing
  // Comms' controls off-screen, sized to the space left in the column.
  const sectionRef = useRef<HTMLElement>(null);
  const [sheetMax, setSheetMax] = useState(250);
  useLayoutEffect(() => {
    if (!expanded) return;
    const fit = () => {
      const el = sectionRef.current;
      const col = el?.parentElement;
      if (!el || !col) return;
      const room = col.getBoundingClientRect().bottom - el.getBoundingClientRect().bottom - 6;
      // At least enough for the chips, input and newest answer on short laptop screens.
      setSheetMax(Math.max(260, Math.round(room)));
    };
    fit();
    window.addEventListener("resize", fit);
    return () => window.removeEventListener("resize", fit);
  }, [expanded]);
  const [uiState, setUiState] = useState<UiState>("idle");
  const [serviceMode, setServiceMode] = useState<VoiceServiceMode>("offline");
  const [healthChecked, setHealthChecked] = useState(false);
  const [maxRecordS, setMaxRecordS] = useState(DEFAULT_MAX_RECORDING_S);
  const [muted, setMuted] = useState(false);
  const [speaking, setSpeaking] = useState(false);
  const [recording, setRecording] = useState(false);
  const [elapsed, setElapsed] = useState(0);
  const [text, setText] = useState("");
  const [turns, setTurns] = useState<TurnRecord[]>([]);
  const [openDetails, setOpenDetails] = useState<Record<string, boolean>>({});
  const [banner, setBanner] = useState<{ text: string; retry?: boolean } | null>(null);
  // Mic capability depends on the browser (secure context, MediaRecorder) — read on the client only.
  const [mic, setMic] = useState<MicStatus>({ kind: "service-offline", short: "Checking voice service…", help: "Checking the voice service…" });

  const sessionIdRef = useRef<string>("cmd-voice-default");
  const audioRef = useRef<HTMLAudioElement | null>(null);
  const lastPlayedTurnIdRef = useRef<string | null>(null);
  const recorderRef = useRef<MediaRecorder | null>(null);
  const streamRef = useRef<MediaStream | null>(null);
  const chunksRef = useRef<Blob[]>([]);
  const recordStartRef = useRef<number>(0);
  const cancelRef = useRef(false);
  const mountedRef = useRef(true);

  useEffect(() => {
    sessionIdRef.current = `cmd-${Math.random().toString(36).slice(2, 10)}`;
    mountedRef.current = true;
    return () => {
      mountedRef.current = false;
    };
  }, []);

  useEffect(() => setMic(currentMicStatus(serviceMode)), [serviceMode]);

  const checkHealth = useCallback(async () => {
    try {
      const h = await voiceApi.health();
      setServiceMode(h.operational_mode);
      if (h.limits?.max_recording_seconds) setMaxRecordS(Math.min(h.limits.max_recording_seconds, 60));
      setUiState((prev) => (prev === "offline" ? "idle" : prev));
      setBanner((b) => (b?.retry ? null : b));
      return true;
    } catch {
      setServiceMode("offline");
      setUiState((prev) => (prev === "listening" || prev === "transcribing" || prev === "thinking" || prev === "speaking" ? prev : "offline"));
      return false;
    } finally {
      setHealthChecked(true);
    }
  }, []);

  useEffect(() => {
    void checkHealth();
    const t = setInterval(() => void checkHealth(), 15000);
    return () => clearInterval(t);
  }, [checkHealth]);

  const stopSpeaking = useCallback(() => {
    if (audioRef.current) {
      audioRef.current.pause();
      audioRef.current.currentTime = 0;
      audioRef.current = null;
    }
    setSpeaking(false);
    setUiState((prev) => (prev === "speaking" ? "idle" : prev));
  }, []);

  const releaseMic = useCallback(() => {
    streamRef.current?.getTracks().forEach((t) => t.stop());
    streamRef.current = null;
  }, []);

  // Leaving the page (or unmounting) stops playback and the microphone and never sends a recording.
  useEffect(
    () => () => {
      cancelRef.current = true;
      stopSpeaking();
      if (recorderRef.current && recorderRef.current.state !== "inactive") recorderRef.current.stop();
      releaseMic();
    },
    [stopSpeaking, releaseMic],
  );

  const playAudio = useCallback(
    async (turn: TurnRecord) => {
      if (!turn.audio_base64) return;
      stopSpeaking();
      const mime = turn.audio_mime_type || "audio/mpeg";
      const audio = new Audio(`data:${mime};base64,${turn.audio_base64}`);
      audioRef.current = audio;
      setSpeaking(true);
      setUiState("speaking");
      const done = () => {
        if (audioRef.current !== audio) return;
        audioRef.current = null;
        setSpeaking(false);
        setUiState("idle");
      };
      audio.onended = done;
      audio.onerror = () => {
        done();
        setBanner({ text: "Browser audio playback failed; read the grounded answer below." });
      };
      try {
        await audio.play();
      } catch {
        done();
        setBanner({ text: "Autoplay was blocked by the browser — press ▶ Play on the answer, or read it below." });
      }
    },
    [stopSpeaking],
  );

  const autoPlay = useCallback(
    async (turn: TurnRecord) => {
      // Prevent duplicate playback across React rerenders
      if (lastPlayedTurnIdRef.current === turn.turnId) return;
      lastPlayedTurnIdRef.current = turn.turnId;
      if (muted || !turn.audio_base64) {
        setUiState("idle");
        return;
      }
      await playAudio(turn);
    },
    [muted, playAudio],
  );

  const recordResponse = useCallback(
    async (res: VoiceTurnResponse) => {
      const turnId = `${Date.now()}-${Math.random().toString(36).slice(2, 7)}`;
      const record: TurnRecord = { ...res, turnId, at: new Date().toISOString() };
      setTurns((prev) => [record, ...prev].slice(0, 20));
      if (res.delivery_mode === "full_speech") setServiceMode("full_speech");
      else if (res.delivery_mode === "text_only") setServiceMode((m) => (m === "offline" ? "text_only" : m));
      if (res.warnings && res.warnings.length > 0) setBanner({ text: res.warnings.join(" · ") });
      else if (res.delivery_mode === "backend_unavailable" || res.delivery_mode === "stt_failed") setBanner({ text: res.display_text });
      else setBanner(null);
      await autoPlay(record);
    },
    [autoPlay],
  );

  const goOffline = useCallback((err: unknown) => {
    setServiceMode("offline");
    setUiState("offline");
    const msg = err instanceof VoiceServiceError ? err.detail : "Voice service unreachable.";
    setBanner({ text: `${msg} — the rest of the Command Center is unaffected.`, retry: true });
  }, []);

  const submitText = useCallback(
    async (q: string) => {
      const trimmed = q.trim();
      if (!trimmed) return;
      stopSpeaking();
      setBanner(null);
      setExpanded(true);
      setUiState("thinking");
      try {
        const res = await voiceApi.query(sessionIdRef.current, trimmed, !muted, scenarioId);
        await recordResponse(res);
      } catch (err) {
        goOffline(err);
      }
    },
    [muted, recordResponse, scenarioId, stopSpeaking, goOffline],
  );

  const stopRecording = useCallback((cancel: boolean) => {
    cancelRef.current = cancel;
    if (recorderRef.current && recorderRef.current.state !== "inactive") recorderRef.current.stop();
  }, []);

  const startRecording = useCallback(async () => {
    stopSpeaking();
    setBanner(null);
    setExpanded(true);
    const status = currentMicStatus(serviceMode);
    setMic(status);
    if (status.kind !== "ready") {
      setUiState(status.kind === "service-offline" ? "offline" : "error");
      setBanner({ text: status.help, retry: status.kind === "service-offline" });
      return;
    }

    let stream: MediaStream;
    try {
      stream = await navigator.mediaDevices.getUserMedia({ audio: { echoCancellation: true, noiseSuppression: true } });
    } catch (err) {
      setRecording(false);
      setUiState("error");
      setBanner({ text: micErrorCopy(err) });
      return;
    }
    if (!mountedRef.current) {
      stream.getTracks().forEach((t) => t.stop());
      return;
    }
    streamRef.current = stream;
    const mime = pickSupportedAudioMime();
    let recorder: MediaRecorder;
    try {
      recorder = mime ? new MediaRecorder(stream, { mimeType: mime }) : new MediaRecorder(stream);
    } catch (err) {
      releaseMic();
      setUiState("error");
      setBanner({ text: micErrorCopy(err) });
      return;
    }
    recorderRef.current = recorder;
    chunksRef.current = [];
    cancelRef.current = false;
    recordStartRef.current = Date.now();
    setElapsed(0);
    setRecording(true);
    setUiState("listening");

    recorder.ondataavailable = (e) => {
      if (e.data && e.data.size > 0) chunksRef.current.push(e.data);
    };

    recorder.onstop = () => {
      releaseMic();
      recorderRef.current = null;
      if (!mountedRef.current) return; // left the page: never send
      setRecording(false);
      if (cancelRef.current) {
        cancelRef.current = false;
        setUiState("idle");
        setBanner({ text: "Recording cancelled — nothing was sent." });
        return;
      }
      const durationS = Math.max(0.2, (Date.now() - recordStartRef.current) / 1000.0);
      const actualMime = recorder.mimeType || mime || "audio/webm";
      const blob = new Blob(chunksRef.current, { type: actualMime });
      if (blob.size < 64) {
        setUiState("idle");
        setBanner({ text: "Recording was too short. Press 🎙 Ask, speak, then Stop & Send — or type your question." });
        return;
      }
      setUiState("transcribing");
      const reader = new FileReader();
      reader.onloadend = async () => {
        const dataUrl = String(reader.result || "");
        const b64 = dataUrl.includes(",") ? dataUrl.split(",")[1] : "";
        const filename = actualMime.includes("mp4") || actualMime.includes("m4a") ? "ptt.m4a" : "ptt.webm";
        try {
          const res = await voiceApi.audioTurn(sessionIdRef.current, b64, actualMime, filename, durationS, !muted, scenarioId);
          if (mountedRef.current) await recordResponse(res);
        } catch (err) {
          if (mountedRef.current) goOffline(err);
        }
      };
      reader.readAsDataURL(blob);
    };

    recorder.start();
  }, [muted, recordResponse, scenarioId, serviceMode, stopSpeaking, releaseMic, goOffline]);

  // Recording timer + automatic stop at the voice service's limit; Escape cancels.
  useEffect(() => {
    if (!recording) return;
    const t = setInterval(() => {
      const s = (Date.now() - recordStartRef.current) / 1000;
      setElapsed(s);
      if (s >= maxRecordS) stopRecording(false);
    }, 250);
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") {
        e.stopPropagation();
        stopRecording(true);
      }
    };
    window.addEventListener("keydown", onKey, true);
    return () => {
      clearInterval(t);
      window.removeEventListener("keydown", onKey, true);
    };
  }, [recording, maxRecordS, stopRecording]);

  const clearContext = useCallback(async () => {
    stopSpeaking();
    setTurns([]);
    setOpenDetails({});
    setBanner(null);
    try {
      await voiceApi.resetSession(sessionIdRef.current);
    } catch {
      /* ignore if offline */
    }
  }, [stopSpeaking]);

  const latestTurn = turns[0] ?? null;
  const output = speechOutput(serviceMode, muted);
  const micReady = mic.kind === "ready";

  const statusMeta: Record<UiState, { label: string; color: string }> = {
    idle: { label: serviceMode === "full_speech" ? "VOICE READY" : "TEXT MODE", color: "var(--ok)" },
    listening: { label: `LISTENING ${formatElapsed(elapsed)}`, color: "var(--danger)" },
    transcribing: { label: "TRANSCRIBING…", color: "var(--warn)" },
    thinking: { label: "THINKING…", color: "var(--warn)" },
    speaking: { label: "SPEAKING…", color: "var(--accent)" },
    offline: { label: healthChecked ? "VOICE OFFLINE" : "CONNECTING…", color: "var(--muted)" },
    error: { label: "MIC UNAVAILABLE", color: "var(--warn)" },
  };
  const busyState = uiState === "listening" || uiState === "transcribing" || uiState === "thinking" || uiState === "speaking";

  return (
    <section
      ref={sectionRef}
      className="panel relative flex shrink-0 flex-col border-l-2 border-l-accent"
      aria-label="Ask AEGIS read-only voice assistant"
      title="Ask AEGIS answers questions about the incident. It does not talk to responders — use Comms for that."
      data-testid="ask-aegis-panel"
      data-state={uiState}
      data-service={serviceMode}
    >
      {/* Compact header bar — always visible */}
      <header className="flex flex-wrap items-center gap-1.5 border-b border-line px-2.5 py-1.5">
        <button
          type="button"
          onClick={() => setExpanded((v) => !v)}
          className="flex items-center gap-1.5 text-left"
          aria-expanded={expanded}
          title="Toggle Ask AEGIS situational-awareness assistant"
        >
          <span className="label !text-accent">{expanded ? "▾" : "▸"} Ask AEGIS</span>
        </button>
        <Badge tone="neutral">read-only</Badge>
        <span
          className="mono flex items-center gap-1 text-[9.5px] font-semibold tracking-wider"
          style={{ color: statusMeta[uiState].color }}
          data-testid="ask-aegis-status"
          role="status"
          aria-live="polite"
        >
          <span className={`h-1.5 w-1.5 rounded-full ${busyState ? "blink" : ""}`} style={{ background: statusMeta[uiState].color }} />
          {statusMeta[uiState].label}
        </span>

        <div className="ml-auto flex items-center gap-1">
          {recording ? (
            <>
              <button
                type="button"
                onClick={() => stopRecording(false)}
                data-testid="ask-aegis-ptt"
                className="mono rounded border border-danger bg-danger px-2 py-0.5 text-[10px] font-bold uppercase tracking-wider text-white"
                title={`Stop and send the question (auto-sends at ${maxRecordS}s)`}
              >
                ● Stop &amp; Send
              </button>
              <button
                type="button"
                onClick={() => stopRecording(true)}
                data-testid="ask-aegis-cancel"
                className="mono rounded border border-line-strong px-1.5 py-0.5 text-[10px] text-muted hover:text-text"
                title="Discard this recording (Esc)"
                aria-label="Cancel recording"
              >
                ✕
              </button>
            </>
          ) : (
            <button
              type="button"
              onClick={() => void startRecording()}
              data-testid="ask-aegis-ptt"
              data-mic={mic.kind}
              aria-disabled={!micReady}
              disabled={uiState === "transcribing" || uiState === "thinking"}
              className={`mono rounded border px-2 py-0.5 text-[10px] font-bold uppercase tracking-wider transition disabled:opacity-50 ${
                micReady ? "border-accent/60 bg-accent/10 text-accent hover:bg-accent/20" : "border-line-strong text-faint hover:text-muted"
              }`}
              title={micReady ? "Ask AEGIS by voice (read-only situational awareness)" : `${mic.short} — click for how to fix`}
            >
              {micReady ? "🎙 Ask" : "🎙 Mic off"}
            </button>
          )}

          {speaking && (
            <button
              type="button"
              onClick={stopSpeaking}
              data-testid="ask-aegis-stop"
              className="mono rounded border border-warn/60 px-1.5 py-0.5 text-[10px] text-warn hover:bg-warn/10"
              title="Stop spoken response immediately"
            >
              ■ Stop
            </button>
          )}

          <button
            type="button"
            onClick={() => {
              setMuted((m) => {
                const next = !m;
                if (next) stopSpeaking();
                return next;
              });
            }}
            data-testid="ask-aegis-mute"
            aria-pressed={muted}
            className={`mono rounded border px-1.5 py-0.5 text-[10px] ${muted ? "border-warn/60 text-warn" : "border-line-strong text-muted hover:text-text"}`}
            title={output.title}
          >
            {output.label}
          </button>

          <button
            type="button"
            onClick={() => setExpanded((v) => !v)}
            className="mono rounded border border-line-strong px-1.5 py-0.5 text-[10px] text-muted hover:text-text"
            aria-label={expanded ? "Collapse Ask AEGIS panel" : "Expand Ask AEGIS panel"}
          >
            {expanded ? "Hide" : "Open"}
          </button>
        </div>
      </header>

      {/* Compact latest-answer preview when collapsed */}
      {!expanded && latestTurn && (
        <div onClick={() => setExpanded(true)} className="cursor-pointer border-b border-line bg-panel-2 px-2.5 py-1 text-[11px] hover:bg-panel">
          <span className="mono text-[10px] text-accent">AEGIS: </span>
          <span className="text-text">{latestTurn.spoken_text}</span>
        </div>
      )}

      {/* Expandable body */}
      {expanded && (
        <div
          data-testid="ask-aegis-sheet"
          role="region"
          aria-label="Ask AEGIS conversation"
          onKeyDown={(e) => {
            if (e.key === "Escape" && !recording) {
              e.stopPropagation();
              setExpanded(false);
            }
          }}
          style={{ maxHeight: sheetMax }}
          className="scrollbar-thin absolute inset-x-0 top-full z-20 mt-1 flex flex-col gap-1.5 overflow-y-auto rounded border border-accent/50 bg-panel p-2 shadow-lg"
        >
          {/* What works here, before the operator tries */}
          <p className="mono text-[9.5px] leading-snug text-faint" data-testid="ask-aegis-capabilities">
            <span className={serviceMode === "offline" ? "text-warn" : "text-ok"}>Typed {serviceMode === "offline" ? "✕" : "✓"}</span>
            {" · "}
            <span className={micReady ? "text-ok" : "text-warn"}>{micReady ? "Voice input ✓" : mic.short}</span>
            {" · "}
            <span className={output.available && !muted ? "text-ok" : ""}>{output.available ? (muted ? "Spoken answers muted" : "Spoken answers ✓") : "Spoken answers off (text only)"}</span>
          </p>

          {banner && (
            <div role="status" data-testid="ask-aegis-banner" className="flex items-start gap-2 rounded border border-warn/50 bg-tone-warn px-2 py-1 text-[10.5px] text-warn">
              <span className="min-w-0 flex-1">{banner.text}</span>
              {banner.retry && (
                <button
                  type="button"
                  onClick={() => void checkHealth()}
                  data-testid="ask-aegis-retry"
                  className="mono shrink-0 rounded border border-warn/60 px-1.5 text-[10px] hover:bg-warn/10"
                >
                  Retry
                </button>
              )}
            </div>
          )}

          {/* Quick situational-awareness chips */}
          <div className="flex flex-wrap gap-1">
            {QUICK_PROMPTS.map((p) => (
              <button
                key={p.label}
                type="button"
                onClick={() => void submitText(p.query)}
                className="rounded border border-line-strong bg-panel-2 px-1.5 py-0.5 text-[10px] text-muted hover:border-accent hover:text-accent"
              >
                {p.label}
              </button>
            ))}
            {turns.length > 0 && (
              <button
                type="button"
                onClick={() => void clearContext()}
                className="ml-auto rounded border border-line-strong px-1.5 py-0.5 text-[9.5px] text-faint hover:text-text"
                title="Clear conversation context (does not affect simulation state)"
              >
                Reset chat
              </button>
            )}
          </div>

          {/* Typed question — always available */}
          <form
            className="flex gap-1"
            onSubmit={(e) => {
              e.preventDefault();
              if (!text.trim()) return;
              const q = text;
              setText("");
              void submitText(q);
            }}
          >
            <input
              value={text}
              onChange={(e) => setText(e.target.value)}
              maxLength={500}
              data-testid="ask-aegis-input"
              aria-label="Type a question for Ask AEGIS"
              placeholder="Ask AEGIS (e.g. 'What's our situation?', 'Which responder?')…"
              className="min-w-0 flex-1 rounded border border-line-strong bg-bg px-2 py-1 text-[11px] outline-none focus:border-accent"
            />
            <button
              type="submit"
              data-testid="ask-aegis-send"
              disabled={uiState === "thinking" || uiState === "transcribing"}
              className="rounded border border-accent/60 px-2 py-1 text-[11px] font-semibold text-accent hover:bg-accent/10 disabled:opacity-50"
            >
              Ask
            </button>
          </form>

          {/* Conversation turns: short spoken transcript + expandable detailed response */}
          {turns.length === 0 ? (
            <p className="text-[10.5px] text-faint">
              SIMULATION ONLY · Read-only situational awareness. Ask about incident status, optimization rationale, responder positions,
              unassigned victims, or acknowledgments. Ask AEGIS never approves, dispatches or changes anything.
            </p>
          ) : (
            <div className="space-y-1.5">
              {turns.map((t) => {
                const detailOpen = !!openDetails[t.turnId];
                const hasMoreDetail = t.display_text !== t.spoken_text;
                return (
                  <div key={t.turnId} data-testid="ask-aegis-turn" className="rounded border border-line bg-panel-2 px-2 py-1.5 text-[11px]">
                    <div className="flex items-baseline justify-between gap-2">
                      <span className="font-semibold text-accent" data-testid="ask-aegis-question">
                        You: {t.user_transcript || "(spoken audio)"}
                      </span>
                      <span className="flex shrink-0 items-center gap-1.5">
                        {t.audio_base64 && (
                          <button
                            type="button"
                            onClick={() => void playAudio(t)}
                            data-testid="ask-aegis-play"
                            className="mono rounded border border-accent/50 px-1 text-[9.5px] text-accent hover:bg-accent/10"
                            title="Play this answer"
                          >
                            ▶ Play
                          </button>
                        )}
                        <span className="mono text-[9.5px] text-faint">{t.intent}</span>
                      </span>
                    </div>
                    <p className="mt-0.5 text-[11.5px] font-medium text-text" data-testid="ask-aegis-spoken">
                      {t.spoken_text}
                    </p>
                    {(hasMoreDetail || t.trace_fields.length > 0) && (
                      <div className="mt-1">
                        <button
                          type="button"
                          onClick={() => setOpenDetails((prev) => ({ ...prev, [t.turnId]: !prev[t.turnId] }))}
                          data-testid="ask-aegis-toggle-detail"
                          className="mono text-[9.5px] text-accent hover:underline"
                        >
                          {detailOpen ? "▾ Hide full detail & provenance" : "▸ Show full detail & provenance"}
                        </button>
                        {detailOpen && (
                          <div data-testid="ask-aegis-detail" className="mt-1 space-y-1 border-t border-line pt-1 text-[10.5px] text-muted">
                            <p>{t.display_text}</p>
                            {t.trace_fields.length > 0 && <p className="mono text-[9px] text-faint">Sources: {t.trace_fields.join(", ")}</p>}
                          </div>
                        )}
                      </div>
                    )}
                  </div>
                );
              })}
            </div>
          )}
        </div>
      )}
    </section>
  );
}
