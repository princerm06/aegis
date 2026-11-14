"use client";

import { humanize, meters, responderName, shortId } from "@/lib/format";
import { milestoneSource, type ProposalDiff } from "@/lib/insights";
import type { DeviceLink } from "@/lib/preview";
import type { SimulationProposal, SimulationState } from "@/lib/types";
import { Badge, Button } from "./ui";

interface Props {
  state: SimulationState;
  proposal: SimulationProposal | null;
  diff: ProposalDiff | null;
  pending: string | null;
  now: number;
  readOnly: boolean;
  device: (responderId: string) => DeviceLink;
  onDecide: (p: SimulationProposal, d: "approve" | "reject") => void;
  onDispatch: (p: SimulationProposal, approvalId: string) => void;
  onReplan: () => void;
  onOpenDetails: () => void;
}

const DIFF_COLOR = { new: "text-ok", dropped: "text-danger", reassigned: "text-warn", rerouted: "text-accent" } as const;

/** Human approval gate + task acknowledgements, always visible. */
export function CommandDock({ state, proposal, diff, pending, now, readOnly, device, onDecide, onDispatch, onReplan, onOpenDetails }: Props) {
  const busy = pending !== null || readOnly;
  const approval = proposal?.approval_id ? state.approvals.find((a) => a.id === proposal.approval_id) : undefined;
  const isPending = proposal?.status === "pending_approval";
  const isApproved = proposal?.status === "approved" && approval?.status === "active";
  const deadline = isApproved ? approval!.expires_at : proposal?.expires_at;
  const secs = deadline ? Math.round((new Date(deadline).getTime() - now) / 1000) : 0;
  const tasks = state.assignments.filter((a) => a.status !== "completed");

  return (
    <section className={`panel flex min-h-0 flex-col ${isPending ? "ring-1 ring-warn/60" : ""}`}>
      <header className="flex items-center gap-2 border-b border-line px-2.5 py-1.5">
        <h2 className="label !text-text">Recommendation</h2>
        {proposal ? (
          <Badge tone={isPending ? "warn" : isApproved ? "ok" : "neutral"}>{isPending ? `advisory · rev ${proposal.revision}` : humanize(proposal.status)}</Badge>
        ) : (
          <Badge>none pending</Badge>
        )}
        {proposal && (
          <span className={`mono ml-auto text-[10px] ${secs < 30 ? "text-warn" : "text-faint"}`}>
            {secs > 0 ? `${isApproved ? "approval" : "expires"} ${Math.floor(secs / 60)}:${String(secs % 60).padStart(2, "0")}` : "expired"}
          </span>
        )}
        <button onClick={onOpenDetails} className={`text-[10.5px] text-accent hover:underline ${proposal ? "" : "ml-auto"}`}>
          details ›
        </button>
      </header>

      <div className="scrollbar-thin min-h-0 flex-1 space-y-1.5 overflow-y-auto px-2.5 py-1.5">
        {proposal ? (
          <div className="flex flex-wrap gap-x-3 gap-y-0.5 text-[12px]">
            {proposal.assignments.map((a) => (
              <span key={a.casualty_id} className="whitespace-nowrap">
                <b>{responderName(state, a.responder_id)}</b> <span className="text-accent">→</span> <span className="mono">{shortId(a.casualty_id)}</span>{" "}
                <span className="mono text-[10px] text-muted">{a.route ? meters(a.route.distance_m) : ""}</span>
              </span>
            ))}
            {proposal.unassigned_casualty_ids.length > 0 && (
              <span className="text-[10.5px] text-faint">{proposal.unassigned_casualty_ids.map(shortId).join(", ")} unassigned</span>
            )}
          </div>
        ) : (
          <p className="text-[11px] text-muted">{state.last_recommendation ? "No new assignable work." : "Waiting for optimizer."}</p>
        )}

        {diff && diff.items.length > 0 && (
          <div className="rounded border border-accent/30 bg-accent-dim/15 px-2 py-1">
            <p className="label !text-[9px] !text-accent">
              Changed since rev {diff.previous.revision}
              {diff.trigger ? ` · ${humanize(diff.trigger.split(":")[0])}` : ""}
            </p>
            <ul>
              {diff.items.map((d) => (
                <li key={d.casualtyId + d.kind} className={`text-[10.5px] ${DIFF_COLOR[d.kind]}`}>
                  {d.text}
                </li>
              ))}
            </ul>
          </div>
        )}

        {tasks.length > 0 && (
          <ul className="space-y-0.5 border-t border-line pt-1.5">
            {tasks.map((a) => {
              const ack = milestoneSource(a, "acknowledged");
              const dl = device(a.responder_id);
              return (
                <li key={a.id} className="flex items-center gap-1.5 text-[10.5px]">
                  <span className="font-semibold">{responderName(state, a.responder_id)}</span>→<span className="mono">{shortId(a.casualty_id)}</span>
                  <Badge
                    tone={a.status === "acknowledged" ? "ok" : a.status === "delivered" ? "info" : "warn"}
                    className={a.status === "dispatched" || a.status === "delivered" ? "blink" : ""}
                  >
                    {a.status === "acknowledged" ? "ack'd" : a.status === "delivered" ? "awaiting ack" : "sent"}
                  </Badge>
                  {ack && (
                    <span className={`mono text-[9.5px] ${ack === "device_bridge" ? "text-ok" : "text-warn"}`} title="Provisional: inferred from the device event id">
                      {ack === "device_bridge" ? "via device ⌁" : "simulated"}
                    </span>
                  )}
                  {dl.kind !== "unknown" && (
                    <span className={`mono ml-auto text-[9px] ${dl.kind === "online" ? "text-ok" : "text-danger"}`}>
                      ● {dl.kind} <span className="rounded bg-warn/20 px-0.5 text-warn">PREVIEW</span>
                    </span>
                  )}
                </li>
              );
            })}
          </ul>
        )}
      </div>

      <footer className="flex items-center gap-1.5 border-t border-line px-2.5 py-1.5">
        {isPending && (
          <>
            <Button variant="danger" size="sm" disabled={busy} onClick={() => onDecide(proposal!, "reject")}>
              Reject
            </Button>
            <Button
              variant="warn"
              className="flex-1"
              disabled={busy || secs <= 0 || proposal!.assignments.length === 0}
              onClick={() => onDecide(proposal!, "approve")}
              title="Shortcut: A"
            >
              {pending === "Approve" ? "Recording…" : `Approve rev ${proposal!.revision}`}
            </Button>
          </>
        )}
        {isApproved && (
          <Button variant="ok" className="flex-1" disabled={busy} onClick={() => onDispatch(proposal!, approval!.id)} title="Shortcut: D">
            {pending === "Dispatch" ? "Dispatching…" : `Dispatch ${proposal!.assignments.length} task(s)`}
          </Button>
        )}
        {proposal?.status === "approved" && !isApproved && <span className="flex-1 text-[11px] text-danger">approval {approval?.status ?? "missing"} — re-approve</span>}
        {!isPending && !proposal?.status?.startsWith("approved") && <span className="flex-1 text-[10.5px] text-faint">Approve → Dispatch → device ack</span>}
        <Button size="sm" disabled={busy} onClick={onReplan} title="Recompute recommendations">
          ↻
        </Button>
      </footer>
    </section>
  );
}
