"use client";

import { useCallback, useEffect, useState } from "react";

/**
 * Operator-logged field observations per casualty: what command SEES on a feed
 * or HEARS from the responder. They are not CV output and not a diagnosis.
 *
 * PROVISIONAL: the shared contract has no vitals/injury schema yet, so these
 * live only in this browser (localStorage, scoped to the simulation epoch).
 * When Prince adds a schema, swap this hook for API calls.
 */
export type Tri = "yes" | "no" | "unknown";

export interface PulseReport {
  bpm: number;
  at: string; // ISO time logged
  source: "responder_report" | "operator_observed";
}

export interface FieldNote {
  bleeding: Tri;
  wound: Tri;
  woundNote: string;
  breathing: Tri;
  pulse: PulseReport | null;
  updatedAt: string | null;
}

export const EMPTY_NOTE: FieldNote = {
  bleeding: "unknown",
  wound: "unknown",
  woundNote: "",
  breathing: "unknown",
  pulse: null,
  updatedAt: null,
};

export function useFieldNotes(epoch: number | undefined) {
  const key = epoch == null ? null : `aegis.fieldNotes.e${epoch}`;
  const [notes, setNotes] = useState<Record<string, FieldNote>>({});

  useEffect(() => {
    if (!key) return;
    try {
      const raw = localStorage.getItem(key);
      setNotes(raw ? JSON.parse(raw) : {});
    } catch {
      setNotes({});
    }
  }, [key]);

  const update = useCallback(
    (casualtyId: string, patch: Partial<FieldNote>) => {
      setNotes((prev) => {
        const next = {
          ...prev,
          [casualtyId]: { ...EMPTY_NOTE, ...prev[casualtyId], ...patch, updatedAt: new Date().toISOString() },
        };
        if (key) {
          try {
            localStorage.setItem(key, JSON.stringify(next));
          } catch {
            /* storage unavailable: keep in memory */
          }
        }
        return next;
      });
    },
    [key],
  );

  const get = useCallback((id: string): FieldNote => notes[id] ?? EMPTY_NOTE, [notes]);

  return { notes, get, update };
}
