"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { api } from "@/lib/api";
import { responderName } from "@/lib/format";
import { assessMediaSupport, readMediaEnvironment } from "@/lib/mediaCapabilities";
import {
  MIC,
  POLL_MS,
  RING_TIMEOUT_MS,
  fetchRtcConfig,
  isTalkAuthError,
  phoneTokenKey,
  readToken,
  responderStep,
  takeTokenFromUrl,
  talkApi,
  talkErrorText,
  waitForIce,
  writeToken,
} from "@/lib/talk";
import type { SimulationState } from "@/lib/types";

type Phase = "pick" | "ready" | "waiting" | "connecting" | "talking";

const SAVED = "aegis.phone.responder";
const LINK_EXPIRED = "This link has expired or was revoked. Ask the operator for a new one.";

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
  // This phone's credential for the responder channel (from the operator's link), per channel, this tab only.
  const tokenRef = useRef<string | null>(null);
  const [hasToken, setHasToken] = useState(false);
  const [expired, setExpired] = useState(false);
  // Informational line that survives heartbeats (errors are cleared by the next successful one).
  const [info, setInfo] = useState<string | null>(null);
  const talk = useRef(talkApi(undefined, () => tokenRef.current)).current;
  // Calls that never connected (e.g. the operator's browser vanished mid-ring): don't answer them again.
  const declinedRef = useRef<Set<string>>(new Set());
  const connectTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  useEffect(() => {
    setSecure(assessMediaSupport(readMediaEnvironment()).kind === "none");
    const fromUrl = new URLSearchParams(window.location.search).get("id");
    const id = fromUrl || window.localStorage.getItem(SAVED);
    // The link carries the token in its #fragment: keep it in sessionStorage and remove it from the address bar.
    const { token, cleaned } = takeTokenFromUrl(window.location.href);
    if (token && id) {
      writeToken(window.sessionStorage, phoneTokenKey(id), token);
      window.history.replaceState(null, "", cleaned);
    }
    const onHash = () => {
      const cur = new URLSearchParams(window.location.search).get("id");
      const t = takeTokenFromUrl(window.location.href);
      if (!t.token || !cur) return;
      writeToken(window.sessionStorage, phoneTokenKey(cur), t.token);
      window.history.replaceState(null, "", t.cleaned);
      tokenRef.current = t.token;
      setHasToken(true);
      setExpired(false);
      setError(null);
      setResponderId(cur);
      setPhase((p) => (p === "pick" ? "ready" : p));
    };
    window.addEventListener("hashchange", onHash);
    if (id) {
      tokenRef.current = readToken(window.sessionStorage, phoneTokenKey(id));
      setHasToken(!!tokenRef.current);
      setResponderId(id);
      setPhase("ready");
    }
    api.state().then(setState).catch(() => setState(null));
    return () => window.removeEventListener("hashchange", onHash);
  }, []);

  const closeCall = useCallback(() => {
    if (connectTimerRef.current) clearTimeout(connectTimerRef.current);
    connectTimerRef.current = null;
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
      const config = await fetchRtcConfig(undefined, tokenRef.current);
      if (sessionRef.current !== sessionId) return;
      const pc = new RTCPeerConnection(config);
      pcRef.current = pc;
      // A call that doesn't connect in time (stale offer, operator gone, no network path) is hung up
      // and not answered again, so the phone never sits on "Operator calling…" forever.
      if (connectTimerRef.current) clearTimeout(connectTimerRef.current);
      connectTimerRef.current = setTimeout(() => {
        if (pcRef.current !== pc || pc.connectionState === "connected") return;
        declinedRef.current.add(sessionId);
        void talk.hangup(channel, sessionId, "responder").catch(() => {});
        setInfo("The operator's call didn't connect. You're still connected — the operator can call again.");
        closeCall();
      }, RING_TIMEOUT_MS);
      mic.getAudioTracks().forEach((t) => pc.addTrack(t, mic));
      pc.ontrack = (e) => {
        if (!audioRef.current) return;
        audioRef.current.srcObject = e.streams[0] ?? new MediaStream([e.track]);
        void audioRef.current.play().catch(() => {});
      };
      pc.onconnectionstatechange = () => {
        if (pcRef.current !== pc) return;
        if (pc.connectionState === "connected") {
          if (connectTimerRef.current) clearTimeout(connectTimerRef.current);
          connectTimerRef.current = null;
          setError(null);
          setInfo(null);
          setPhase("talking");
        }
        else if (pc.connectionState === "failed") {
          // Tell the relay too, so the call doesn't stay "connected" there, and don't answer it again.
          declinedRef.current.add(sessionId);
          void talk.hangup(channel, sessionId, "responder").catch(() => {});
          setInfo("Audio couldn't connect. Is this phone on the AEGIS network (hotspot or Tailscale)? You're still connected — the operator can call again.");
          closeCall();
        }
      };
      await pc.setRemoteDescription({ type: "offer", sdp: offer });
      await pc.setLocalDescription(await pc.createAnswer());
      await waitForIce(pc);
      if (pcRef.current !== pc) return;
      await talk.answer(channel, sessionId, pc.localDescription?.sdp ?? "");
    },
    [closeCall, talk],
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
        if (step === "answer" && view.session?.offer && !declinedRef.current.has(view.session.session_id))
          await answer(responderId, view.session.session_id, view.session.offer);
        else if (step === "close") closeCall();
      } catch (e) {
        if (stopped) return;
        if (isTalkAuthError(e) && (e.kind === "signin" || e.kind === "forbidden")) {
          stopped = true;
          clearInterval(t);
          signedOut();
          return;
        }
        setError(talkErrorText(e));
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
  }, [responderId, joined, answer, closeCall]); // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(() => {
    const again = () => {
      if (document.visibilityState === "visible" && joinedRef.current) void keepAwake();
    };
    document.addEventListener("visibilitychange", again);
    return () => document.removeEventListener("visibilitychange", again);
  }, [keepAwake]);

  // Closing the page or leaving: hang up (keepalive survives the unload) and release the microphone.
  useEffect(() => {
    const release = () => {
      const s = sessionRef.current;
      if (responderId && s) void talk.hangup(responderId, s, "responder", true).catch(() => {});
      pcRef.current?.close();
      micRef.current?.getTracks().forEach((t) => t.stop());
      void wakeRef.current?.release().catch(() => {});
    };
    window.addEventListener("pagehide", release);
    return () => {
      window.removeEventListener("pagehide", release);
      release();
    };
  }, [responderId, talk]);

  const join = async () => {
    if (!responderId) return;
    setError(null);
    setInfo(null);
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

  /** The operator revoked this phone or the link expired: stop, release everything, explain. */
  function signedOut() {
    if (responderId) writeToken(window.sessionStorage, phoneTokenKey(responderId), null);
    tokenRef.current = null;
    setHasToken(false);
    setExpired(true);
    joinedRef.current = false;
    setJoined(false);
    closeCall();
    micRef.current?.getTracks().forEach((t) => t.stop());
    micRef.current = null;
    void wakeRef.current?.release().catch(() => {});
    wakeRef.current = null;
    setMuted(false);
    setPhase("ready");
    setError(LINK_EXPIRED);
  }

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
    talking: { text: muted ? "● Talking with the operator · you're muted" : "● Talking with the operator", color: "var(--accent)" },
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
                tokenRef.current = readToken(window.sessionStorage, phoneTokenKey(r.id));
                setHasToken(!!tokenRef.current);
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
          <p className="text-lg font-semibold" style={{ color: status[phase].color }} data-testid="phone-status" data-phase={phase}>
            {status[phase].text}
          </p>

          {phase === "ready" ? (
            <>
              <button
                onClick={join}
                disabled={!secure || !hasToken}
                data-testid="phone-join"
                className="rounded-2xl bg-accent py-8 text-2xl font-bold text-black disabled:opacity-40"
              >
                Join
              </button>
              {!hasToken && !expired && (
                <p className="rounded border border-warn/60 p-3 text-sm leading-snug text-warn" data-testid="phone-needs-link">
                  This phone isn&apos;t signed in for {name}. Ask the operator for your phone link (Comms → Get phone link) and open it here.
                </p>
              )}
            </>
          ) : (
            <div className="grid grid-cols-2 gap-3">
              <button
                onClick={toggleMute}
                data-testid="phone-mute"
                aria-pressed={muted}
                className={`rounded-2xl border py-8 text-xl font-bold ${muted ? "border-warn bg-warn/20 text-warn" : "border-line-strong"}`}
              >
                {muted ? "Unmute" : "Mute"}
              </button>
              <button onClick={leave} data-testid="phone-leave" className="rounded-2xl border border-danger py-8 text-xl font-bold text-danger">
                Leave
              </button>
            </div>
          )}

          {info && (
            <p className="text-sm leading-snug text-muted" data-testid="phone-info">
              {info}
            </p>
          )}
          {error && (
            <p className="text-sm leading-snug text-danger" data-testid="phone-error">
              {error}
            </p>
          )}

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
