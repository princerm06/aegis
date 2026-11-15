"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { SEVERITY_META, clock, meters, responderName, shortId } from "@/lib/format";
import { directHint, routeToSteps } from "@/lib/navigation";
import type { AStarRoute, ResponderStatus, SimulationProposal, SimulationState } from "@/lib/types";
import { Badge } from "./ui";

interface Props {
  state: SimulationState;
  proposal: SimulationProposal | null;
  fighterId: string | null;
  onStatus: (id: string, status: ResponderStatus) => void;
  placing: boolean;
  onPlace: (on: boolean) => void;
  busy: boolean;
}

const QUICK = ["Stop", "Hold position", "Go forward", "Turn left", "Turn right", "Go back", "Victim is ahead", "Evacuate now"];
const STATUS_COLOR: Record<ResponderStatus, string> = { available: "var(--accent)", busy: "var(--warn)", offline: "var(--faint)" };

interface LogLine {
  at: string;
  text: string;
  kind: "voice" | "tts";
}

/** Voice + guidance for the firefighter selected in the bodycam grid. */
export function CommsPanel({ state, proposal, fighterId, onStatus, placing, onPlace, busy }: Props) {
  const fighters = state.scenario.responders;
  const f = fighters.find((r) => r.id === fighterId) ?? fighters[0];
  const [log, setLog] = useState<LogLine[]>([]);
  const addLog = useCallback((text: string, kind: LogLine["kind"]) => {
    setLog((l) => [{ at: new Date().toISOString(), text, kind }, ...l].slice(0, 30));
  }, []);

  if (!f) return <section className="panel p-3 text-xs text-muted">No responders in scenario.</section>;
  const name = responderName(state, f.id);

  // Target: active task first, otherwise the pending/approved recommendation.
  const task = state.assignments.find((a) => a.responder_id === f.id && a.status !== "completed");
  const proposed =
    proposal && (proposal.status === "pending_approval" || proposal.status === "approved")
      ? proposal.assignments.find((a) => a.responder_id === f.id)
      : undefined;
  const targetId = task?.casualty_id ?? proposed?.casualty_id ?? null;
  const route: AStarRoute | null | undefined = task?.route ?? proposed?.route;
  const target = state.scenario.casualties.find((c) => c.id === targetId);
  const sev = target?.medical_assessment?.severity ?? "unassessed";

  const speak = (text: string) => {
    const phrase = `${name}. ${text}`;
    try {
      window.speechSynthesis.cancel();
      const u = new SpeechSynthesisUtterance(phrase);
      u.rate = 1.02;
      window.speechSynthesis.speak(u);
    } catch {
      /* TTS unavailable */
    }
    addLog(text, "tts");
  };

  const steps = route && target ? routeToSteps(route, `Victim ${shortId(target.id)}`) : [];

  return (
    <section className="panel flex min-h-0 flex-1 flex-col">
      <header className="flex items-center gap-2 border-b border-line px-2.5 py-1.5">
        <h2 className="label !text-text">Comms</h2>
        <span className="text-[12px] font-semibold">→ {name}</span>
        {target && (
          <span className="mono text-[10.5px] text-muted">
            target {shortId(target.id)} <span style={{ color: SEVERITY_META[sev].color }}>{SEVERITY_META[sev].label}</span>
            {route ? ` · ${meters(route.distance_m)}` : ""}
          </span>
        )}
      </header>

      <div className="scrollbar-thin min-h-0 flex-1 space-y-2 overflow-y-auto p-2">
        {/* status row */}
        <div className="flex items-center gap-2">
          <div className="grid flex-1 grid-cols-3 gap-1" role="radiogroup" aria-label={`${name} availability`}>
            {(["available", "busy", "offline"] as ResponderStatus[]).map((s) => {
              const active = f.status === s;
              return (
                <button
                  key={s}
                  role="radio"
                  aria-checked={active}
                  disabled={busy || active}
                  onClick={() => onStatus(f.id, s)}
                  className="mono rounded border py-0.5 text-[10px] uppercase tracking-wider disabled:cursor-default"
                  style={{
                    borderColor: active ? STATUS_COLOR[s] : "var(--line-strong)",
                    color: active ? STATUS_COLOR[s] : "var(--muted)",
                  }}
                >
                  {s}
                </button>
              );
            })}
          </div>
          <button
            onClick={() => onPlace(!placing)}
            className={`mono rounded border px-2 py-0.5 text-[10px] uppercase ${placing ? "border-warn bg-warn/15 text-warn" : "border-line-strong text-muted hover:text-text"}`}
            title="Then click the map where the bodycam shows the firefighter is"
          >
            {placing ? "click map…" : "mark position"}
          </button>
        </div>

        <PushToTalk name={name} onLog={addLog} />

        {/* quick commands */}
        <div>
          <p className="label mb-1 !text-[9.5px]">Quick commands (spoken)</p>
          <div className="grid grid-cols-4 gap-1">
            {QUICK.map((q) => (
              <button
                key={q}
                onClick={() => speak(q)}
                className={`rounded border px-1 py-1 text-[10.5px] leading-tight hover:border-accent hover:text-accent ${
                  q === "Evacuate now" || q === "Stop" ? "border-danger/50 text-danger" : "border-line-strong"
                }`}
              >
                {q}
              </button>
            ))}
          </div>
          <CustomSay onSay={speak} />
        </div>

        {/* navigation */}
        <div className="rounded border border-line bg-panel-2">
          <div className="flex items-center gap-2 border-b border-line px-2 py-1">
            <span className="label !text-[9.5px] !text-text">Route guidance</span>
            {target && <span className="mono text-[10px] text-muted">→ {shortId(target.id)}</span>}
            {!task && proposed && <Badge tone="warn">not yet approved</Badge>}
            {steps.length > 0 && (
              <button onClick={() => speak(steps.map((s) => s.text).join(". "))} className="ml-auto text-[10.5px] text-accent hover:underline">
                speak all
              </button>
            )}
          </div>
          {!target ? (
            <p className="px-2 py-2 text-[11px] text-muted">No task or recommendation for {name}.</p>
          ) : steps.length ? (
            <ol className="divide-y divide-line">
              {steps.map((s, i) => (
                <li key={s.id} className="flex items-center gap-2 px-2 py-1">
                  <span className="mono w-4 text-[10px] text-faint">{i + 1}</span>
                  <span className="flex-1 text-[11.5px]">{s.text}</span>
                  <button onClick={() => speak(s.text)} className="text-[10px] text-muted hover:text-accent" aria-label={`Speak: ${s.text}`}>
                    ▶ say
                  </button>
                </li>
              ))}
            </ol>
          ) : (
            <p className="px-2 py-2 text-[11px] text-muted">{directHint(f.position, target.position, `Victim ${shortId(target.id)}`)}</p>
          )}
          <p className="border-t border-line px-2 py-1 text-[9.5px] text-faint">
            From the planned A* route (map north = up). Responder heading isn&apos;t tracked — confirm on the bodycam.
          </p>
        </div>

        {log.length > 0 && (
          <div>
            <p className="label mb-1 !text-[9.5px]">Comms log</p>
            <ol className="space-y-0.5">
              {log.map((l, i) => (
                <li key={i} className="mono flex gap-2 text-[10.5px]">
                  <span className="text-faint">{clock(l.at)}</span>
                  <span className={l.kind === "voice" ? "text-accent" : "text-muted"}>{l.kind === "voice" ? "🎙" : "🔊"}</span>
                  <span className="text-muted">{l.text}</span>
                </li>
              ))}
            </ol>
          </div>
        )}
      </div>
    </section>
  );
}

function CustomSay({ onSay }: { onSay: (t: string) => void }) {
  const [t, setT] = useState("");
  return (
    <form
      className="mt-1 flex gap-1"
      onSubmit={(e) => {
        e.preventDefault();
        if (t.trim()) onSay(t.trim());
        setT("");
      }}
    >
      <input
        value={t}
        onChange={(e) => setT(e.target.value)}
        placeholder="Type a message to speak…"
        className="min-w-0 flex-1 rounded border border-line-strong bg-bg px-2 py-1 text-[11px] outline-none focus:border-accent"
      />
      <button className="rounded border border-line-strong px-2 text-[11px] hover:border-accent">Say</button>
    </form>
  );
}

/**
 * Push-to-talk: routes the operator's microphone to a chosen audio output
 * (e.g. the speaker attached to the responder, paired to this computer).
 * Hold the button or the Space bar. Mic is muted when not held.
 */
function PushToTalk({ name, onLog }: { name: string; onLog: (t: string, k: "voice") => void }) {
  const [ready, setReady] = useState(false);
  const [talking, setTalking] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [outputs, setOutputs] = useState<MediaDeviceInfo[]>([]);
  const [sink, setSink] = useState("");
  const audioRef = useRef<HTMLAudioElement>(null);
  const streamRef = useRef<MediaStream | null>(null);
  const startedAt = useRef<number>(0);
  const talkingRef = useRef<boolean>(false);
  const canSink = typeof HTMLMediaElement !== "undefined" && "setSinkId" in HTMLMediaElement.prototype;

  const enable = async () => {
    try {
      const s = await navigator.mediaDevices.getUserMedia({ audio: { echoCancellation: true, noiseSuppression: true } });
      s.getAudioTracks().forEach((t) => (t.enabled = false));
      streamRef.current = s;
      if (audioRef.current) {
        audioRef.current.srcObject = s;
        await audioRef.current.play();
      }
      const all = await navigator.mediaDevices.enumerateDevices();
      setOutputs(all.filter((d) => d.kind === "audiooutput"));
      setReady(true);
      setError(null);
    } catch (e) {
      setError((e as Error).message);
    }
  };

  useEffect(() => () => streamRef.current?.getTracks().forEach((t) => t.stop()), []);

  useEffect(() => {
    if (!canSink || !sink || !audioRef.current) return;
    (audioRef.current as HTMLAudioElement & { setSinkId: (id: string) => Promise<void> }).setSinkId(sink).catch((e: Error) => setError(e.message));
  }, [sink, canSink]);

  const set = useCallback(
    (on: boolean) => {
      const s = streamRef.current;
      if (!s) return;
      if (on) {
        if (talkingRef.current) return;
        talkingRef.current = true;
        startedAt.current = Date.now();
        s.getAudioTracks().forEach((t) => (t.enabled = true));
        setTalking(true);
      } else {
        if (!talkingRef.current) return;
        talkingRef.current = false;
        s.getAudioTracks().forEach((t) => (t.enabled = false));
        setTalking(false);
        const duration = Math.max(1, Math.round((Date.now() - startedAt.current) / 1000));
        onLog(`Voice to ${name} (${duration}s)`, "voice");
      }
    },
    [name, onLog],
  );

  // Space bar = hold to talk (ignored while typing).
  useEffect(() => {
    if (!ready) return;
    const typing = (e: KeyboardEvent) => e.target instanceof HTMLInputElement || e.target instanceof HTMLTextAreaElement || e.target instanceof HTMLSelectElement;
    const down = (e: KeyboardEvent) => {
      if (e.code === "Space" && !e.repeat && !typing(e)) {
        e.preventDefault();
        set(true);
      }
    };
    const up = (e: KeyboardEvent) => {
      if (e.code === "Space" && !typing(e)) set(false);
    };
    window.addEventListener("keydown", down);
    window.addEventListener("keyup", up);
    return () => {
      window.removeEventListener("keydown", down);
      window.removeEventListener("keyup", up);
    };
  }, [ready, set]);

  return (
    <div className="rounded border border-line bg-panel-2 p-2">
      <audio ref={audioRef} hidden />
      {!ready ? (
        <div className="flex items-center gap-2">
          <button onClick={enable} className="rounded border border-accent/60 px-2.5 py-1 text-[11px] text-accent hover:bg-accent/10">
            Enable voice link
          </button>
          <span className="text-[10.5px] text-muted">{error ? <span className="text-danger">{error}</span> : `Mic → ${name}'s speaker`}</span>
        </div>
      ) : (
        <div className="flex items-center gap-2">
          <button
            onPointerDown={() => set(true)}
            onPointerUp={() => set(false)}
            onPointerLeave={() => talking && set(false)}
            className={`flex-1 select-none rounded border py-2 text-[12px] font-bold tracking-wide transition ${
              talking ? "border-danger bg-danger text-white" : "border-accent/60 text-accent hover:bg-accent/10"
            }`}
          >
            {talking ? `● TALKING TO ${name.toUpperCase()}` : "HOLD TO TALK  ·  space"}
          </button>
          {canSink && outputs.length > 0 && (
            <select
              value={sink}
              onChange={(e) => setSink(e.target.value)}
              className="mono w-32 rounded border border-line-strong bg-bg px-1 py-1 text-[10.5px]"
              title="Audio output (the responder's speaker)"
            >
              <option value="">default output</option>
              {outputs.map((o) => (
                <option key={o.deviceId} value={o.deviceId}>
                  {o.label || "output"}
                </option>
              ))}
            </select>
          )}
        </div>
      )}
    </div>
  );
}
