"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { api, ApiError, wsUrl } from "./api";
import type { ActionResponse, EventEnvelope, SimulationState } from "./types";

export type LinkStatus = "connecting" | "live" | "polling" | "offline";

export interface Toast {
  id: number;
  kind: "error" | "info";
  text: string;
}

const MAX_EVENTS = 500;
const POLL_MS = 2000;

/**
 * Authoritative-state sync, per docs/INTEGRATION.md:
 * - The WebSocket sends a `simulation.snapshot` first, then events.
 * - Events only *notify*: on each one we refetch GET /state instead of
 *   guessing local state. Mutation responses also carry the post-state.
 * - If the socket drops we poll /state and /events until it reconnects.
 */
export function useSimulation() {
  const [state, setState] = useState<SimulationState | null>(null);
  const [events, setEvents] = useState<EventEnvelope[]>([]);
  const [link, setLink] = useState<LinkStatus>("connecting");
  const [pending, setPending] = useState<string | null>(null);
  const [toasts, setToasts] = useState<Toast[]>([]);

  const seen = useRef(new Set<string>());
  const lastSeq = useRef(0);
  const stateSeq = useRef(-1);
  const refetchTimer = useRef<ReturnType<typeof setTimeout> | null>(null);

  const toast = useCallback((kind: Toast["kind"], text: string) => {
    const id = Date.now() + Math.random();
    setToasts((t) => [...t.slice(-3), { id, kind, text }]);
    setTimeout(() => setToasts((t) => t.filter((x) => x.id !== id)), kind === "error" ? 7000 : 3500);
  }, []);

  const applyState = useCallback((s: SimulationState) => {
    // Sequences survive resets, so a higher-or-equal sequence is always newer.
    if (s.sequence < stateSeq.current) return;
    stateSeq.current = s.sequence;
    setState(s);
  }, []);

  const addEvents = useCallback((incoming: EventEnvelope[]) => {
    const fresh = incoming.filter((e) => {
      if (e.type === "simulation.snapshot" || seen.current.has(e.event_id)) return false;
      seen.current.add(e.event_id);
      return true;
    });
    if (!fresh.length) return;
    for (const e of fresh) lastSeq.current = Math.max(lastSeq.current, e.sequence);
    setEvents((prev) => [...prev, ...fresh].sort((a, b) => a.sequence - b.sequence).slice(-MAX_EVENTS));
  }, []);

  const refreshState = useCallback(async () => {
    try {
      applyState(await api.state());
    } catch {
      /* link status handles visibility */
    }
  }, [applyState]);

  const scheduleRefetch = useCallback(() => {
    if (refetchTimer.current) clearTimeout(refetchTimer.current);
    refetchTimer.current = setTimeout(refreshState, 120);
  }, [refreshState]);

  const catchUpEvents = useCallback(async () => {
    try {
      const page = await api.events(lastSeq.current);
      addEvents(page.events);
    } catch {
      /* ignore */
    }
  }, [addEvents]);

  // Initial load + WebSocket with reconnect / polling fallback.
  useEffect(() => {
    let ws: WebSocket | null = null;
    let closed = false;
    let retry: ReturnType<typeof setTimeout> | null = null;
    let poll: ReturnType<typeof setInterval> | null = null;
    let attempts = 0;

    const startPolling = () => {
      if (poll) return;
      poll = setInterval(async () => {
        try {
          applyState(await api.state());
          await catchUpEvents();
          setLink((l) => (l === "live" ? l : "polling"));
        } catch {
          setLink("offline");
        }
      }, POLL_MS);
    };
    const stopPolling = () => {
      if (poll) clearInterval(poll);
      poll = null;
    };

    const connect = () => {
      if (closed) return;
      try {
        ws = new WebSocket(wsUrl());
      } catch {
        startPolling();
        return;
      }
      ws.onopen = () => {
        attempts = 0;
        setLink("live");
        stopPolling();
      };
      ws.onmessage = (msg) => {
        let env: EventEnvelope;
        try {
          env = JSON.parse(msg.data);
        } catch {
          return;
        }
        if (env.type === "simulation.snapshot") {
          applyState(env.payload as unknown as SimulationState);
          void catchUpEvents();
          return;
        }
        addEvents([env]);
        scheduleRefetch();
      };
      ws.onclose = () => {
        if (closed) return;
        setLink("polling");
        startPolling();
        attempts += 1;
        retry = setTimeout(connect, Math.min(8000, 500 * 2 ** attempts));
      };
      ws.onerror = () => ws?.close();
    };

    (async () => {
      try {
        applyState(await api.state());
        await catchUpEvents();
      } catch {
        setLink("offline");
      }
      connect();
    })();

    return () => {
      closed = true;
      if (retry) clearTimeout(retry);
      stopPolling();
      ws?.close();
    };
  }, [addEvents, applyState, catchUpEvents, scheduleRefetch]);

  /** Run one explicit operator action; surfaces backend errors verbatim. */
  const run = useCallback(
    async <R,>(label: string, fn: () => Promise<ActionResponse<R>>): Promise<ActionResponse<R> | null> => {
      setPending(label);
      try {
        const res = await fn();
        applyState(res.state);
        if (res.duplicate) toast("info", `${label}: replayed earlier result (no duplicate action)`);
        void catchUpEvents();
        return res;
      } catch (e) {
        const err = e as ApiError;
        const hint = err.status === 409 ? " — state changed; review the current proposal" : "";
        toast("error", `${label} failed (${err.status || "network"}): ${err.detail ?? String(e)}${hint}`);
        void refreshState();
        return null;
      } finally {
        setPending(null);
      }
    },
    [applyState, catchUpEvents, refreshState, toast],
  );

  return { state, events, link, pending, toasts, run, refreshState, toast };
}
