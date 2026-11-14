"use client";

import type { EventEnvelope, SimulationProposal, SimulationState } from "@/lib/types";

type Phase = "idle" | "active" | "done";

interface Step {
  key: string;
  label: string;
  value: string;
  phase: Phase;
}

const ADAPT_TRIGGERS = /^(map|responder|association|casualty|observation|camera|capabilities)/;

/**
 * The coordination loop (detect → … → adapt), derived only from backend state
 * and events. Nothing here is a hardcoded success indicator.
 */
export function LoopStrip({
  state,
  proposal,
  events,
  compact = false,
}: {
  state: SimulationState;
  proposal: SimulationProposal | null;
  events: EventEnvelope[];
  compact?: boolean;
}) {
  const cas = state.scenario.casualties;
  const assessed = cas.filter((c) => c.medical_assessment).length;
  const tasks = state.assignments;
  const acked = tasks.filter((a) => a.status === "acknowledged" || a.status === "completed").length;
  const awaitingAck = tasks.filter((a) => a.status === "dispatched" || a.status === "delivered").length;
  // Only count events from the current epoch (after the latest reset).
  const lastReset = events.reduce((m, e) => (e.type === "simulation.reset" ? Math.max(m, e.sequence) : m), 0);
  const adapts = events.filter(
    (e) => e.sequence > lastReset).filter(
    (e) => e.type === "simulation.replan_requested" && ADAPT_TRIGGERS.test(String(e.payload.trigger ?? "")),
  ).length;
  const rec = state.last_recommendation;

  const steps: Step[] = [
    {
      key: "detect",
      label: "Detect",
      value: `${cas.length} casualties · ${state.tracks.length} tracks`,
      phase: cas.length || state.tracks.length ? "done" : "idle",
    },
    {
      key: "assess",
      label: "Assess",
      value: `${assessed}/${cas.length} human`,
      phase: cas.length && assessed === cas.length ? "done" : assessed ? "active" : cas.length ? "active" : "idle",
    },
    {
      key: "optimize",
      label: "Optimize",
      value: rec ? `${rec.engine ?? "—"} · rev ${rec.scene_revision}` : "—",
      phase: rec ? "done" : "idle",
    },
    {
      key: "approve",
      label: "Approve",
      value:
        proposal?.status === "pending_approval"
          ? "awaiting command"
          : `${state.approvals.filter((a) => a.decision === "approve").length} approved`,
      phase: proposal?.status === "pending_approval" ? "active" : state.approvals.some((a) => a.decision === "approve") ? "done" : "idle",
    },
    {
      key: "dispatch",
      label: "Dispatch",
      value: `${state.dispatches.length} sent`,
      phase: proposal?.status === "approved" ? "active" : state.dispatches.length ? "done" : "idle",
    },
    {
      key: "ack",
      label: "Acknowledge",
      value: awaitingAck ? `${awaitingAck} awaiting` : `${acked} ack'd`,
      phase: awaitingAck ? "active" : acked ? "done" : "idle",
    },
    {
      key: "adapt",
      label: "Adapt",
      value: `${adapts} replans`,
      phase: adapts ? "done" : "idle",
    },
  ];

  if (compact) {
    return (
      <ol className="flex min-w-0 items-center gap-1 overflow-hidden" aria-label="Coordination loop">
        {steps.map((s, i) => (
          <li
            key={s.key}
            title={`${s.label}: ${s.value}`}
            className={`mono flex shrink-0 items-center gap-1 rounded border px-1.5 py-0.5 text-[10px] uppercase tracking-wider ${
              s.phase === "done"
                ? "border-accent/40 text-accent"
                : s.phase === "active"
                  ? "blink border-warn/70 bg-warn/10 text-warn"
                  : "border-line-strong text-faint"
            }`}
          >
            <span>{s.phase === "done" ? "✓" : i + 1}</span>
            <span>{s.label}</span>
            {s.phase === "active" && <span className="normal-case tracking-normal">· {s.value}</span>}
          </li>
        ))}
      </ol>
    );
  }

  return (
    <ol className="grid grid-cols-7 gap-px overflow-hidden rounded-md border border-line bg-line">
      {steps.map((s, i) => (
        <li
          key={s.key}
          className={`relative flex flex-col gap-0.5 px-3 py-2 ${s.phase === "active" ? "bg-tone-warn" : "bg-panel"}`}
        >
          <div className="flex items-center gap-1.5">
            <span
              className={`mono flex h-4 w-4 items-center justify-center rounded-sm text-[9px] font-bold ${
                s.phase === "done"
                  ? "bg-accent text-on-accent"
                  : s.phase === "active"
                    ? "blink bg-warn text-on-warn"
                    : "border border-line-strong text-faint"
              }`}
            >
              {s.phase === "done" ? "✓" : i + 1}
            </span>
            <span className={`label !text-[10px] ${s.phase === "idle" ? "" : "!text-text"}`}>{s.label}</span>
          </div>
          <span className={`mono truncate text-[10.5px] ${s.phase === "active" ? "text-warn" : "text-muted"}`}>{s.value}</span>
        </li>
      ))}
    </ol>
  );
}
