"use client";

import { useState } from "react";
import { clock, meters, responderName, shortId } from "@/lib/format";
import type { AssignmentStatus, SimulatedAssignment, SimulationState } from "@/lib/types";
import { Badge, Button, Empty, PanelHeader } from "./ui";

const STEPS: AssignmentStatus[] = ["dispatched", "delivered", "acknowledged", "completed"];
const STEP_LABEL: Record<AssignmentStatus, string> = {
  dispatched: "Dispatched",
  delivered: "Device rcvd",
  acknowledged: "Ack'd",
  completed: "Complete",
};

interface Props {
  state: SimulationState;
  busy: boolean;
  onSimulate: (a: SimulatedAssignment, kind: "delivered" | "acknowledged" | "completed") => void;
}

export function TasksPanel({ state, busy, onSimulate }: Props) {
  const [rehearsal, setRehearsal] = useState(false);
  const active = state.assignments.filter((a) => a.status !== "completed");
  const done = state.assignments.filter((a) => a.status === "completed");

  return (
    <section className="panel flex min-h-0 flex-col">
      <PanelHeader
        title="Active tasks"
        sub={`${active.length} active · ${done.length} complete`}
        right={
          <label className="mono flex cursor-pointer items-center gap-1.5 text-[10px] uppercase tracking-wider text-muted">
            <input type="checkbox" checked={rehearsal} onChange={(e) => setRehearsal(e.target.checked)} className="accent-[var(--warn)]" />
            rehearsal
          </label>
        }
      />
      <div className="scrollbar-thin min-h-0 flex-1 space-y-2 overflow-y-auto p-2">
        {state.assignments.length === 0 && <Empty>No dispatched tasks. Approve and dispatch a recommendation.</Empty>}
        {[...active, ...done].map((a) => (
          <TaskCard key={a.id} a={a} state={state} rehearsal={rehearsal} busy={busy} onSimulate={onSimulate} />
        ))}
      </div>
      {rehearsal && (
        <p className="sim-stripe border-t border-warn/30 px-3 py-1.5 text-[10px] text-warn">
          SIMULATED device events — stand-in for the Arduino button. Not physical delivery.
        </p>
      )}
    </section>
  );
}

function TaskCard({
  a,
  state,
  rehearsal,
  busy,
  onSimulate,
}: {
  a: SimulatedAssignment;
  state: SimulationState;
  rehearsal: boolean;
  busy: boolean;
  onSimulate: Props["onSimulate"];
}) {
  const idx = STEPS.indexOf(a.status);
  const stamp: Record<AssignmentStatus, string | null | undefined> = {
    dispatched: a.created_at,
    delivered: a.delivered_at,
    acknowledged: a.acknowledged_at,
    completed: a.completed_at,
  };
  const awaitingAck = a.status === "dispatched" || a.status === "delivered";
  const next = STEPS[idx + 1] as "delivered" | "acknowledged" | "completed" | undefined;
  const invalidated = a.status !== "completed" && a.route_status === "invalidated";
  const stale = !invalidated && a.status !== "completed" && !!a.route && a.route.map_revision !== state.scenario.scene_map?.revision;

  return (
    <article className={`rounded border bg-panel-2 p-2 ${a.status === "completed" ? "border-line opacity-60" : invalidated ? "border-danger/60" : "border-line-strong"}`}>
      <div className="flex items-center gap-2">
        <span className="text-[13px] font-semibold">{responderName(state, a.responder_id)}</span>
        <span className="text-muted">→</span>
        <span className="mono text-[13px] font-semibold">{shortId(a.casualty_id)}</span>
        {a.route && <span className="mono text-[10.5px] text-muted">{meters(a.route.distance_m)}</span>}
        <span className="ml-auto flex items-center gap-1">
          {invalidated && <Badge tone="danger">route invalidated</Badge>}
          {!invalidated && stale && <Badge>historical route</Badge>}
          {awaitingAck ? (
            <Badge tone="warn" className="blink">awaiting ack</Badge>
          ) : a.status === "acknowledged" ? (
            <Badge tone="ok">acknowledged</Badge>
          ) : (
            <Badge>complete</Badge>
          )}
        </span>
      </div>
      {invalidated && (
        <p className="mt-1 text-[10.5px] leading-snug text-danger">
          Blocked at {(a.invalidated_blocks ?? []).map(([c, r]) => `(${c}, ${r})`).join(", ") || "unknown cell"} —
          historical route kept; replan and approve a revised route before redispatch.
        </p>
      )}

      <ol className="mt-2 grid grid-cols-4 gap-1">
        {STEPS.map((s, i) => {
          const reached = i <= idx;
          const ts = stamp[s];
          return (
            <li key={s} className="min-w-0">
              <div className={`h-1 rounded-full ${reached ? (s === "acknowledged" || s === "completed" ? "bg-ok" : "bg-warn") : "bg-line-strong"}`} />
              <div className={`mono mt-1 truncate text-[9.5px] uppercase ${reached ? "text-text" : "text-faint"}`}>{STEP_LABEL[s]}</div>
              <div className="mono text-[9.5px] text-faint">{ts ? clock(ts) : "—"}</div>
            </li>
          );
        })}
      </ol>

      <div className="mono mt-1.5 flex gap-2 text-[9.5px] text-faint">
        <span>device {a.device_id}</span>
        <span>cmd {a.command_id}</span>
      </div>

      {rehearsal && next && (
        <Button size="sm" variant="ghost" disabled={busy} onClick={() => onSimulate(a, next)} className="mt-2 w-full !border-warn/50 !text-warn">
          SIMULATE: {next}
        </Button>
      )}
    </article>
  );
}
