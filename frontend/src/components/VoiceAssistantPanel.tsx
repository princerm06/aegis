"use client";

import { useCallback, useEffect, useLayoutEffect, useRef, useState } from "react";
import { voiceApi, VoiceServiceError, type VoiceTurnResponse } from "@/lib/voiceApi";
import { Badge } from "./ui";

type UiState = "idle" | "listening" | "thinking" | "speaking" | "offline" | "error";

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
 * - Responder Comms sends one-way radio/TTS instructions TO a firefighter.
 * - Ask AEGIS answers the operator's questions ABOUT live simulation state
 *   using the standalone read-only AEGIS Voice service (`port 8010`).
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
  const [serviceMode, setServiceMode] = useState<"full_speech" | "text_only" | "offline">("offline");
  const [muted, setMuted] = useState(false);
  const [speaking, setSpeaking] = useState(false);
  const [recording, setRecording] = useState(false);
  const [text, setText] = useState("");
  const [turns, setTurns] = useState<TurnRecord[]>([]);
  const [openDetails, setOpenDetails] = useState<Record<string, boolean>>({});
  const [banner, setBanner] = useState<string | null>(null);

  const sessionIdRef = useRef<string>("cmd-voice-default");
  const audioRef = useRef<HTMLAudioElement | null>(null);
  const lastPlayedTurnIdRef = useRef<string | null>(null);
  const recorderRef = useRef<MediaRecorder | null>(null);
  const chunksRef = useRef<Blob[]>([]);
  const recordStartRef = useRef<number>(0);

  useEffect(() => {
    sessionIdRef.current = `cmd-${Math.random().toString(36).slice(2, 10)}`;
  }, []);

  const checkHealth = useCallback(async () => {
    try {
      const h = await voiceApi.health();
      setServiceMode(h.operational_mode);
      setUiState((prev) => (prev === "offline" ? "idle" : prev));
    } catch {
      setServiceMode("offline");
      setUiState((prev) => (prev === "listening" || prev === "thinking" || prev === "speaking" ? prev : "offline"));
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

  useEffect(
    () => () => {
      stopSpeaking();
      if (recorderRef.current && recorderRef.current.state !== "inactive") {
        recorderRef.current.stop();
      }
    },
    [stopSpeaking],
  );

  const playTurnAudio = useCallback(
    async (turn: TurnRecord) => {
      // Prevent duplicate playback across React rerenders
      if (lastPlayedTurnIdRef.current === turn.turnId) return;
      lastPlayedTurnIdRef.current = turn.turnId;

      if (muted || !turn.audio_base64) {
        setUiState("idle");
        return;
      }
      stopSpeaking();
      const mime = turn.audio_mime_type || "audio/mpeg";
      const audio = new Audio(`data:${mime};base64,${turn.audio_base64}`);
      audioRef.current = audio;
      setSpeaking(true);
      setUiState("speaking");

      audio.onended = () => {
        setSpeaking(false);
        setUiState("idle");
      };
      audio.onerror = () => {
        setSpeaking(false);
        setUiState("idle");
        setBanner("Browser audio playback failed; read the grounded transcript below.");
      };
      try {
        await audio.play();
      } catch {
        setSpeaking(false);
        setUiState("idle");
        setBanner("Autoplay blocked by browser policy; tap Play or read the transcript below.");
      }
    },
    [muted, stopSpeaking],
  );

  const recordResponse = useCallback(
    async (res: VoiceTurnResponse) => {
      const turnId = `${Date.now()}-${Math.random().toString(36).slice(2, 7)}`;
      const record: TurnRecord = {
        ...res,
        turnId,
        at: new Date().toISOString(),
      };
      setTurns((prev) => [record, ...prev].slice(0, 20));
      if (res.delivery_mode === "full_speech") {
        setServiceMode("full_speech");
      } else if (res.delivery_mode === "text_only") {
        setServiceMode((m) => (m === "offline" ? "text_only" : m));
      }
      if (res.warnings && res.warnings.length > 0) {
        setBanner(res.warnings.join(" · "));
      } else if (res.delivery_mode === "backend_unavailable" || res.delivery_mode === "stt_failed") {
        setBanner(res.display_text);
      } else {
        setBanner(null);
      }
      await playTurnAudio(record);
    },
    [playTurnAudio],
  );

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
        setServiceMode("offline");
        setUiState("offline");
        const msg = err instanceof VoiceServiceError ? err.detail : "Voice service unreachable on port 8010.";
        setBanner(`${msg} — Command Center remains fully operational.`);
      }
    },
    [muted, recordResponse, scenarioId, stopSpeaking],
  );

  const toggleRecording = useCallback(async () => {
    if (recording) {
      if (recorderRef.current && recorderRef.current.state !== "inactive") {
        recorderRef.current.stop();
      }
      return;
    }

    stopSpeaking();
    setBanner(null);
    setExpanded(true);

    if (typeof navigator === "undefined" || !navigator.mediaDevices || !navigator.mediaDevices.getUserMedia) {
      setUiState("error");
      setBanner("Microphone requires localhost or HTTPS. Use the typed question input below.");
      return;
    }

    try {
      const stream = await navigator.mediaDevices.getUserMedia({
        audio: { echoCancellation: true, noiseSuppression: true },
      });
      const mime = pickSupportedAudioMime();
      const recorder = mime ? new MediaRecorder(stream, { mimeType: mime }) : new MediaRecorder(stream);
      recorderRef.current = recorder;
      chunksRef.current = [];
      recordStartRef.current = Date.now();
      setRecording(true);
      setUiState("listening");

      recorder.ondataavailable = (e) => {
        if (e.data && e.data.size > 0) chunksRef.current.push(e.data);
      };

      recorder.onstop = () => {
        setRecording(false);
        stream.getTracks().forEach((t) => t.stop());
        const durationS = Math.max(0.2, (Date.now() - recordStartRef.current) / 1000.0);
        const actualMime = recorder.mimeType || mime || "audio/webm";
        const blob = new Blob(chunksRef.current, { type: actualMime });

        if (blob.size < 64) {
          setUiState("idle");
          setBanner("Recording was too short. Hold or tap Talk while speaking, or type your question.");
          return;
        }

        setUiState("thinking");
        const reader = new FileReader();
        reader.onloadend = async () => {
          const dataUrl = String(reader.result || "");
          const b64 = dataUrl.includes(",") ? dataUrl.split(",")[1] : "";
          const filename = actualMime.includes("mp4") || actualMime.includes("m4a") ? "ptt.m4a" : "ptt.webm";
          try {
            const res = await voiceApi.audioTurn(
              sessionIdRef.current,
              b64,
              actualMime,
              filename,
              durationS,
              !muted,
              scenarioId,
            );
            await recordResponse(res);
          } catch (err) {
            setServiceMode("offline");
            setUiState("offline");
            const msg = err instanceof VoiceServiceError ? err.detail : "Voice service unreachable on port 8010.";
            setBanner(`${msg} — Command Center remains fully operational.`);
          }
        };
        reader.readAsDataURL(blob);
      };

      recorder.start();
    } catch {
      setRecording(false);
      setUiState("error");
      setBanner("Microphone permission denied or unavailable. Type your question below.");
    }
  }, [muted, recordResponse, recording, scenarioId, stopSpeaking]);

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

  const statusMeta: Record<UiState, { label: string; color: string }> = {
    idle: { label: serviceMode === "full_speech" ? "VOICE READY" : "TEXT MODE", color: "var(--ok)" },
    listening: { label: "LISTENING…", color: "var(--danger)" },
    thinking: { label: "THINKING…", color: "var(--warn)" },
    speaking: { label: "SPEAKING…", color: "var(--accent)" },
    offline: { label: "VOICE OFFLINE", color: "var(--muted)" },
    error: { label: "MIC / API NOTE", color: "var(--warn)" },
  };

  return (
    <section
      ref={sectionRef}
      className="panel relative flex shrink-0 flex-col border-l-2 border-l-accent"
      aria-label="Ask AEGIS read-only voice assistant"
      data-testid="ask-aegis-panel"
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
        <Badge tone="neutral">read-only · sim</Badge>
        <span
          className="mono flex items-center gap-1 text-[9.5px] font-semibold tracking-wider"
          style={{ color: statusMeta[uiState].color }}
        >
          <span
            className={`h-1.5 w-1.5 rounded-full ${uiState === "listening" || uiState === "thinking" || uiState === "speaking" ? "blink" : ""}`}
            style={{ background: statusMeta[uiState].color }}
          />
          {statusMeta[uiState].label}
        </span>

        <div className="ml-auto flex items-center gap-1">
          <button
            type="button"
            onClick={() => void toggleRecording()}
            data-testid="ask-aegis-ptt"
            className={`mono rounded border px-2 py-0.5 text-[10px] font-bold uppercase tracking-wider transition ${
              recording
                ? "border-danger bg-danger text-white"
                : "border-accent/60 bg-accent/10 text-accent hover:bg-accent/20"
            }`}
            title="Push-to-talk to Ask AEGIS (read-only situational awareness)"
          >
            {recording ? "● Stop & Send" : "🎙 Ask"}
          </button>

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
            className={`mono rounded border px-1.5 py-0.5 text-[10px] ${
              muted ? "border-warn/60 text-warn" : "border-line-strong text-muted hover:text-text"
            }`}
            title={muted ? "Voice audio muted (text-only responses)" : "Spoken ElevenLabs audio enabled"}
          >
            {muted ? "Muted" : "🔊 On"}
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
        <div
          onClick={() => setExpanded(true)}
          className="cursor-pointer border-b border-line bg-panel-2 px-2.5 py-1 text-[11px] hover:bg-panel"
        >
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
            if (e.key === "Escape") {
              e.stopPropagation();
              setExpanded(false);
            }
          }}
          style={{ maxHeight: sheetMax }}
          className="scrollbar-thin absolute inset-x-0 top-full z-20 mt-1 flex flex-col gap-1.5 overflow-y-auto rounded border border-accent/50 bg-panel p-2 shadow-lg"
        >
          {banner && (
            <div
              role="status"
              data-testid="ask-aegis-banner"
              className="rounded border border-warn/50 bg-tone-warn px-2 py-1 text-[10.5px] text-warn"
            >
              {banner}
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

          {/* Typed question fallback */}
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
              placeholder="Ask AEGIS (e.g. 'What's our situation?', 'Which responder?')…"
              className="min-w-0 flex-1 rounded border border-line-strong bg-bg px-2 py-1 text-[11px] outline-none focus:border-accent"
            />
            <button
              type="submit"
              data-testid="ask-aegis-send"
              className="rounded border border-accent/60 px-2 py-1 text-[11px] font-semibold text-accent hover:bg-accent/10"
            >
              Ask
            </button>
          </form>

          {/* Conversation turns: short spoken transcript + expandable detailed response */}
          {turns.length === 0 ? (
            <p className="text-[10.5px] text-faint">
              SIMULATION ONLY · Read-only situational awareness. Ask about incident status, optimization rationale,
              responder positions, unassigned victims, or acknowledgments.
            </p>
          ) : (
            <div className="space-y-1.5">
              {turns.map((t) => {
                const detailOpen = !!openDetails[t.turnId];
                const hasMoreDetail = t.display_text !== t.spoken_text;
                return (
                  <div
                    key={t.turnId}
                    data-testid="ask-aegis-turn"
                    className="rounded border border-line bg-panel-2 px-2 py-1.5 text-[11px]"
                  >
                    <div className="flex items-baseline justify-between gap-2">
                      <span className="font-semibold text-accent">You: {t.user_transcript || "(spoken audio)"}</span>
                      <span className="mono shrink-0 text-[9.5px] text-faint">{t.intent}</span>
                    </div>
                    <p className="mt-0.5 text-[11.5px] font-medium text-text" data-testid="ask-aegis-spoken">
                      {t.spoken_text}
                    </p>
                    {(hasMoreDetail || t.trace_fields.length > 0) && (
                      <div className="mt-1">
                        <button
                          type="button"
                          onClick={() =>
                            setOpenDetails((prev) => ({
                              ...prev,
                              [t.turnId]: !prev[t.turnId],
                            }))
                          }
                          data-testid="ask-aegis-toggle-detail"
                          className="mono text-[9.5px] text-accent hover:underline"
                        >
                          {detailOpen ? "▾ Hide full detail & provenance" : "▸ Show full detail & provenance"}
                        </button>
                        {detailOpen && (
                          <div
                            data-testid="ask-aegis-detail"
                            className="mt-1 space-y-1 border-t border-line pt-1 text-[10.5px] text-muted"
                          >
                            <p>{t.display_text}</p>
                            {t.trace_fields.length > 0 && (
                              <p className="mono text-[9px] text-faint">Sources: {t.trace_fields.join(", ")}</p>
                            )}
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
