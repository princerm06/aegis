"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { ageSeconds, responderName, shortId } from "./format";
import type { EventEnvelope, SimulationProposal, SimulationState } from "./types";

export type AlertLevel = "info" | "ok" | "warn" | "danger";

export interface Alert {
  key: string;
  level: AlertLevel;
  title: string;
  detail?: string;
  targetId?: string; // casualty / responder / track to flash
  at: number;
}

const STALE_S = 30;
const EXPIRY_WARN_S = 20;
const TTL: Record<AlertLevel, number> = { info: 6000, ok: 7000, warn: 10000, danger: 0 }; // 0 = until dismissed

/** Short synthesized tones; no audio files. Needs one prior user gesture (browser policy). */
function beep(level: AlertLevel) {
  try {
    const W = window as unknown as { __aegisAudio?: AudioContext; webkitAudioContext?: typeof AudioContext };
    const ctx = (W.__aegisAudio ??= new (window.AudioContext || W.webkitAudioContext!)());
    const notes = level === "danger" ? [880, 660, 880] : level === "warn" ? [740, 740] : level === "ok" ? [523, 784] : [660];
    notes.forEach((f, i) => {
      const o = ctx.createOscillator();
      const g = ctx.createGain();
      o.type = level === "danger" ? "square" : "sine";
      o.frequency.value = f;
      const t = ctx.currentTime + i * 0.16;
      g.gain.setValueAtTime(0.0001, t);
      g.gain.exponentialRampToValueAtTime(0.18, t + 0.02);
      g.gain.exponentialRampToValueAtTime(0.0001, t + 0.14);
      o.connect(g).connect(ctx.destination);
      o.start(t);
      o.stop(t + 0.15);
    });
  } catch {
    /* audio unavailable */
  }
}

/**
 * Attention alerts derived from backend events and time-based state checks.
 * Only events that arrive after the page loads raise alerts (no storm on reload).
 */
export function useAlerts(state: SimulationState | null, events: EventEnvelope[], proposal: SimulationProposal | null, now: number) {
  const [alerts, setAlerts] = useState<Alert[]>([]);
  const [muted, setMuted] = useState(false);
  const baseline = useRef<number | null>(null);
  const handled = useRef(new Set<string>());
  const knownTracks = useRef(new Set<string>());

  useEffect(() => {
    try {
      setMuted(localStorage.getItem("aegis.alerts.muted") === "1");
    } catch {
      /* ignore */
    }
  }, []);

  const toggleMute = () =>
    setMuted((m) => {
      try {
        localStorage.setItem("aegis.alerts.muted", m ? "0" : "1");
      } catch {
        /* ignore */
      }
      return !m;
    });

  const raise = useCallback(
    (a: Omit<Alert, "at">) => {
      if (handled.current.has(a.key)) return;
      handled.current.add(a.key);
      setAlerts((list) => [{ ...a, at: Date.now() }, ...list].slice(0, 6));
      if (!muted) beep(a.level);
    },
    [muted],
  );

  const dismiss = useCallback((key: string) => setAlerts((l) => l.filter((a) => a.key !== key)), []);

  // Auto-expire non-critical alerts.
  useEffect(() => {
    setAlerts((l) => {
      const keep = l.filter((a) => TTL[a.level] === 0 || now - a.at < TTL[a.level]);
      return keep.length === l.length ? l : keep;
    });
  }, [now]);

  // Event-driven alerts.
  useEffect(() => {
    if (!state) return;
    if (baseline.current === null) {
      baseline.current = state.sequence;
      state.tracks.forEach((t) => knownTracks.current.add(t.track_id));
      return;
    }
    for (const e of events) {
      if (e.sequence <= baseline.current || handled.current.has(e.event_id)) continue;
      const p = e.payload as Record<string, unknown>;
      const dev = (p.device_event ?? {}) as Record<string, string>;
      switch (e.type) {
        case "simulation.observation_received": {
          const obs = (p.observation ?? {}) as { track?: { track_id?: string } };
          const tid = obs.track?.track_id;
          if (tid && !knownTracks.current.has(tid)) {
            knownTracks.current.add(tid);
            const linked = state.tracks.find((t) => t.track_id === tid)?.casualty_id;
            if (!linked) raise({ key: e.event_id, level: "warn", title: "New person detected by camera", detail: "Confirm or link in the victim strip", targetId: tid });
          }
          break;
        }
        case "simulation.casualty_created":
          raise({ key: e.event_id, level: "warn", title: "New victim confirmed", detail: shortId(String(p.casualty_id ?? p.id ?? "")), targetId: String(p.casualty_id ?? "") });
          break;
        case "simulation.device_acknowledged":
          raise({
            key: e.event_id,
            level: "ok",
            title: `${responderName(state, dev.responder_id)} acknowledged`,
            detail: `task → ${shortId(dev.casualty_id ?? "")}`,
            targetId: dev.responder_id,
          });
          break;
        case "simulation.responder_updated": {
          const r = (p.responder ?? {}) as { id?: string; status?: string };
          if (r.status === "offline") raise({ key: e.event_id, level: "danger", title: `${responderName(state, r.id ?? "")} is OFFLINE`, targetId: r.id });
          break;
        }
        case "simulation.approval_invalidated":
          raise({ key: e.event_id, level: "warn", title: "Approval invalidated", detail: String(p.reason ?? "").replace(/_/g, " "), targetId: undefined });
          break;
        case "simulation.proposal_created":
          raise({ key: e.event_id, level: "info", title: "New recommendation awaiting approval", detail: `rev ${p.revision}` });
          break;
      }
      handled.current.add(e.event_id);
    }
  }, [events, state, raise]);

  // Time-based alerts.
  useEffect(() => {
    if (!state || baseline.current === null) return;
    if (proposal?.status === "pending_approval") {
      const left = (new Date(proposal.expires_at).getTime() - now) / 1000;
      if (left > 0 && left <= EXPIRY_WARN_S) raise({ key: `exp-${proposal.id}`, level: "warn", title: "Recommendation expiring", detail: `${Math.ceil(left)}s left to approve rev ${proposal.revision}` });
    }
    if (proposal?.status === "approved" && proposal.approval_id) {
      const ap = state.approvals.find((a) => a.id === proposal.approval_id);
      const left = ap ? (new Date(ap.expires_at).getTime() - now) / 1000 : 0;
      if (ap?.status === "active" && left > 0 && left <= EXPIRY_WARN_S)
        raise({ key: `apexp-${ap.id}`, level: "warn", title: "Approval expiring — dispatch now", detail: `${Math.ceil(left)}s left` });
    }
    for (const t of state.tracks) {
      if (!t.casualty_id) continue;
      const age = ageSeconds(t.latest_observation.observed_at, now);
      if (age > STALE_S) raise({ key: `stale-${t.track_id}-${t.latest_observation.id}`, level: "warn", title: `Camera lost sight of ${shortId(t.casualty_id)}`, detail: `last seen ${age}s ago`, targetId: t.casualty_id });
    }
  }, [now, state, proposal, raise]);

  const flashing = new Set(alerts.filter((a) => a.targetId && now - a.at < 6000).map((a) => a.targetId!));

  return { alerts, dismiss, muted, toggleMute, flashing };
}
