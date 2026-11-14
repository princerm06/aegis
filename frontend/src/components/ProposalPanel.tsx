"use client";

import { useEffect, useState } from "react";
import { api } from "@/lib/api";
import { SEVERITY_META, humanize, meters, responderName, shortId } from "@/lib/format";
import type { ExplanationFact, SimulationProposal, SimulationState } from "@/lib/types";
import { Badge, Button, PanelHeader } from "./ui";

interface Props {
  state: SimulationState;
  proposal: SimulationProposal | null;
  pending: string | null;
  now: number;
  onDecide: (p: SimulationProposal, decision: "approve" | "reject") => void;
  onDispatch: (p: SimulationProposal, approvalId: string) => void;
  onReplan: () => void;
}

const CATEGORY_TONE: Record<string, "accent" | "warn" | "neutral" | "info"> = {
  procedural: "info",
  estimated: "accent",
  unknown: "warn",
};

export function ProposalPanel({ state, proposal, pending, now, onDecide, onDispatch, onReplan }: Props) {
  const [facts, setFacts] = useState<ExplanationFact[] | null>(null);
  const [showWhy, setShowWhy] = useState(false);

  const pid = proposal?.id;
  const pstatus = proposal?.status;
  useEffect(() => {
    setFacts(null);
    if (!pid) return;
    let cancelled = false;
    api
      .explanation(pid)
      .then((r) => !cancelled && setFacts(r.explanation.facts))
      .catch(() => !cancelled && setFacts([]));
    return () => {
      cancelled = true;
    };
  }, [pid, pstatus]);

  const busy = pending !== null;
  const rec = state.last_recommendation;
  const casualty = (id: string) => state.scenario.casualties.find((c) => c.id === id);

  if (!proposal) {
    return (
      <section className="panel flex flex-col">
        <PanelHeader title="Recommendation" right={<Badge>none pending</Badge>} />
        <div className="space-y-2 p-3 text-xs">
          <p className="text-muted">
            {rec
              ? `Optimizer ran at scene rev ${rec.scene_revision} (${humanize(rec.trigger)}) — no new assignable work.`
              : "No recommendation computed yet."}
          </p>
          {rec?.error && <p className="text-danger">Solver error: {rec.error}</p>}
          {rec && rec.unassigned_casualty_ids.length > 0 && (
            <ul className="space-y-1">
              {rec.unassigned_casualty_ids.map((id) => (
                <li key={id} className="mono text-[11px] text-muted">
                  <span className="text-text">{shortId(id)}</span> · {humanize(rec.reasons[id]?.[0] ?? "unassigned")}
                </li>
              ))}
            </ul>
          )}
          <Button onClick={onReplan} disabled={busy} className="w-full">
            {pending === "Replan" ? "Computing…" : "Recompute recommendations"}
          </Button>
        </div>
      </section>
    );
  }

  const approval = proposal.approval_id ? state.approvals.find((a) => a.id === proposal.approval_id) : undefined;
  // Once approved, the binding deadline is the approval's own expiry.
  const deadline = approval?.status === "active" ? approval.expires_at : proposal.expires_at;
  const expiresIn = Math.round((new Date(deadline).getTime() - now) / 1000);
  const isPending = proposal.status === "pending_approval";
  const isApproved = proposal.status === "approved" && approval?.status === "active";
  const status = proposal.recommendation?.status;
  const ov = proposal.objective_values;

  return (
    <section className={`panel flex flex-col ${isPending ? "ring-1 ring-warn/40" : ""}`}>
      <PanelHeader
        title="Recommendation"
        sub={<span className="mono">rev {proposal.revision}</span>}
        right={
          <Badge tone={isPending ? "warn" : isApproved ? "ok" : "neutral"}>
            {isPending ? "advisory" : humanize(proposal.status)}
          </Badge>
        }
      />

      <div className="space-y-3 p-3">
        <div className="mono grid grid-cols-3 gap-2 text-[10.5px]">
          <Stat k="engine" v={proposal.engine} />
          <Stat k="solve" v={`${proposal.runtime_ms < 1000 ? proposal.runtime_ms.toFixed(0) + " ms" : (proposal.runtime_ms / 1000).toFixed(1) + " s"}`} />
          <Stat
            k={approval?.status === "active" ? "approval exp" : "expires"}
            v={expiresIn > 0 ? `${Math.floor(expiresIn / 60)}:${String(expiresIn % 60).padStart(2, "0")}` : "expired"}
            warn={expiresIn < 30}
          />
          <Stat k="result" v={status ?? "—"} warn={status !== "feasible"} />
          <Stat k="new tasks" v={String(proposal.assignments.length)} />
          <Stat k="travel" v={ov.new_travel_distance_m != null ? meters(ov.new_travel_distance_m) : "—"} />
        </div>

        <ul className="space-y-1.5">
          {proposal.assignments.map((a) => {
            const c = casualty(a.casualty_id);
            const sev = c?.medical_assessment?.severity ?? "unassessed";
            const m = SEVERITY_META[sev];
            return (
              <li key={a.casualty_id} className="flex items-center gap-2 rounded border border-accent/30 bg-accent-dim/20 px-2 py-1.5">
                <span className="text-[13px] font-semibold">{responderName(state, a.responder_id)}</span>
                <span className="text-accent">→</span>
                <span className="mono text-[13px] font-semibold">{shortId(a.casualty_id)}</span>
                <span className="mono text-[9.5px] font-bold uppercase" style={{ color: m.color }}>
                  {m.short === "—" ? "UNASSESSED" : m.label}
                </span>
                <span className="mono ml-auto text-[11px] text-muted">{a.route ? meters(a.route.distance_m) : "no route"}</span>
              </li>
            );
          })}
          {proposal.unassigned_casualty_ids.map((id) => (
            <li key={id} className="flex items-center gap-2 rounded border border-dashed border-line-strong px-2 py-1 text-[11px] text-muted">
              <span className="mono font-semibold text-text">{shortId(id)}</span>
              <span>unassigned · {humanize(proposal.reasons[id]?.[0] ?? "")}</span>
            </li>
          ))}
          {proposal.preserved_assignments.length > 0 && (
            <li className="text-[10.5px] text-faint">
              {proposal.preserved_assignments.length} active task(s) preserved — not re-dispatched.
            </li>
          )}
        </ul>

        <div>
          <button onClick={() => setShowWhy((v) => !v)} className="label flex items-center gap-1 hover:!text-text">
            {showWhy ? "▾" : "▸"} Why this plan {facts ? `· ${facts.length} grounded facts` : ""}
          </button>
          {showWhy && (
            <ul className="scrollbar-thin mt-2 max-h-56 space-y-1.5 overflow-y-auto pr-1">
              {facts === null && <li className="text-[11px] text-muted">Loading…</li>}
              {facts?.map((f) => (
                <li key={f.id} className="flex gap-2 text-[11px] leading-snug">
                  <Badge tone={CATEGORY_TONE[f.category] ?? "neutral"} className="h-fit shrink-0">
                    {f.category}
                  </Badge>
                  <span className="text-muted">{f.text}</span>
                </li>
              ))}
            </ul>
          )}
        </div>

        {isPending && (
          <div className="space-y-1.5 border-t border-line pt-3">
            <div className="flex gap-2">
              <Button variant="danger" disabled={busy} onClick={() => onDecide(proposal, "reject")}>
                Reject
              </Button>
              <Button
                variant="warn"
                size="lg"
                className="flex-1"
                disabled={busy || expiresIn <= 0 || proposal.assignments.length === 0}
                onClick={() => onDecide(proposal, "approve")}
              >
                {pending === "Approve" ? "Recording…" : `Approve revision ${proposal.revision}`}
              </Button>
            </div>
            <p className="text-[10px] leading-snug text-faint">
              Approval applies to this exact revision only. Dispatch is a separate step.
            </p>
          </div>
        )}

        {proposal.status === "approved" && (
          <div className="space-y-1.5 border-t border-line pt-3">
            {isApproved ? (
              <>
                <p className="text-[11px] text-ok">
                  ✓ Approved by <span className="mono">{approval!.approved_by}</span>{" "}
                  <span className="text-faint">(local operator, not authenticated)</span>
                </p>
                <Button variant="ok" size="lg" className="w-full" disabled={busy} onClick={() => onDispatch(proposal, approval!.id)}>
                  {pending === "Dispatch" ? "Dispatching…" : `Dispatch ${proposal.assignments.length} task(s) to responders`}
                </Button>
              </>
            ) : (
              <p className="text-[11px] text-danger">
                Approval {approval?.status ?? "missing"}
                {approval?.invalidated_reason ? ` — ${humanize(approval.invalidated_reason)}` : ""}. Fresh approval required.
              </p>
            )}
          </div>
        )}

        <Button size="sm" onClick={onReplan} disabled={busy} className="w-full">
          {pending === "Replan" ? "Computing…" : "Recompute"}
        </Button>
      </div>
    </section>
  );
}

function Stat({ k, v, warn }: { k: string; v: string; warn?: boolean }) {
  return (
    <div className="rounded border border-line bg-panel-2 px-2 py-1">
      <div className="text-[9px] uppercase tracking-widest text-faint">{k}</div>
      <div className={`truncate ${warn ? "text-warn" : "text-text"}`}>{v}</div>
    </div>
  );
}
