"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { api } from "@/lib/api";
import { responderName } from "@/lib/format";
import { assessMediaSupport, readMediaEnvironment } from "@/lib/mediaCapabilities";
import { MIC, POLL_MS, fetchRtcConfig, responderStep, talkApi, talkErrorText, waitForIce } from "@/lib/talk";
import type { SimulationState } from "@/lib/types";

type Phase = "pick" | "ready" | "waiting" | "connecting" | "talking";

const talk = talkApi();
const SAVED = "aegis.phone.responder";

type WakeLockSentinelLike = { release: () => Promise<void> };
type WakeLockNavigator = Navigator & { wakeLock?: { request: (type: "screen") => Promise<WakeLockSentinelLike> } };

/**
 * The responder's phone: open once, tap Join, keep it open. It answers the
 * operator's Talk by itself, hands-free, and keeps the screen awake.
 */
export default function ResponderPhonePage() {
  const [state, setState] = useState<SimulationState | null>(null);
  const [responderId, setResponderId] = useState<string | null>(null);
  const [phase, setPhase] = useState<Phase>("pick");
  const [joined, setJoined] = useState(false);
  const [muted, setMuted] = useState(false);
  const [awake, setAwake] = useState<boolean | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [secure, setSecure] = useState(true);
  const audioRef = useRef<HTMLAudioElement>(null);
  const micRef = useRef<MediaStream | null>(null);
  const pcRef = useRef<RTCPeerConnection | null>(null);
  const sessionRef = useRef<string | null>(null);
  const wakeRef = useRef<WakeLockSentinelLike | null>(null);
  const joinedRef = useRef(false);

  useEffect(() => {
    setSecure(assessMediaSupport(readMediaEnvironment()).kind === "none");
    const fromUrl = new URLSearchParams(window.location.search).get("id");
    const id = fromUrl || window.localStorage.getItem(SAVED);
    if (id) {
      setResponderId(id);
      setPhase("ready");
    }
    api.state().then(setState).catch(() => setState(null));
  }, []);

  const closeCall = useCallback(() => {
    pcRef.current?.close();
    pcRef.current = null;
    sessionRef.current = null;
    if (audioRef.current) audioRef.current.srcObject = null;
    if (joinedRef.current) setPhase("waiting");
  }, []);

  const keepAwake = useCallback(async () => {
    const wl = (navigator as WakeLockNavigator).wakeLock;
    if (!wl) return setAwake(false);
    try {
      wakeRef.current = await wl.request("screen");
      setAwake(true);
    } catch {
      setAwake(false);
    }
  }, []);

  const answer = useCallback(
    async (channel: string, sessionId: string, offer: string) => {
      closeCall();
      sessionRef.current = sessionId;
      setPhase("connecting");
      const mic = micRef.current;
      if (!mic) return;
      const config = await fetchRtcConfig();
      if (sessionRef.current !== sessionId) return;
      const pc = new RTCPeerConnection(config);
      pcRef.current = pc;
      mic.getAudioTracks().forEach((t) => pc.addTrack(t, mic));
      pc.ontrack = (e) => {
        if (!audioRef.current) return;
        audioRef.current.srcObject = e.streams[0] ?? new MediaStream([e.track]);
        void audioRef.current.play().catch(() => {});
      };
      pc.onconnectionstatechange = () => {
        if (pcRef.current !== pc) return;
        if (pc.connectionState === "connected") setPhase("talking");
        else if (pc.connectionState === "failed") {
          setError("Audio couldn't connect. Is this phone on the AEGIS network (hotspot or Tailscale)?");
          closeCall();
        }
      };
      await pc.setRemoteDescription({ type: "offer", sdp: offer });
      await pc.setLocalDescription(await pc.createAnswer());
      await waitForIce(pc);
      if (pcRef.current !== pc) return;
      await talk.answer(channel, sessionId, pc.localDescription?.sdp ?? "");
    },
    [closeCall],
  );

  // Heartbeat while joined: tells the dashboard this phone is ready and picks up calls.
  useEffect(() => {
    if (!responderId || !joined) return;
    let stopped = false;
    let busy = false;
    const tick = async () => {
      if (busy) return;
      busy = true;
      try {
        const view = await talk.heartbeat(responderId);
        if (stopped) return;
        setError(null);
        const step = responderStep(sessionRef.current, view);
        if (step === "answer" && view.session?.offer) await answer(responderId, view.session.session_id, view.session.offer);
        else if (step === "close") closeCall();
      } catch (e) {
        if (!stopped) setError(talkErrorText(e));
      } finally {
        busy = false;
      }
    };
    void tick();
    const t = setInterval(tick, POLL_MS);
    return () => {
      stopped = true;
      clearInterval(t);
    };
  }, [responderId, joined, answer, closeCall]);

  useEffect(() => {
    const again = () => {
      if (document.visibilityState === "visible" && joinedRef.current) void keepAwake();
    };
    document.addEventListener("visibilitychange", again);
    return () => document.removeEventListener("visibilitychange", again);
  }, [keepAwake]);

  useEffect(
    () => () => {
      pcRef.current?.close();
      micRef.current?.getTracks().forEach((t) => t.stop());
      void wakeRef.current?.release().catch(() => {});
    },
    [],
  );

  const join = async () => {
    if (!responderId) return;
    setError(null);
    try {
      micRef.current = await navigator.mediaDevices.getUserMedia({ audio: MIC });
      window.localStorage.setItem(SAVED, responderId);
      joinedRef.current = true;
      setJoined(true);
      setPhase("waiting");
      void keepAwake();
    } catch (e) {
      setError(talkErrorText(e));
    }
  };

  const leave = () => {
    const session = sessionRef.current;
    if (responderId && session) void talk.hangup(responderId, session, "responder").catch(() => {});
    joinedRef.current = false;
    setJoined(false);
    closeCall();
    micRef.current?.getTracks().forEach((t) => t.stop());
    micRef.current = null;
    void wakeRef.current?.release().catch(() => {});
    wakeRef.current = null;
    setMuted(false);
    setPhase("ready");
  };

  const toggleMute = () => {
    const next = !muted;
    micRef.current?.getAudioTracks().forEach((t) => (t.enabled = !next));
    setMuted(next);
  };

  const name = responderId ? (state ? responderName(state, responderId) : responderId) : "";
  const responders = state?.scenario.responders ?? [];
  const status: Record<Phase, { text: string; color: string }> = {
    pick: { text: "Who are you?", color: "var(--muted)" },
    ready: { text: "Tap Join to connect to the operator", color: "var(--muted)" },
    waiting: { text: "Connected · the operator can talk to you", color: "var(--accent)" },
    connecting: { text: "Operator calling…", color: "var(--warn)" },
    talking: { text: "● Talking with the operator", color: "var(--accent)" },
  };

  return (
    <main className="mx-auto flex min-h-dvh max-w-md flex-col gap-4 p-5">
      <audio ref={audioRef} autoPlay playsInline />
      <header className="flex items-center justify-between">
        <span className="mono text-sm font-bold tracking-widest">AEGIS · RESPONDER</span>
        <span className="mono rounded border border-warn/60 px-1.5 text-[10px] text-warn">SIMULATION</span>
      </header>

      {!secure && (
        <p className="rounded border border-danger/60 bg-danger/10 p-3 text-sm leading-snug">
          The microphone needs a secure address. Open this page with the <b>https://</b> link (Tailscale), not http://192.168…
        </p>
      )}

      {phase === "pick" || !responderId ? (
        <section className="flex flex-col gap-2">
          <p className="text-sm text-muted">Pick your responder:</p>
          {responders.length === 0 && <p className="text-sm text-faint">Loading responders…</p>}
          {responders.map((r) => (
            <button
              key={r.id}
              onClick={() => {
                setResponderId(r.id);
                setPhase("ready");
              }}
              className="rounded-lg border border-line-strong px-4 py-4 text-left text-lg font-semibold hover:border-accent"
            >
              {state ? responderName(state, r.id) : r.id}
            </button>
          ))}
        </section>
      ) : (
        <section className="flex flex-1 flex-col gap-4">
          <div className="flex items-baseline justify-between">
            <h1 className="text-3xl font-bold">{name}</h1>
            {phase === "ready" && (
              <button onClick={() => setPhase("pick")} className="text-sm text-muted underline">
                not you?
              </button>
            )}
          </div>
          <p className="text-lg font-semibold" style={{ color: status[phase].color }}>
            {status[phase].text}
          </p>

          {phase === "ready" ? (
            <button onClick={join} disabled={!secure} className="rounded-2xl bg-accent py-8 text-2xl font-bold text-black disabled:opacity-40">
              Join
            </button>
          ) : (
            <div className="grid grid-cols-2 gap-3">
              <button
                onClick={toggleMute}
                aria-pressed={muted}
                className={`rounded-2xl border py-8 text-xl font-bold ${muted ? "border-warn bg-warn/20 text-warn" : "border-line-strong"}`}
              >
                {muted ? "Unmute" : "Mute"}
              </button>
              <button onClick={leave} className="rounded-2xl border border-danger py-8 text-xl font-bold text-danger">
                Leave
              </button>
            </div>
          )}

          {error && <p className="text-sm leading-snug text-danger">{error}</p>}

          <div className="mt-auto space-y-1 text-xs leading-snug text-faint">
            {phase !== "ready" && awake === false && (
              <p className="text-warn">Keep this screen on: Settings → Display &amp; Brightness → Auto-Lock → Never.</p>
            )}
            <p>Keep this page open. If the phone locks or you switch apps, the operator can&apos;t hear you. Earbuds work best.</p>
          </div>
        </section>
      )}
    </main>
  );
}
