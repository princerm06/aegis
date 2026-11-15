"use client";

import { useEffect, useRef, useState } from "react";
import type { SimulationState } from "./types";

/**
 * PREVIEW adapters for features the backend does not provide yet
 * (see frontend/BACKEND_REQUESTS.md). Every adapter has a `none` mode (honest
 * "no data") and a `preview` mode that generates clearly-labelled mock data so
 * the UI can be developed and reviewed. When the backend ships an endpoint,
 * add an `api` mode here; components do not change.
 *
 * Preview data is OFF by default and must never be presented as live.
 */
export interface PreviewFlags {
  sensorVitals: boolean;
  deviceStatus: boolean;
  /** Mock floor assignments (backend has no floor field yet) — see src/lib/floors.ts. */
  multiFloor: boolean;
}

const KEY = "aegis.preview";
const DEFAULT: PreviewFlags = { sensorVitals: false, deviceStatus: false, multiFloor: false };

export function usePreviewFlags() {
  const [flags, setFlags] = useState<PreviewFlags>(DEFAULT);
  useEffect(() => {
    try {
      const raw = localStorage.getItem(KEY);
      if (raw) setFlags({ ...DEFAULT, ...JSON.parse(raw) });
    } catch {
      /* ignore */
    }
  }, []);
  const set = (patch: Partial<PreviewFlags>) =>
    setFlags((f) => {
      const next = { ...f, ...patch };
      try {
        localStorage.setItem(KEY, JSON.stringify(next));
      } catch {
        /* ignore */
      }
      return next;
    });
  const anyOn = flags.sensorVitals || flags.deviceStatus || flags.multiFloor;
  return { flags, set, anyOn };
}

/* ── Sensor vitals (future: POST/GET vitals from a contact or radar sensor) ── */

export interface SensorReading {
  bpm: number;
  at: string;
  preview: true;
}

/** Random-walk heart rate per casualty, PREVIEW ONLY. */
export function useSensorVitals(state: SimulationState | null, enabled: boolean) {
  const [readings, setReadings] = useState<Record<string, SensorReading>>({});
  const ids = state?.scenario.casualties.map((c) => c.id).join(",") ?? "";
  const ref = useRef(readings);
  ref.current = readings;

  useEffect(() => {
    if (!enabled || !ids) {
      setReadings({});
      return;
    }
    const tick = () => {
      const next: Record<string, SensorReading> = {};
      for (const id of ids.split(",")) {
        const prev = ref.current[id]?.bpm ?? 70 + Math.round(Math.random() * 40);
        const bpm = Math.max(45, Math.min(150, prev + Math.round((Math.random() - 0.5) * 6)));
        next[id] = { bpm, at: new Date().toISOString(), preview: true };
      }
      setReadings(next);
    };
    tick();
    const t = setInterval(tick, 2000);
    return () => clearInterval(t);
  }, [enabled, ids]);

  return (id: string): SensorReading | null => (enabled ? readings[id] ?? null : null);
}

/* ── Responder device link (future: device heartbeat events from the bridge) ── */

export type DeviceLink = { kind: "unknown" } | { kind: "online" | "offline"; lastSeen: string; preview: true };

export function useDeviceStatus(state: SimulationState | null, enabled: boolean, now: number) {
  return (responderId: string): DeviceLink => {
    if (!enabled || !state) return { kind: "unknown" };
    const r = state.scenario.responders.find((x) => x.id === responderId);
    // Mock: device mirrors responder availability, heartbeat every ~3 s.
    const lastSeen = new Date(now - (r?.status === "offline" ? 45000 : (now % 3000))).toISOString();
    return { kind: r?.status === "offline" ? "offline" : "online", lastSeen, preview: true };
  };
}
