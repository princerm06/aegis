"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import type { Call, Device } from "@twilio/voice-sdk";
import { assessMediaSupport, readMediaEnvironment } from "@/lib/mediaCapabilities";
import { callAvailability, callClock, callErrorText, fetchCallStatus, fetchCallToken, type CallStatus } from "@/lib/responderCalls";

type Phase = "idle" | "connecting" | "ringing" | "live";

const MIC_NEEDS_SECURE =
  "Calls need this browser's microphone: open the dashboard on the host as http://localhost:3000 (or over HTTPS).";

/**
 * Rings the responder's real phone from the operator's browser (Twilio), so both
 * sides talk hands-free while the responder works. One call at a time; switching
 * the selected responder keeps the current call.
 */
export function PhoneCall({ responderId, name, onLog }: { responderId: string; name: string; onLog: (t: string, k: "voice") => void }) {
  const [status, setStatus] = useState<CallStatus | null>(null);
  const [phase, setPhase] = useState<Phase>("idle");
  const [peer, setPeer] = useState(name);
  const [muted, setMuted] = useState(false);
  const [elapsed, setElapsed] = useState(0);
  const [error, setError] = useState<string | null>(null);
  const deviceRef = useRef<Device | null>(null);
  const callRef = useRef<Call | null>(null);
  const answeredAt = useRef(0);
  const abortedRef = useRef(false);

  useEffect(() => {
    let live = true;
    void fetchCallStatus().then((s) => live && setStatus(s));
    return () => {
      live = false;
    };
  }, []);

  useEffect(() => {
    if (phase !== "live") return;
    const t = setInterval(() => setElapsed((Date.now() - answeredAt.current) / 1000), 1000);
    return () => clearInterval(t);
  }, [phase]);

  useEffect(
    () => () => {
      callRef.current?.disconnect();
      deviceRef.current?.destroy();
    },
    [],
  );

  const finish = useCallback(
    (who: string, why?: string) => {
      const talked = answeredAt.current ? (Date.now() - answeredAt.current) / 1000 : 0;
      onLog(talked ? `Phone call with ${who} (${callClock(talked)})` : `Call to ${who} not connected`, "voice");
      answeredAt.current = 0;
      callRef.current = null;
      deviceRef.current?.destroy();
      deviceRef.current = null;
      setMuted(false);
      setPhase("idle");
      setError(why ?? null);
    },
    [onLog],
  );

  const hangUp = () => {
    if (callRef.current) {
      callRef.current.disconnect();
      return;
    }
    abortedRef.current = true;
    finish(peer);
  };

  const start = async () => {
    if (assessMediaSupport(readMediaEnvironment()).kind !== "none") {
      setError(MIC_NEEDS_SECURE);
      return;
    }
    const who = name;
    abortedRef.current = false;
    setPeer(who);
    setError(null);
    setPhase("connecting");
    try {
      const [{ Device, Call }, grant] = await Promise.all([import("@twilio/voice-sdk"), fetchCallToken(responderId)]);
      if (abortedRef.current) return;
      const device = new Device(grant.token, {
        codecPreferences: [Call.Codec.Opus, Call.Codec.PCMU],
        closeProtection: `You are on a call with ${who}.`,
      });
      deviceRef.current = device;
      let done = false;
      const end = (why?: string) => {
        if (done || abortedRef.current) return;
        done = true;
        finish(who, why);
      };
      device.on("error", (e: unknown) => end(callErrorText(e, who)));
      const call = await device.connect();
      if (abortedRef.current || done) {
        call.disconnect();
        device.destroy();
        return;
      }
      callRef.current = call;
      call.on("ringing", () => setPhase("ringing"));
      call.on("accept", () => {
        answeredAt.current = Date.now();
        setElapsed(0);
        setPhase("live");
      });
      call.on("mute", (isMuted: boolean) => setMuted(isMuted));
      call.on("disconnect", () => end());
      call.on("cancel", () => end());
      call.on("reject", () => end(`${who} declined the call.`));
      call.on("error", (e: unknown) => end(callErrorText(e, who)));
    } catch (e) {
      if (abortedRef.current) return;
      deviceRef.current?.destroy();
      deviceRef.current = null;
      setPhase("idle");
      setError(callErrorText(e, who));
    }
  };

  if (!status) return null;
  const availability = callAvailability(status, responderId, name);

  if (phase !== "idle") {
    return (
      <div className="flex items-center gap-2 rounded border border-accent/60 bg-accent/10 p-2">
        <span className={`mono flex-1 text-[11.5px] font-bold ${phase === "live" ? "text-accent" : "text-warn"}`}>
          {phase === "live" ? `● ON CALL · ${peer.toUpperCase()} · ${callClock(elapsed)}` : `📞 Ringing ${peer}…`}
        </span>
        {phase === "live" && (
          <button
            onClick={() => callRef.current?.mute(!muted)}
            aria-pressed={muted}
            className={`mono rounded border px-2 py-1 text-[10.5px] uppercase ${muted ? "border-warn text-warn" : "border-line-strong text-muted hover:text-text"}`}
          >
            {muted ? "unmute" : "mute"}
          </button>
        )}
        <button
          onClick={hangUp}
          className="rounded border border-danger bg-danger px-2.5 py-1 text-[11px] font-bold text-white"
        >
          {phase === "live" ? "Hang up" : "Cancel"}
        </button>
      </div>
    );
  }

  if (availability.kind !== "ready") {
    return <p className="text-[10.5px] text-faint">📞 {availability.text}</p>;
  }

  return (
    <div className="rounded border border-line bg-panel-2 p-2">
      <div className="flex items-center gap-2">
        <button
          onClick={start}
          className="whitespace-nowrap rounded border border-accent/60 px-2.5 py-1 text-[11px] font-semibold text-accent hover:bg-accent/10"
          title="Rings their real phone; you both talk hands-free"
        >
          📞 Call {name}
        </button>
        <span className="mono whitespace-nowrap text-[10.5px] text-muted">{availability.hint}</span>
        <span className="truncate text-[10px] text-faint">rings their phone</span>
      </div>
      {error && <p className="mt-1 text-[10.5px] leading-snug text-danger">{error}</p>}
    </div>
  );
}
