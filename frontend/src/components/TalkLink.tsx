"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { assessMediaSupport, readMediaEnvironment } from "@/lib/mediaCapabilities";
import { ICE_SERVERS, MIC, POLL_MS, RING_TIMEOUT_MS, operatorStep, phonePageUrl, talkApi, talkErrorText, waitForIce } from "@/lib/talk";

type Phase = "idle" | "ringing" | "live";

const api = talkApi();
const clock = (s: number) => `${Math.floor(s / 60)}:${String(Math.floor(s % 60)).padStart(2, "0")}`;

/**
 * Two-way voice with the responder's phone, browser to browser. The phone keeps
 * /responder open and answers by itself; the operator just presses Talk.
 */
export function TalkLink({ responderId, name, onLog }: { responderId: string; name: string; onLog: (t: string, k: "voice") => void }) {
  const [online, setOnline] = useState<boolean | null>(null);
  const [phase, setPhase] = useState<Phase>("idle");
  const [peer, setPeer] = useState(name);
  const [muted, setMuted] = useState(false);
  const [elapsed, setElapsed] = useState(0);
  const [error, setError] = useState<string | null>(null);
  const [phoneUrl, setPhoneUrl] = useState("");
  const audioRef = useRef<HTMLAudioElement>(null);
  const pcRef = useRef<RTCPeerConnection | null>(null);
  const micRef = useRef<MediaStream | null>(null);
  const call = useRef<{ channel: string; who: string; session: string | null; answered: boolean; startedAt: number; liveAt: number } | null>(null);

  useEffect(() => {
    const origin = process.env.NEXT_PUBLIC_AEGIS_PHONE_ORIGIN || (window.location.protocol === "https:" ? window.location.origin : "");
    setPhoneUrl(phonePageUrl(origin || "https://<host's Tailscale address>", responderId));
  }, [responderId]);

  const end = useCallback(
    (why: string | null, tellPhone: boolean) => {
      const c = call.current;
      if (!c) return;
      call.current = null;
      if (tellPhone && c.session) void api.hangup(c.channel, c.session, "operator").catch(() => {});
      pcRef.current?.close();
      pcRef.current = null;
      micRef.current?.getTracks().forEach((t) => t.stop());
      micRef.current = null;
      if (audioRef.current) audioRef.current.srcObject = null;
      onLog(c.liveAt ? `Talk with ${c.who} (${clock((Date.now() - c.liveAt) / 1000)})` : `Talk with ${c.who} not connected`, "voice");
      setMuted(false);
      setPhase("idle");
      setError(why);
    },
    [onLog],
  );

  useEffect(() => () => end(null, true), [end]);

  // One poll: the selected responder's presence, or the active call's progress.
  useEffect(() => {
    let stopped = false;
    const tick = async () => {
      const c = call.current;
      try {
        const view = await api.status(c?.channel ?? responderId);
        if (stopped) return;
        if (!c) {
          setOnline(view.responder_online);
          return;
        }
        if (!c.session || call.current !== c) return;
        const step = operatorStep(c.session, c.answered, view);
        if (step === "apply-answer" && view.session?.answer) {
          c.answered = true;
          await pcRef.current?.setRemoteDescription({ type: "answer", sdp: view.session.answer }).catch((e: unknown) => {
            if (call.current === c) end(`Couldn't connect to ${c.who}'s phone: ${talkErrorText(e)}`, true);
          });
        } else if (step === "close") {
          end(view.session?.ended_by === "responder" ? `${c.who} left the talk link.` : null, false);
        } else if (!c.answered && Date.now() - c.startedAt > RING_TIMEOUT_MS) {
          end(`${c.who}'s phone didn't pick up. Is the AEGIS page open on it?`, true);
        }
      } catch {
        if (!stopped && !c) setOnline(null);
      }
    };
    void tick();
    const t = setInterval(tick, POLL_MS);
    return () => {
      stopped = true;
      clearInterval(t);
    };
  }, [responderId, end]);

  useEffect(() => {
    if (phase !== "live") return;
    const t = setInterval(() => setElapsed(call.current?.liveAt ? (Date.now() - call.current.liveAt) / 1000 : 0), 1000);
    return () => clearInterval(t);
  }, [phase]);

  const start = async () => {
    if (assessMediaSupport(readMediaEnvironment()).kind !== "none") {
      setError("Talking needs this browser's microphone: open the dashboard as http://localhost:3000 on the host, or over HTTPS.");
      return;
    }
    const c = { channel: responderId, who: name, session: null as string | null, answered: false, startedAt: Date.now(), liveAt: 0 };
    call.current = c;
    setPeer(name);
    setError(null);
    setElapsed(0);
    setPhase("ringing");
    try {
      const mic = await navigator.mediaDevices.getUserMedia({ audio: MIC });
      micRef.current = mic;
      const pc = new RTCPeerConnection({ iceServers: ICE_SERVERS });
      pcRef.current = pc;
      mic.getAudioTracks().forEach((t) => pc.addTrack(t, mic));
      pc.ontrack = (e) => {
        if (!audioRef.current) return;
        audioRef.current.srcObject = e.streams[0] ?? new MediaStream([e.track]);
        void audioRef.current.play().catch(() => {});
      };
      pc.onconnectionstatechange = () => {
        if (call.current !== c) return;
        if (pc.connectionState === "connected" && !c.liveAt) {
          c.liveAt = Date.now();
          setPhase("live");
        } else if (pc.connectionState === "failed") {
          end("Audio couldn't connect. Put the phone and this computer on the same network (hotspot or Tailscale).", true);
        }
      };
      await pc.setLocalDescription(await pc.createOffer());
      await waitForIce(pc);
      if (call.current !== c) return;
      const view = await api.offer(c.channel, pc.localDescription?.sdp ?? "");
      c.session = view.session?.session_id ?? null;
    } catch (e) {
      if (call.current === c) end(talkErrorText(e), true);
    }
  };

  const toggleMute = () => {
    const next = !muted;
    micRef.current?.getAudioTracks().forEach((t) => (t.enabled = !next));
    setMuted(next);
  };

  return (
    <div className="rounded border border-line bg-panel-2 p-2">
      <audio ref={audioRef} autoPlay playsInline hidden />
      {phase === "idle" ? (
        <div className="flex flex-col gap-1">
          <div className="flex items-center gap-2">
            <button
              onClick={start}
              disabled={!online}
              className="whitespace-nowrap rounded border border-accent/60 px-2.5 py-1 text-[11px] font-semibold text-accent hover:bg-accent/10 disabled:cursor-not-allowed disabled:border-line-strong disabled:text-faint"
            >
              🎙 Talk to {name}
            </button>
            <span className="flex items-center gap-1 text-[10.5px] text-muted">
              <span className="inline-block h-1.5 w-1.5 rounded-full" style={{ background: online ? "var(--accent)" : "var(--faint)" }} />
              {online === null ? "checking phone…" : online ? "phone ready" : "phone not connected"}
            </span>
          </div>
          {online === false && (
            <p className="text-[10px] leading-snug text-faint">
              On {name}&apos;s phone open <span className="mono select-all text-muted">{phoneUrl}</span> and tap Join.
            </p>
          )}
          {error && <p className="text-[10.5px] leading-snug text-danger">{error}</p>}
        </div>
      ) : (
        <div className="flex items-center gap-2">
          <span className={`mono flex-1 text-[11.5px] font-bold ${phase === "live" ? "text-accent" : "text-warn"}`}>
            {phase === "live" ? `● TALKING · ${peer.toUpperCase()} · ${clock(elapsed)}` : `Connecting to ${peer}'s phone…`}
          </span>
          {phase === "live" && (
            <button
              onClick={toggleMute}
              aria-pressed={muted}
              className={`mono rounded border px-2 py-1 text-[10.5px] uppercase ${muted ? "border-warn text-warn" : "border-line-strong text-muted hover:text-text"}`}
            >
              {muted ? "unmute" : "mute"}
            </button>
          )}
          <button onClick={() => end(null, true)} className="rounded border border-danger bg-danger px-2.5 py-1 text-[11px] font-bold text-white">
            {phase === "live" ? "End" : "Cancel"}
          </button>
        </div>
      )}
    </div>
  );
}
